"""Find your Telegram chat id.

Usage:
  1. Put your bot token in .env  (TELEGRAM_BOT_TOKEN=...)
  2. Send any message to your bot in Telegram.
  3. Run:  python setup_telegram.py
"""
import sys

import requests

from tracker import config, console

console.init()

token = config.secret("TELEGRAM_BOT_TOKEN")
if not token:
    sys.exit("No TELEGRAM_BOT_TOKEN in .env — add it first (see .env.example).")

me = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=30).json()
if not me.get("ok"):
    sys.exit(f"Token rejected by Telegram: {me.get('description')}")
print(f"Bot: @{me['result'].get('username')}")

updates = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=30).json()
chats = {}
for upd in updates.get("result", []):
    msg = upd.get("message") or upd.get("channel_post") or {}
    chat = msg.get("chat") or {}
    if chat.get("id"):
        name = chat.get("username") or chat.get("title") or chat.get("first_name") or "?"
        chats[chat["id"]] = name

if not chats:
    sys.exit(
        "No messages found. Open Telegram, send your bot any message "
        "(e.g. 'hi'), then run this again."
    )

print("\nFound these chats:")
for cid, name in chats.items():
    print(f"  TELEGRAM_CHAT_ID={cid}    ({name})")
print("\nPaste the line you want into your .env file.")
