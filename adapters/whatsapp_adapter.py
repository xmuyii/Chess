"""
WhatsApp adapter using Meta's WhatsApp Cloud API.
Set env vars: WA_VERIFY_TOKEN, WA_ACCESS_TOKEN, WA_PHONE_NUMBER_ID
(easiest via a .env file — see .env.example)

Run with: flask --app adapters.whatsapp_adapter run -p 8000
Point your WhatsApp Cloud API webhook at https://<yourhost>/webhook

Note: this adapter only parses inbound messages and calls
core.router.handle_incoming(). Sending replies (even cross-platform
ones) happens through core.senders, not here.
"""
from dotenv import load_dotenv
load_dotenv()  # must run before importing anything that reads env vars at import time

import os
from flask import Flask, request, jsonify

from core.router import handle_incoming

app = Flask(__name__)
WA_VERIFY_TOKEN = os.environ["WA_VERIFY_TOKEN"]


@app.route("/webhook", methods=["GET"])
def verify():
    if request.args.get("hub.verify_token") == WA_VERIFY_TOKEN:
        return request.args.get("hub.challenge"), 200
    return "Forbidden", 403


@app.route("/webhook", methods=["POST"])
def incoming():
    body = request.get_json(silent=True) or {}
    print("RAW INCOMING PAYLOAD:", body, flush=True)  # <-- ADD THIS FOR RAILWAY LOGS

    try:
        entry = body["entry"][0]["changes"][0]["value"]
        messages = entry.get("messages", [])
    except (KeyError, IndexError):
        print("PAYLOAD DID NOT CONTAIN MESSAGES (STATUS/READ RECEIPT)", flush=True)
        return jsonify({"status": "ignored"}), 200

    for msg in messages:
        sender_number = "+" + msg["from"]
        sender_name = entry.get("contacts", [{}])[0].get("profile", {}).get("name", sender_number)
        text = msg.get("text", {}).get("body", "")
        
        print(f"PARSED MESSAGE FROM {sender_name} ({sender_number}): {text}", flush=True) # <-- ADD THIS
        handle_incoming("whatsapp", sender_number, sender_name, text)

    return jsonify({"status": "ok"}), 200


if __name__ == "__main__":
    app.run(port=8000)
