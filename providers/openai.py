"""OpenAI-compatible Chat Completions provider.

Works with OpenAI, OpenRouter, Groq, Ollama, Together, and any OpenAI-compatible API.
"""

import json
import urllib.request

name = "openai"


def _convert_messages(messages):
    """Translate Anthropic-format messages to OpenAI format."""
    out = []
    for msg in messages:
        role = msg["role"]
        content = msg["content"]

        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue

        if isinstance(content, list):
            # tool results
            if content and isinstance(content[0], dict) and content[0].get("type") == "tool_result":
                for block in content:
                    c = block.get("content", "")
                    out.append({
                        "role": "tool",
                        "tool_call_id": block["tool_use_id"],
                        "content": c if isinstance(c, str) else json.dumps(c),
                    })
                continue

            # assistant message with text + tool_use blocks
            if role == "assistant":
                text_parts = []
                tool_calls = []
                for block in content:
                    if block.get("type") == "text":
                        text_parts.append(block["text"])
                    elif block.get("type") == "tool_use":
                        tool_calls.append({
                            "id": block["id"],
                            "type": "function",
                            "function": {"name": block["name"], "arguments": json.dumps(block["input"])},
                        })
                entry = {"role": "assistant", "content": "\n".join(text_parts) or None}
                if tool_calls:
                    entry["tool_calls"] = tool_calls
                out.append(entry)
                continue

            # user message with content array — flatten to string
            texts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    texts.append(block["text"])
                elif isinstance(block, str):
                    texts.append(block)
            out.append({"role": role, "content": "\n".join(texts)})
            continue

        out.append({"role": role, "content": str(content)})
    return out


def _convert_tools(tools):
    """Translate Anthropic tool specs to OpenAI function format."""
    if not tools:
        return None
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("input_schema", {"type": "object", "properties": {}}),
            },
        }
        for t in tools
    ]


def stream(messages, tools=None, system=None, model=None, api_key=None, base_url=None, max_tokens=4096):
    """Yield normalized events from an OpenAI-compatible streaming API.

    Yields:
        ("text", "chunk")
        ("tool_use", {"id": ..., "name": ..., "input": {...}})
        ("done", "end_turn" | "tool_use")
    """
    url = (base_url or "https://api.openai.com/v1").rstrip("/") + "/chat/completions"

    oai_messages = _convert_messages(messages)
    if system:
        oai_messages.insert(0, {"role": "system", "content": system})

    body = {
        "model": model or "gpt-4o",
        "max_tokens": max_tokens,
        "messages": oai_messages,
        "stream": True,
    }
    oai_tools = _convert_tools(tools)
    if oai_tools:
        body["tools"] = oai_tools

    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key or ''}",
        },
    )
    resp = urllib.request.urlopen(req)

    tool_calls = {}  # index -> {"id": ..., "name": ..., "arguments": ""}
    finish_reason = None

    try:
        while True:
            line = resp.readline()
            if not line:
                break
            line = line.decode().strip()
            if not line.startswith("data: ") or line == "data: [DONE]":
                continue

            try:
                event = json.loads(line[6:])
            except json.JSONDecodeError:
                continue

            choice = event.get("choices", [{}])[0]
            delta = choice.get("delta", {})

            if choice.get("finish_reason"):
                finish_reason = choice["finish_reason"]

            if delta.get("content"):
                yield ("text", delta["content"])

            if delta.get("tool_calls"):
                for tc in delta["tool_calls"]:
                    idx = tc["index"]
                    if idx not in tool_calls:
                        tool_calls[idx] = {"id": tc.get("id", ""), "name": tc.get("function", {}).get("name", ""), "arguments": ""}
                    else:
                        if tc.get("id"):
                            tool_calls[idx]["id"] = tc["id"]
                        if tc.get("function", {}).get("name"):
                            tool_calls[idx]["name"] = tc["function"]["name"]
                    tool_calls[idx]["arguments"] += tc.get("function", {}).get("arguments", "")

        for idx in sorted(tool_calls):
            tc = tool_calls[idx]
            inp = json.loads(tc["arguments"]) if tc["arguments"] else {}
            yield ("tool_use", {"id": tc["id"], "name": tc["name"], "input": inp})

        yield ("done", "tool_use" if finish_reason == "tool_calls" else "end_turn")
    finally:
        resp.close()
