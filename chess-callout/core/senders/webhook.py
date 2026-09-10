"""
Generic fallback sender for platforms that were never given a dedicated
core/senders/<platform>.py module — i.e. platforms integrated purely
via HTTP (see api/core_api.py). Looks up where to POST replies for a
given platform from the platform_webhooks table, and pushes the message
there. That external service is then responsible for actually calling
Discord/Slack/whatever's real API — this code never needs to know how.
"""
import requests

from core import db


def send_via_webhook(platform: str, platform_id: str, text: str) -> None:
    webhook = db.get_platform_webhook(platform)
    if not webhook:
        raise RuntimeError(f"No sender or registered webhook for platform '{platform}'")

    resp = requests.post(
        webhook["outbound_url"],
        json={"platform_id": platform_id, "text": text},
        headers={"X-Webhook-Secret": webhook["outbound_secret"]},
        timeout=10,
    )
    resp.raise_for_status()
