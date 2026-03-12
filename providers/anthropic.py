"""Anthropic Messages API provider."""

import json
import urllib.request

name = "anthropic"


def stream(messages, tools=None, system=None, model=None, api_key=None, base_url=None, max_tokens=4096):
    """Yield normalized events from Anthropic's streaming API.

    Yields:
        ("text", "chunk")
        ("tool_use", {"id": ..., "name": ..., "input": {...}})
        ("done", "end_turn" | "tool_use")
    """
    url = (base_url or "https://api.anthropic.com").rstrip("/") + "/v1/messages"

    body = {
        "model": model or "claude-sonnet-4-6",
        "max_tokens": max_tokens,
        "messages": messages,
        "stream": True,
    }
    if tools:
        body["tools"] = tools
    if system:
        body["system"] = system

    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key or "",
            "anthropic-version": "2023-06-01",
        },
    )
    resp = urllib.request.urlopen(req)

    blocks = {}
    stop_reason = None

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

            t = event.get("type")

            if t == "content_block_start":
                idx = event["index"]
                blk = event["content_block"]
                if blk["type"] == "text":
                    blocks[idx] = {"type": "text", "text": ""}
                elif blk["type"] == "tool_use":
                    blocks[idx] = {"type": "tool_use", "id": blk["id"], "name": blk["name"], "input_json": ""}

            elif t == "content_block_delta":
                idx = event["index"]
                delta = event["delta"]
                if delta["type"] == "text_delta":
                    blocks[idx]["text"] += delta["text"]
                    yield ("text", delta["text"])
                elif delta["type"] == "input_json_delta":
                    blocks[idx]["input_json"] += delta["partial_json"]

            elif t == "content_block_stop":
                idx = event["index"]
                blk = blocks.get(idx)
                if blk and blk["type"] == "tool_use":
                    inp = json.loads(blk["input_json"]) if blk["input_json"] else {}
                    yield ("tool_use", {"id": blk["id"], "name": blk["name"], "input": inp})

            elif t == "message_delta":
                stop_reason = event["delta"].get("stop_reason")

        yield ("done", stop_reason or "end_turn")
    finally:
        resp.close()
