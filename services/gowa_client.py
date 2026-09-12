import os
import requests

GOWA_API_URL = os.environ.get("GOWA_API_URL", "http://localhost:3000")
GOWA_API_KEY = os.environ.get("GOWA_API_KEY", "")


def send_whatsapp_message(to_jid: str, text: str) -> bool:
    """Sends an outbound WhatsApp message via GOWA's REST API."""
    phone_number = to_jid.replace("@s.whatsapp.net", "").replace("@g.us", "")
    url = f"{GOWA_API_URL.rstrip('/')}/send/message"

    headers = {
        "Content-Type": "application/json"
    }
    if GOWA_API_KEY:
        headers["Authorization"] = f"Bearer {GOWA_API_KEY}"

    payload = {
        "phone": phone_number,
        "message": text
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        response.raise_for_status()
        print(f"[GOWA Client] Outbound message sent to {phone_number}")
        return True
    except Exception as exc:
        print(f"[GOWA Client Error] Failed to send message to {phone_number}: {exc}")
        return False