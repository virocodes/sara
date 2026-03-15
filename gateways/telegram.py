#!/usr/bin/env python3
"""Telegram gateway for atom agent. HTTP client like client.py."""

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

env_path = Path(__file__).resolve().parent.parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
AGENT_URL = os.environ.get("AGENT_URL", "http://localhost:8080")
TG = f"https://api.telegram.org/bot{TOKEN}"

conversations = {}  # chat_id -> conversation_id


# --- Telegram API ---

def tg(method, **params):
    data = json.dumps(params).encode()
    req = urllib.request.Request(f"{TG}/{method}", data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        print(f"Telegram error ({method}): {e.code} {e.read().decode()}")
        return None


def send_text(chat_id, text):
    while text:
        chunk, text = text[:4096], text[4096:]
        tg("sendMessage", chat_id=chat_id, text=chunk)


# --- Message handling ---

def handle_message(chat_id, text):
    if text.strip().lower() in ("/new", "/start"):
        conversations.pop(chat_id, None)
        msg = "conversation cleared." if text.strip().lower() == "/new" else "hey! send a message to get started."
        tg("sendMessage", chat_id=chat_id, text=msg)
        return

    tg("sendChatAction", chat_id=chat_id, action="typing")

    body = {"message": text}
    conv_id = conversations.get(chat_id)
    if conv_id:
        body["conversation_id"] = conv_id

    try:
        req = urllib.request.Request(AGENT_URL, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        resp = urllib.request.urlopen(req, timeout=300)
    except Exception as e:
        send_text(chat_id, f"Error: {e}")
        return

    buf = ""
    sent = False
    for raw in resp:
        line = raw.decode().strip()
        if not line.startswith("data: "):
            continue
        event = json.loads(line[6:])
        t = event["type"]

        if t == "conversation":
            conversations[chat_id] = event["data"]
        elif t == "text":
            buf += event["data"]
        elif t == "tool_start":
            if buf.strip():
                send_text(chat_id, buf)
                buf = ""
                sent = True
            tg("sendChatAction", chat_id=chat_id, action="typing")
        elif t == "error":
            buf += f"\nError: {event['data']}"

    resp.close()
    if buf.strip():
        send_text(chat_id, buf)
    elif not sent:
        send_text(chat_id, "(no response)")


# --- Main ---

if __name__ == "__main__":
    if not TOKEN:
        print("Set TELEGRAM_BOT_TOKEN in .env")
        sys.exit(1)

    me = tg("getMe")
    if not me or not me.get("ok"):
        print("Invalid bot token.")
        sys.exit(1)

    print(f"Telegram bot: @{me['result']['username']}")
    print(f"Agent: {AGENT_URL}")

    offset = 0
    while True:
        try:
            resp = tg("getUpdates", offset=offset, timeout=30)
            if not resp or not resp.get("ok"):
                time.sleep(5)
                continue
            for update in resp["result"]:
                offset = update["update_id"] + 1
                msg = update.get("message", {})
                chat_id = msg.get("chat", {}).get("id")
                text = msg.get("text")
                if chat_id and text:
                    handle_message(chat_id, text)
        except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
            print(f"Poll error: {e}")
            time.sleep(5)
        except KeyboardInterrupt:
            print("\nStopped.")
            break
