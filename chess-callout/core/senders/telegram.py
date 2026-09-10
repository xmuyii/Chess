import os
import requests

from core.senders.registry import register


def send_telegram_message(chat_id: str, text: str) -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]  # read lazily, see whatsapp.py comment
    api = f"https://api.telegram.org/bot{token}"
    requests.post(f"{api}/sendMessage", json={"chat_id": chat_id, "text": text}, timeout=10)


register("telegram", send_telegram_message)
