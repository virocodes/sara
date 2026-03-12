#!/usr/bin/env python3
"""Streaming terminal client for the agent server."""

import json
import re
import sys
import urllib.request

URL = f"http://localhost:{sys.argv[1] if len(sys.argv) > 1 else 8080}"

DIM = "\033[2m"
RESET = "\033[0m"
BOLD = "\033[1m"
CYAN = "\033[36m"
YELLOW = "\033[33m"
GREEN = "\033[32m"
MAGENTA = "\033[35m"
WHITE = "\033[37m"


def render_line(line, in_code_block):
    """Render a single line of markdown with ANSI codes."""
    # code block fences
    if line.strip().startswith("```"):
        return f"{DIM}{'─' * 40}{RESET}", not in_code_block

    if in_code_block:
        return f"{DIM}  {line}{RESET}", True

    # headers
    m = re.match(r"^(#{1,3})\s+(.*)", line)
    if m:
        return f"{BOLD}{MAGENTA}{m.group(2)}{RESET}", False

    # inline formatting
    line = re.sub(r"\*\*(.+?)\*\*", rf"{BOLD}\1{RESET}", line)
    line = re.sub(r"`([^`]+)`", rf"{CYAN}\1{RESET}", line)
    line = re.sub(r"^(\s*)[-*] ", rf"\1{GREEN}•{RESET} ", line)

    return line, False


def truncate(text, max_lines=8):
    lines = text.split("\n")
    if len(lines) <= max_lines:
        return text
    return "\n".join(lines[:max_lines]) + f"\n  {DIM}... ({len(lines) - max_lines} more lines){RESET}"


conversation_id = None


def stream_request(message):
    global conversation_id
    body = {"message": message}
    if conversation_id:
        body["conversation_id"] = conversation_id

    req = urllib.request.Request(
        URL,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    resp = urllib.request.urlopen(req, timeout=300)

    while True:
        line = resp.readline()
        if not line:
            break
        line = line.decode().strip()
        if line.startswith("data: "):
            yield json.loads(line[6:])

    resp.close()


print(f"{BOLD}atom{RESET} {DIM}(ctrl+c to quit){RESET}\n")

while True:
    try:
        message = input(f"{GREEN}>{RESET} ").strip()
    except (KeyboardInterrupt, EOFError):
        print()
        break

    if not message:
        continue

    print()

    try:
        line_buf = ""
        in_code_block = False
        streaming_text = False

        for event in stream_request(message):
            t = event["type"]

            if t == "conversation":
                conversation_id = event["data"]

            elif t == "text":
                streaming_text = True
                chunk = event["data"]

                # split on newlines, render completed lines immediately
                parts = chunk.split("\n")
                for i, part in enumerate(parts):
                    if i > 0:
                        # newline hit — render the completed line
                        rendered, in_code_block = render_line(line_buf, in_code_block)
                        sys.stdout.write(rendered + "\n")
                        sys.stdout.flush()
                        line_buf = ""
                    line_buf += part

            elif t == "tool_start":
                if streaming_text and line_buf:
                    rendered, in_code_block = render_line(line_buf, in_code_block)
                    sys.stdout.write(rendered + "\n")
                    line_buf = ""
                streaming_text = False
                data = event["data"]
                tool_name = data["tool"]
                tool_input = data["input"]
                # format the display based on tool
                if tool_name == "bash":
                    display = tool_input.get("command", "")
                else:
                    display = json.dumps(tool_input, separators=(",", ":"))
                print(f"  {YELLOW}{tool_name}{RESET} {WHITE}{display}{RESET}")

            elif t == "tool_output":
                data = event["data"]
                for line in truncate(data["output"]).split("\n"):
                    print(f"    {DIM}{line}{RESET}")
                print()

            elif t == "compact":
                print(f"  {DIM}[context compacted]{RESET}\n")

            elif t == "error":
                print(f"{YELLOW}error: {event['data']}{RESET}")

            elif t == "done":
                if line_buf:
                    rendered, in_code_block = render_line(line_buf, in_code_block)
                    sys.stdout.write(rendered + "\n")
                    line_buf = ""

        print()

    except KeyboardInterrupt:
        print(f"\n{DIM}interrupted{RESET}\n")
    except Exception as e:
        print(f"{YELLOW}error: {e}{RESET}\n")
