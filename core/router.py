"""
Parses raw text into a command, runs it, and delivers every resulting
OutMessage through core.senders — regardless of which platform sent
the reply belongs to. This is what makes cross-platform callouts work:
an adapter just calls handle_incoming(); it doesn't need to know or
care where the replies end up.
"""
import traceback

from core import commands, senders


def handle_incoming(platform: str, sender_platform_id: str, sender_username: str, raw_text: str) -> None:
    for out in _route(platform, sender_platform_id, sender_username, raw_text):
        try:
            senders.send(out.platform, out.to_platform_id, out.text)
        except Exception:
            # One platform being unreachable (e.g. WhatsApp creds not
            # configured yet) shouldn't stop the rest of the replies in
            # this batch from going out, and shouldn't look like the
            # whole command crashed — just log it and keep going.
            print(f"WARNING: failed to deliver a message to {out.platform}:{out.to_platform_id}")
            traceback.print_exc()


def _route(platform: str, sender_platform_id: str, sender_username: str, raw_text: str) -> list[commands.OutMessage]:
    text = raw_text.strip()
    if not text:
        return []

    parts = text.split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""

    if cmd == "/callout":
        return commands.handle_callout(platform, sender_platform_id, sender_username, arg)
    if cmd == "/yes":
        return commands.handle_accept(platform, sender_platform_id, sender_username)
    if cmd == "/no":
        return commands.handle_decline(platform, sender_platform_id, sender_username)
    if cmd == "/leaderboard":
        return commands.handle_leaderboard(platform, sender_platform_id, sender_username)
    if cmd == "/change_username":
        return commands.handle_change_username(platform, sender_platform_id, sender_username, arg)
    if cmd == "/link":
        return commands.handle_link(platform, sender_platform_id, sender_username, arg)
    if cmd == "/whoami":
        return commands.handle_whoami(platform, sender_platform_id, sender_username)
    if cmd == "/random":
        return commands.handle_random(platform, sender_platform_id, sender_username)
    if cmd == "/cancel_random":
        return commands.handle_cancel_random(platform, sender_platform_id, sender_username)
    if cmd == "/play_bot":
        return commands.handle_play_bot(platform, sender_platform_id, sender_username)
    if cmd == "/shop":
        return commands.handle_shop(platform, sender_platform_id, sender_username)
    if cmd == "/help":
        return commands.handle_help(platform, sender_platform_id, sender_username)

    return [commands.OutMessage(platform, sender_platform_id, "Unknown command. Send /help to see what I can do.")]
