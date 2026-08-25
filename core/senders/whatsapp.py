import os
import requests

from core.senders.registry import register

GRAPH_URL = "https://graph.facebook.com/v20.0/{phone_number_id}/messages"


def send_whatsapp_message(to_number: str, text: str) -> None:
    # Read env vars lazily so this module can be imported even when
    # only *other* platforms are configured in this deployment.
    access_token = os.environ["WA_ACCESS_TOKEN"]
    phone_number_id = os.environ["WA_PHONE_NUMBER_ID"]

    payload = {
        "messaging_product": "whatsapp",
        "to": to_number.lstrip("+"),
        "type": "text",
        "text": {"body": text},
    }
    headers = {"Authorization": f"Bearer {access_token}"}
    resp = requests.post(GRAPH_URL.format(phone_number_id=phone_number_id), json=payload, headers=headers, timeout=10)
    resp.raise_for_status()


register("whatsapp", send_whatsapp_message)
