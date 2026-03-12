#!/usr/bin/env python3
"""Telegram gateway for atom agent. Zero dependencies beyond stdlib."""

import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agent import agent_loop

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
API = f"https://api.telegram.org/bot{TOKEN}"

conversations = {}  # chat_id (int) -> messages (list)


def tg(method, **params):
    """Call Telegram Bot API. Returns parsed JSON or None on error."""
    data = json.dumps(params).encode()
    req = urllib.request.Request(
        f"{API}/{method}", data=data,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        print(f"Telegram API error ({method}): {e.code} {e.read().decode()}")
        return None


def send_message(chat_id, text):
    """Send text, splitting at Telegram's 4096 char limit."""
    if not text:
        text = "(no response)"
    while text:
        chunk, text = text[:4096], text[4096:]
        tg("sendMessage", chat_id=chat_id, text=chunk)


def handle_message(chat_id, text):
    """Process a user message through the agent."""
    if text.strip().lower() in ("/new", "/start"):
        conversations.pop(chat_id, None)
        tg("sendMessage", chat_id=chat_id, text="Conversation cleared." if "/new" in text.lower() else "Welcome! Send a message to begin.")
        return

    if chat_id not in conversations:
        conversations[chat_id] = []
    messages = conversations[chat_id]
    messages.append({"role": "user", "content": text})

    tg("sendChatAction", chat_id=chat_id, action="typing")

    result_text = ""

    def send(event_type, data):
        nonlocal result_text
        if event_type == "text":
            result_text += data

    try:
        agent_loop(messages, send)
    except Exception as e:
        send_message(chat_id, f"Error: {e}")
        return

    send_message(chat_id, result_text)


def main():
    if not TOKEN:
        print("Set TELEGRAM_BOT_TOKEN in .env")
        sys.exit(1)

    me = tg("getMe")
    if not me or not me.get("ok"):
        print("Invalid bot token.")
        sys.exit(1)
    print(f"Telegram bot: @{me['result']['username']}")

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
                text = msg.get("text")
                chat_id = msg.get("chat", {}).get("id")
                if text and chat_id:
                    handle_message(chat_id, text)
        except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
            print(f"Polling error: {e}")
            time.sleep(5)
        except KeyboardInterrupt:
            print("\nStopped.")
            break


if __name__ == "__main__":
    main()
