"""
REST API for triggering bot commands from anywhere — not just the
Python adapters in adapters/. This is what lets a platform be
integrated without touching this codebase: a service in any language
just needs to speak HTTPS.

Registered as a Flask Blueprint and mounted on the same app as the
chess board (web/game_server.py), so there's only one web service to
deploy — see README for the Railway setup.

Two-way integration for a brand-new platform:
  1. Their service POSTs each inbound command to /api/v1/incoming here.
  2. Their service registers an outbound URL via /api/v1/platforms, so
     this app can push replies back to THEM — see core/senders/webhook.py.
     They're then responsible for actually delivering to their platform's
     real API (Discord, Slack, whatever); this code never needs to know how.
"""
import os
import secrets
from functools import wraps

from flask import Blueprint, request, jsonify

from core import db
from core.router import handle_incoming

bp = Blueprint("core_api", __name__, url_prefix="/api/v1")


def require_api_key(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        expected = os.environ["CORE_API_KEY"]
        provided = request.headers.get("X-API-Key", "")
        if not secrets.compare_digest(provided, expected):
            return jsonify({"error": "unauthorized"}), 401
        return fn(*args, **kwargs)
    return wrapper


@bp.route("/incoming", methods=["POST"])
@require_api_key
def incoming():
    """
    Body: {"platform": "discord", "platform_id": "...", "display_name": "...", "text": "/callout alice"}

    Runs the command exactly as if a chat adapter had received it, and
    delivers every resulting reply (to whichever platform(s) they
    belong on — could be a different platform than the sender's) via
    core.senders. Fire-and-forget from the caller's side: nothing is
    returned except an ack, since replies may not even come back to
    the platform that sent this request.
    """
    body = request.get_json(silent=True) or {}
    platform = body.get("platform")
    platform_id = body.get("platform_id")
    display_name = body.get("display_name") or platform_id
    text = body.get("text", "")

    if not platform or not platform_id:
        return jsonify({"error": "platform and platform_id are required"}), 400

    handle_incoming(platform, platform_id, display_name, text)
    return jsonify({"status": "accepted"}), 202


@bp.route("/platforms", methods=["POST"])
@require_api_key
def register_platform():
    """
    Body: {"platform": "discord", "outbound_url": "https://your-service.com/webhook-out", "outbound_secret": "..."}

    Registers where this app should POST outbound replies for a new
    platform. outbound_secret is sent back as X-Webhook-Secret on every
    push, so the receiving service can verify the request really came
    from here before trusting it.
    """
    body = request.get_json(silent=True) or {}
    platform = body.get("platform")
    outbound_url = body.get("outbound_url")
    outbound_secret = body.get("outbound_secret")

    if not all([platform, outbound_url, outbound_secret]):
        return jsonify({"error": "platform, outbound_url, and outbound_secret are required"}), 400
    if not outbound_url.startswith("https://"):
        return jsonify({"error": "outbound_url must be https"}), 400

    db.register_platform_webhook(platform, outbound_url, outbound_secret)
    return jsonify({"status": "registered", "platform": platform}), 201


@bp.route("/leaderboard", methods=["GET"])
def leaderboard():
    """Public, read-only — no API key needed. Safe to call from a
    public website widget or an /api/v1/incoming caller that wants
    to render a leaderboard itself instead of just chat text."""
    limit = int(request.args.get("limit", 10))
    rows = db.get_leaderboard(limit=min(limit, 100))
    return jsonify({"leaderboard": rows})
