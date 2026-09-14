import os
import re
import requests

GOWA_API_URL = os.environ.get("GOWA_API_URL", "https://go-wa.up.railway.app")
GOWA_API_KEY = os.environ.get("GOWA_API_KEY", "")

def send_whatsapp_message(to_jid: str, text: str) -> bool:
    """
    Sends an outbound message back via GOWA REST API.
    Cleans any raw input (JID, formatted phone with '+', etc.) to pure digits.
    """
    # 1. Strip device ID if present (e.g., '2349113528965:2@s.whatsapp.net' -> '2349113528965@s.whatsapp.net')
    raw_target = to_jid.split(":")[0] if ":" in to_jid else to_jid
    
    # 2. Extract strictly numbers (removes '+', '@s.whatsapp.net', spaces, hyphens)
    phone_number = re.sub(r"\D", "", raw_target)
    
    if not phone_number:
        print(f"[GOWA Client Error] Cannot send message: invalid phone target '{to_jid}'")
        return False

    url = f"{GOWA_API_URL.rstrip('/')}/send/message"
    headers = {"Content-Type": "application/json"}
    
    if GOWA_API_KEY:
        headers["Authorization"] = f"Bearer {GOWA_API_KEY}"

    payload = {
        "phone": phone_number,
        "message": text
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        print(f"[GOWA Client Outbound] Sent to {phone_number} | Status: {response.status_code} | Body: {response.text}")
        response.raise_for_status()
        return True
    except Exception as exc:
        print(f"[GOWA Client Error] Failed sending message to {phone_number}: {exc}")
        return False