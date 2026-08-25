"""
Central outbound-message dispatch. This exists because a callout can
cross platforms: a Telegram user can call out a WhatsApp user by
username, so whichever code is handling the request needs to be able
to deliver a message via WhatsApp too — not just reply on its own platform.

Two ways a platform gets delivery support:
  1. A dedicated module (whatsapp.py, telegram.py) calls register() at
     import time — used for platforms this codebase has direct SDK
     integration with.
  2. No dedicated module -> falls back to core.senders.webhook, which
     looks up a registered outbound URL in Supabase (platform_webhooks)
     and POSTs there. This is what makes new platforms pluggable via
     pure HTTP, with zero changes to this repo — see api/core_api.py.
"""
from typing import Callable

_SENDERS: dict[str, Callable[[str, str], None]] = {}


def register(platform: str, send_fn: Callable[[str, str], None]) -> None:
    _SENDERS[platform] = send_fn


def send(platform: str, platform_id: str, text: str) -> None:
    fn = _SENDERS.get(platform)
    if fn is not None:
        fn(platform_id, text)
        return

    from core.senders.webhook import send_via_webhook  # local import avoids a cycle at module load time
    send_via_webhook(platform, platform_id, text)
