"""
Telegram adapter, long-polling for simplicity (switch to a webhook for
production). Set env var: TELEGRAM_BOT_TOKEN (easiest via a .env file —
see .env.example)
Run with: python -m adapters.telegram_adapter

New Telegram users get an auto-generated username by default (their
Telegram @handle if set, else a generated one) — they can immediately
/change_username to pick their permanent global ID.
"""
from dotenv import load_dotenv
load_dotenv()

import os
import sys
import time
import traceback
import requests

from core.router import handle_incoming

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
API = f"https://api.telegram.org/bot{TOKEN}"


def verify_token_and_get_bot_username() -> str:
    """Fail loudly and immediately if the token is wrong, instead of
    the adapter silently doing nothing forever. Retries a few times
    before giving up, since a single slow/flaky network blip shouldn't
    be treated the same as a genuinely broken connection."""
    last_error = None
    for attempt in range(3):
        try:
            resp = requests.get(f"{API}/getMe", timeout=20).json()
        except requests.exceptions.RequestException as e:
            last_error = e
            print(f"Attempt {attempt + 1}/3 to reach Telegram failed ({e}), retrying...")
            continue

        if not resp.get("ok"):
            print(f"ERROR: Telegram rejected TELEGRAM_BOT_TOKEN — {resp.get('description', 'no details given')}.")
            print("Double-check the token you got from @BotFather is pasted correctly in .env.")
            sys.exit(1)

        return resp["result"]["username"]

    print(f"ERROR: couldn't reach Telegram's API after 3 attempts — check your internet connection. ({last_error})")
    print("If a plain 'curl https://api.telegram.org' also fails/hangs in your terminal, this is a network")
    print("issue (ISP filtering, needs a VPN), not something wrong with this code.")
    sys.exit(1)


def poll_forever():
    bot_username = verify_token_and_get_bot_username()
    print(f"Connected as @{bot_username}. Message this bot on Telegram to test it.")
    print("Waiting for messages... (this will print each one as it arrives)")

    offset = None
    while True:
        try:
            params = {"timeout": 30}
            if offset:
                params["offset"] = offset
            resp = requests.get(f"{API}/getUpdates", params=params, timeout=35).json()
        except requests.exceptions.RequestException as e:
            print(f"WARNING: network error polling Telegram, retrying in 3s — {e}")
            time.sleep(3)
            continue

        if not resp.get("ok"):
            print(f"WARNING: Telegram returned an error, retrying in 3s — {resp.get('description')}")
            time.sleep(3)
            continue

        for update in resp.get("result", []):
            offset = update["update_id"] + 1
            msg = update.get("message")
            if not msg or "text" not in msg:
                continue
            chat_id = str(msg["chat"]["id"])
            display_hint = msg["from"].get("username") or msg["from"].get("first_name", chat_id)
            text = msg["text"]

            print(f"<- from {display_hint} ({chat_id}): {text}")
            try:
                handle_incoming("telegram", chat_id, display_hint, text)
            except Exception:
                # Don't let one bad message kill the whole polling loop —
                # print the full traceback so the actual cause is visible
                # instead of the adapter just going quiet.
                print("ERROR while handling that message:")
                traceback.print_exc()


if __name__ == "__main__":
    poll_forever()
