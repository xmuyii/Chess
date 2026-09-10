"""
Outbound sender for GOWA (go-whatsapp-web-multidevice) — an UNOFFICIAL
WhatsApp integration. See README for the real tradeoffs before relying
on this: it works outside WhatsApp's Terms of Service, and the number
you use with it can be banned without appeal, unlike the official
Cloud API adapter in this same repo.

IMPORTANT: the request body field names below (`phone`, `message`) are
this project's long-standing convention, but I could not verify them
against the live openapi.yaml when this was written. VERIFY WITH A
RAW CURL TEST (see README setup steps) before wiring this into the
full pipeline — if the field names have changed, this is the one
place to fix it.
"""
import os
import requests

from core.senders.registry import register


def send_gowa_message(platform_id: str, text: str) -> None:
    base_url = os.environ["GOWA_BASE_URL"].rstrip("/")
    device_id = os.environ.get("GOWA_DEVICE_ID", "")

    payload = {"phone": platform_id, "message": text}
    headers = {}
    if device_id:
        headers["X-Device-Id"] = device_id

    auth = None
    user = os.environ.get("GOWA_BASIC_AUTH_USER", "")
    password = os.environ.get("GOWA_BASIC_AUTH_PASS", "")
    if user and password:
        auth = (user, password)

    resp = requests.post(f"{base_url}/send/message", json=payload, headers=headers, auth=auth, timeout=15)
    resp.raise_for_status()


register("gowa", send_gowa_message)
