"""
Outbound sender for GOWA (go-whatsapp-web-multidevice).
"""
import os
import re
import requests

from core.senders.registry import register


def sanitize_phone_number(raw_target: str) -> str:
    """
    Strips JID suffixes, device tags, plus signs, spaces, and hyphens,
    leaving strictly the numeric phone string expected by GOWA.
    """
    if not raw_target:
        return ""
    # Strip device identifier if present (e.g. '2349113528965:2@s.whatsapp.net' -> '2349113528965@s.whatsapp.net')
    clean_str = raw_target.split(":")[0]
    # Remove any non-numeric characters (+, @s.whatsapp.net, spaces, etc.)
    return re.sub(r"\D", "", clean_str)


def send_gowa_message(platform_id: str, text: str) -> None:
    # Check GOWA_BASE_URL first, fall back to GOWA_API_URL
    base_url = os.environ.get("GOWA_BASE_URL") or os.environ.get("GOWA_API_URL", "https://go-wa.up.railway.app")
    base_url = base_url.rstrip("/")
    device_id = os.environ.get("GOWA_DEVICE_ID", "")

    phone_number = sanitize_phone_number(platform_id)
    if not phone_number:
        print(f"[GOWA Sender Error] Could not parse a valid phone number from target: '{platform_id}'")
        return

    payload = {"phone": phone_number, "message": text}
    headers = {"Content-Type": "application/json"}
    
    if device_id:
        headers["X-Device-Id"] = device_id

    # Optional Bearer Token support if configured on Railway
    api_key = os.environ.get("GOWA_API_KEY", "")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    auth = None
    user = os.environ.get("GOWA_BASIC_AUTH_USER", "")
    password = os.environ.get("GOWA_BASIC_AUTH_PASS", "")
    if user and password:
        auth = (user, password)

    try:
        resp = requests.post(
            f"{base_url}/send/message",
            json=payload,
            headers=headers,
            auth=auth,
            timeout=15
        )
        print(f"[GOWA Outbound] Dispatch to {phone_number} | HTTP {resp.status_code} | Body: {resp.text}")
        resp.raise_for_status()
    except Exception as exc:
        print(f"[GOWA Sender Exception] Failed delivering message to {phone_number}: {exc}")
        raise


register("gowa", send_gowa_message)