#!/usr/bin/env python3
"""Minimal streaming agent with pluggable providers, tools, and MCP support."""

import atexit
import importlib.util
import json
import os
import platform
import subprocess
import time
import urllib.error
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path


def load_env():
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())


load_env()
PROJECT_DIR = Path(__file__).parent

PROVIDER_NAME = os.environ.get("PROVIDER", "anthropic")
API_KEY = os.environ.get("API_KEY") or os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("OPENAI_API_KEY", "")
MODEL = os.environ.get("MODEL", "")
BASE_URL = os.environ.get("BASE_URL", "")
MAX_TOKENS = int(os.environ.get("MAX_TOKENS", "4096"))
MAX_CONTEXT_CHARS = int(os.environ.get("MAX_CONTEXT_CHARS", "200000"))


# --- MCP Client (stdio, JSON-RPC 2.0) ---

class MCPClient:
    def __init__(self, name, command, args=None, env=None):
        self.name = name
        self._id = 0
        merged_env = {**os.environ, **(env or {})}
        self.proc = subprocess.Popen(
            [command] + (args or []),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, env=merged_env,
        )
        self._initialize()

    def _request(self, method, params=None):
        self._id += 1
        msg = {"jsonrpc": "2.0", "id": self._id, "method": method}
        if params is not None:
            msg["params"] = params
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()
        while True:
            line = self.proc.stdout.readline()
            if not line:
                raise RuntimeError(f"MCP server '{self.name}' closed unexpectedly")
            line = line.strip()
            if not line:
                continue
            try:
                resp = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "id" in resp and resp["id"] == self._id:
                if "error" in resp:
                    raise RuntimeError(f"MCP error: {resp['error'].get('message', resp['error'])}")
                return resp.get("result", {})

    def _notify(self, method, params=None):
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()

    def _initialize(self):
        self._request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "atom", "version": "1.0"},
        })
        self._notify("notifications/initialized")

    def list_tools(self):
        return self._request("tools/list").get("tools", [])

    def call_tool(self, tool_name, arguments):
        result = self._request("tools/call", {"name": tool_name, "arguments": arguments})
        content = result.get("content", [])
        texts = [c.get("text", "") for c in content if c.get("type") == "text"]
        return "\n".join(texts) or json.dumps(result)

    def close(self):
        try:
            self.proc.stdin.close()
            self.proc.terminate()
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.kill()


# --- Loaders ---

