"""
Cold-callout by native platform handle: /callout <handle> <platform>
for reaching someone who has NEVER used this bot, on a platform where
the bot can't message strangers directly (everything except WhatsApp).

This does NOT actually message the target — it can't, that's the whole
platform restriction. Instead it creates a stub account with an ugly
auto-generated username and hands the CHALLENGER a shareable invite
link. The real callout (with a real 5-minute response window) only
starts once the target clicks that link and starts the bot — see
handle_telegram_start() below, wired up in core/router.py.
"""
from core import db, config
from core.commands import OutMessage, _deliver_to_user


def build_invite_link(platform: str, claim_code: str) -> str | None:
    if platform == "telegram":
        if not config.TELEGRAM_BOT_USERNAME:
            return None
        return f"https://t.me/{config.TELEGRAM_BOT_USERNAME}?start=claim_{claim_code}"
    return None  # add other platforms here as real adapters + deep-link support exist


def handle_cold_callout(platform: str, sender_platform_id: str, sender_username: str, target_handle: str, target_platform: str) -> list[OutMessage]:
    challenger = db.get_or_create_user(platform, sender_platform_id, sender_username)
    target_platform = target_platform.lower()

    if target_platform not in config.COLD_INVITE_SUPPORTED_PLATFORMS:
        supported = ", ".join(sorted(config.COLD_INVITE_SUPPORTED_PLATFORMS)) or "(none configured yet)"
        return [OutMessage(platform, sender_platform_id, f"Cold-callout by handle isn't set up for '{target_platform}' yet. Currently supported: {supported}.")]

    faucet_ok, faucet_message = db.check_and_consume_callout(challenger["id"])
    if not faucet_ok:
        return [OutMessage(platform, sender_platform_id, faucet_message)]

    stub_user, claim_code = db.create_pending_invite(target_platform, target_handle, challenger["id"])
    link = build_invite_link(target_platform, claim_code)

    if not link:
        return [OutMessage(platform, sender_platform_id, f"Couldn't build an invite link for {target_platform} — check TELEGRAM_BOT_USERNAME is set in your environment.")]

    return [
        OutMessage(
            platform, sender_platform_id,
            f"Can't message @{target_handle} directly on {target_platform} — bots can't reach people who haven't "
            f"started them. Send them this link yourself, and your callout activates the moment they open it:\n\n{link}",
        )
    ]


def handle_telegram_start(platform: str, sender_platform_id: str, sender_username: str, payload: str) -> list[OutMessage]:
    """Handles /start (with or without a deep-link payload). Wired up
    in core/router.py for the '/start' command."""
    from core import commands  # local import avoids a circular import with commands.py

    if not payload.startswith("claim_"):
        return [OutMessage(platform, sender_platform_id, "Welcome to Chess Callout Bot! Send /help to see what you can do.")]

    claim_code = payload[len("claim_"):]
    invite = db.claim_pending_invite(claim_code, platform, sender_platform_id)
    if not invite:
        return [OutMessage(platform, sender_platform_id, "That invite link is invalid or already used. Send /help to see what you can do.")]

    stub_user = db.get_user_by_id(invite["user_id"])
    challenger = db.get_user_by_id(invite["created_by"])

    # Set their real display name as the username IF it's still the ugly
    # auto-generated one and their real display name is available and free —
    # nice-to-have, not required, so failures here are silently ignored.
    if sender_username and sender_username != stub_user["username"]:
        db.set_username(stub_user["id"], sender_username)  # best-effort; ignore failure (name taken etc.)

    db.create_callout(invite["created_by"], stub_user["id"])
    db.mark_called_out(stub_user["id"])

    notify_challenger = _deliver_to_user(challenger["id"], f"{stub_user['username']} just joined — your callout to them is now active, they have 5 minutes to respond!")
    if notify_challenger:
        from core.senders import send
        send(notify_challenger.platform, notify_challenger.to_platform_id, notify_challenger.text)

    return [
        OutMessage(
            platform, sender_platform_id,
            f"Welcome! {challenger['username']} has called you out for a chess match!\n"
            f"You have 5 minutes to respond.\n\n/yes to play\n/no to reject\n\n"
            f"(Your username is '{stub_user['username']}' — change it with /change_username, first change is free.)",
        )
    ]
