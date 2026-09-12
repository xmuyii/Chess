"""
Webhook receiver for GOWA (go-whatsapp-web-multidevice) — an
UNOFFICIAL WhatsApp integration. See README for the real tradeoffs
before using this: ToS violation, real ban risk for the number you
connect, no appeal process. The official adapter
(adapters/whatsapp_adapter.py) doesn't carry this risk.

GOWA POSTs every WhatsApp event to this endpoint when you configure
`--webhook=https://<this-service>/webhook` on your GOWA instance.
Every request is HMAC-verified against GOWA_WEBHOOK_SECRET before
being trusted — this is what stops someone else from POSTing fake
messages to your bot pretending to be real WhatsApp users.

Run with: flask --app adapters.gowa_adapter run -p 8001
"""
from dotenv import load_dotenv
load_dotenv()

import hmac
import hashlib
import os

from flask import Flask, request, jsonify

from core.router import handle_incoming

app = Flask(__name__)


def _verify_signature(raw_body: bytes, signature_header: str | None) -> bool:
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    secret = os.environ.get("GOWA_WEBHOOK_SECRET", "secret")
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    received = signature_header.removeprefix("sha256=")
    return hmac.compare_digest(expected, received)


@app.route("/webhook", methods=["POST"])
def webhook():
    raw_body = request.get_data()
    signature = request.headers.get("X-Hub-Signature-256")
    if not _verify_signature(raw_body, signature):
        return jsonify({"error": "invalid signature"}), 401

    body = request.get_json(silent=True) or {}
    if body.get("event") != "message":
        return jsonify({"status": "ignored"}), 200  # only care about text messages, not reactions/receipts/etc.

    payload = body.get("payload", {})
    if payload.get("is_from_me"):
        return jsonify({"status": "ignored"}), 200  # echo of our own outbound message, not a real incoming one

    sender_jid = payload.get("from", "")
    text = payload.get("body", "")
    display_name = payload.get("from_name") or sender_jid

    if not sender_jid or not text:
        return jsonify({"status": "ignored"}), 200

    handle_incoming("gowa", sender_jid, display_name, text)
    return jsonify({"status": "ok"}), 200

import os
import requests

GOWA_API_URL = os.environ.get("GOWA_API_URL", "http://localhost:3000")
GOWA_API_KEY = os.environ.get("GOWA_API_KEY", "")  # Basic auth or API key if configured in GOWA

def send_whatsapp_message(to_jid: str, text: str) -> bool:
    """
    Sends an outbound message back via GOWA REST API.
    """
    # Strip WhatsApp suffix to extract raw phone number or group ID
    phone_number = to_jid.replace("@s.whatsapp.net", "").replace("@g.us", "")
    
    url = f"{GOWA_API_URL.rstrip('/')}/send/message"
    
    headers = {
        "Content-Type": "application/json"
    }
    
    # If GOWA uses basic auth / token, attach here
    if GOWA_API_KEY:
        headers["Authorization"] = f"Bearer {GOWA_API_KEY}"

    payload = {
        "phone": phone_number,
        "message": text
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        response.raise_for_status()
        print(f"[GOWA Outbound] Message successfully sent to {phone_number}")
        return True
    except Exception as e:
        print(f"[GOWA Outbound Error] Failed to send message to {phone_number}: {e}")
        return False


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8001))
    app.run(host="0.0.0.0", port=port)