def load_provider(name):
    providers_dir = PROJECT_DIR / "providers"
    path = providers_dir / f"{name}.py"
    if not path.exists():
        available = [p.stem for p in providers_dir.glob("*.py") if not p.name.startswith("_")] if providers_dir.is_dir() else []
        raise RuntimeError(f"Provider '{name}' not found. Available: {', '.join(available) or 'none'}")
    spec = importlib.util.spec_from_file_location(f"providers.{name}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_tools():
    tools = {}
    tools_dir = PROJECT_DIR / "tools"
    if not tools_dir.is_dir():
        return tools
    for path in sorted(tools_dir.glob("*.py")):
        if path.name.startswith("_"):
            continue
        spec = importlib.util.spec_from_file_location(path.stem, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        tools[mod.name] = {
            "spec": {"name": mod.name, "description": mod.description, "input_schema": mod.input_schema},
            "run": mod.run,
        }
    return tools


def load_mcp_servers():
    config_path = PROJECT_DIR / ".mcp.json"
    if not config_path.exists():
        return {}
    config = json.loads(config_path.read_text())
    servers = {}
    for name, srv in config.get("mcpServers", {}).items():
        if srv.get("disabled"):
            continue
        try:
            client = MCPClient(name, srv["command"], srv.get("args", []), srv.get("env"))
            servers[name] = client
        except Exception as e:
            print(f"  Warning: MCP server '{name}' failed to start: {e}")
    return servers


def load_mcp_tools(servers):
    tools = {}
    for server_name, client in servers.items():
        try:
            for tool in client.list_tools():
                tool_name = tool["name"]
                tools[tool_name] = {
                    "spec": {
                        "name": tool_name,
                        "description": tool.get("description", ""),
                        "input_schema": tool.get("inputSchema", {"type": "object", "properties": {}}),
                    },
                    "run": lambda inp, c=client, n=tool_name: c.call_tool(n, inp),
                }
        except Exception as e:
            print(f"  Warning: failed to list tools from '{server_name}': {e}")
    return tools


def build_system_prompt():
    parts = []
    for filename in ("system.md", "AGENTS.md", "CLAUDE.md"):
        path = PROJECT_DIR / filename
        if path.exists():
            content = path.read_text().strip()
            if content:
                parts.append(content)
    parts.append(f"Current date: {datetime.now().strftime('%Y-%m-%d')}\nWorking directory: {os.getcwd()}\nPlatform: {platform.system()}")
    return "\n\n---\n\n".join(parts)


# --- Startup ---

PROVIDER = load_provider(PROVIDER_NAME)
TOOLS = load_tools()
MCP_SERVERS = load_mcp_servers()
MCP_TOOLS = load_mcp_tools(MCP_SERVERS)
ALL_TOOLS = {**MCP_TOOLS, **TOOLS}

atexit.register(lambda: [c.close() for c in MCP_SERVERS.values()])

conversations = {}


# --- Core ---

def stream_with_retry(messages, tool_specs=None, system=None):
    """Call provider.stream() with retry on transient errors. Never retries mid-stream."""
    delays = [1, 2, 4]
    for attempt in range(len(delays) + 1):
        started = False
        try:
            for event in PROVIDER.stream(
                messages,
                tools=tool_specs,
                system=system or build_system_prompt(),
                model=MODEL or None,
                api_key=API_KEY,
                base_url=BASE_URL or None,
                max_tokens=MAX_TOKENS,
            ):
                started = True
                yield event
            return
        except urllib.error.HTTPError as e:
            if started or (e.code < 500 and e.code != 429):
                raise
            if attempt >= len(delays):
                raise
            time.sleep(delays[attempt])
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            if started or attempt >= len(delays):
                raise
            time.sleep(delays[attempt])


def execute_tools(tool_uses, send, tool_dict):
    """Execute tool calls. Parallel if multiple, inline if single."""
    for tool in tool_uses:
        send("tool_start", {"tool": tool["name"], "input": tool["input"]})

    def run_one(tool):
        handler = tool_dict.get(tool["name"])
        if not handler:
            return f"Unknown tool: {tool['name']}"
        try:
            return handler["run"](tool["input"])
        except Exception as e:
            return f"Error running {tool['name']}: {e}"

    if len(tool_uses) == 1:
        outputs = [run_one(tool_uses[0])]
    else:
        with ThreadPoolExecutor(max_workers=len(tool_uses)) as pool:
            outputs = list(pool.map(run_one, tool_uses))

    results = []
    for tool, output in zip(tool_uses, outputs):
        send("tool_output", {"tool": tool["name"], "output": output})
        results.append({"type": "tool_result", "tool_use_id": tool["id"], "content": output})
    return results


def compact_messages(messages, send):
    """Summarize old messages when context exceeds MAX_CONTEXT_CHARS."""
    if len(json.dumps(messages)) <= MAX_CONTEXT_CHARS or len(messages) <= 6:
        return messages

    old = messages[:-4]
    recent = messages[-4:]

    # serialize old conversation for summarization, truncate if huge
    old_text = json.dumps(old)
    if len(old_text) > 50000:
        old_text = old_text[:50000] + "\n...(truncated)"

    summary_messages = [
        {"role": "user", "content": f"Summarize this conversation concisely. Include key decisions, code changes, and important context.\n\n{old_text}"},
    ]

    summary_text = ""
    try:
        for event_type, data in stream_with_retry(
            summary_messages,
            tool_specs=None,
            system="You are a helpful assistant. Summarize the conversation concisely.",
        ):
            if event_type == "text":
                summary_text += data
            elif event_type == "done":
                break
    except Exception:
        return messages

    compacted = [{"role": "user", "content": f"[Previous conversation summary]\n{summary_text}"}]
    # ensure valid role alternation at boundary
    if recent[0]["role"] != "assistant":
        compacted.append({"role": "assistant", "content": "Understood, I have the context from our previous conversation."})
    compacted.extend(recent)

    send("compact", "")
    return compacted


def agent_loop(messages, send, tools=None, system=None):
    """Main agent loop. Streams LLM responses, executes tools, loops until done."""
    tool_dict = tools if tools is not None else ALL_TOOLS
    tool_specs = [t["spec"] for t in tool_dict.values()] if tool_dict else None

    while True:
        messages[:] = compact_messages(messages, send)

        text_buf = ""
        tool_uses = []

        for event_type, data in stream_with_retry(messages, tool_specs=tool_specs, system=system):
            if event_type == "text":
                text_buf += data
                send("text", data)
            elif event_type == "tool_use":
                tool_uses.append(data)
            elif event_type == "done":
                break

        # build assistant message in Anthropic format (canonical storage)
        assistant_content = []
        if text_buf:
            assistant_content.append({"type": "text", "text": text_buf})
        for tool in tool_uses:
            assistant_content.append({"type": "tool_use", "id": tool["id"], "name": tool["name"], "input": tool["input"]})
        messages.append({"role": "assistant", "content": assistant_content})

        if not tool_uses:
            send("done", "")
            return

        tool_results = execute_tools(tool_uses, send, tool_dict)
        messages.append({"role": "user", "content": tool_results})


def run_agent(message, tools=None, system=None):
    """Run a complete agent loop, return text result. For subagent use."""
    messages = [{"role": "user", "content": message}]
    result_text = ""

    def send(event_type, data):
        nonlocal result_text
        if event_type == "text":
            result_text += data

    agent_loop(messages, send, tools=tools, system=system)
    return result_text


# --- HTTP server ---

class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))
        message = body.get("message", "")
        conversation_id = body.get("conversation_id")

        if conversation_id and conversation_id in conversations:
            messages = conversations[conversation_id]
        else:
            conversation_id = str(uuid.uuid4())
            messages = []
            conversations[conversation_id] = messages

        messages.append({"role": "user", "content": message})

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        def send(event_type, data):
            payload = json.dumps({"type": event_type, "data": data})
            self.wfile.write(f"data: {payload}\n\n".encode())
            self.wfile.flush()

        send("conversation", conversation_id)

        try:
            agent_loop(messages, send)
        except Exception as e:
            send("error", str(e))

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    local_names = list(TOOLS.keys())
    mcp_names = list(MCP_TOOLS.keys())

    print(f"Agent listening on http://localhost:{port}")
    print(f"Provider: {PROVIDER_NAME}" + (f" ({MODEL})" if MODEL else ""))
    if local_names:
        print(f"Tools: {', '.join(local_names)}")
    if mcp_names:
        print(f"MCP tools: {', '.join(mcp_names)}")
    if not local_names and not mcp_names:
        print("Tools: (none)")
    HTTPServer(("", port), Handler).serve_forever()
