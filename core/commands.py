"""
Platform-agnostic command handlers.

/callout target resolution:
  - target starts with '+'  -> treated as a WhatsApp phone number.
    If nobody's linked that number yet, a new account is created for
    them on the spot (this is how you can call out a friend who has
    never used the bot before, purely by phone number).
  - anything else            -> treated as a username. The account
    must already exist (usernames aren't guessable/creatable this
    way) and delivery goes to whatever platform they're linked to.

Every OutMessage carries an explicit `platform`, since the reply
target may be on a *different* platform than whoever sent the command.
"""
from dataclasses import dataclass

from core import db, config, game_tokens


@dataclass
class OutMessage:
    platform: str
    to_platform_id: str
    text: str


FOOTER = "\n\n———\n/leaderboard  /shop  /help  /change_username <name>"


def _with_footer(user: dict, text: str) -> str:
    return f"{text}\n\nCoins: {user['coins']} | Points: {user['points']}{FOOTER}"


def _deliver_to_user(user_id: str, text: str) -> OutMessage | None:
    identity = db.get_primary_identity(user_id)
    if not identity:
        return None  # account exists but has no linked platform (shouldn't normally happen)
    return OutMessage(identity["platform"], identity["platform_id"], text)


# ------------------------------------------------------------------
# /callout <+phone or username>
# ------------------------------------------------------------------
def handle_callout(platform: str, sender_platform_id: str, sender_username: str, target: str) -> list[OutMessage]:
    challenger = db.get_or_create_user(platform, sender_platform_id, sender_username)
    target = target.strip()

    if not target:
        return [OutMessage(platform, sender_platform_id, "Usage: /callout +2345678901  or  /callout <username>")]

    if target.startswith("+"):
        # WhatsApp-specific: reach someone by phone, creating their account if needed.
        opponent = db.get_user_by_platform("whatsapp", target)
        if opponent is None:
            opponent = db.get_or_create_user("whatsapp", target, target)
    else:
        opponent = db.get_user_by_username(target)
        if opponent is None:
            return [OutMessage(platform, sender_platform_id, f"No player found with username '{target}'.")]

    if opponent["id"] == challenger["id"]:
        return [OutMessage(platform, sender_platform_id, "You can't call yourself out.")]

    if db.get_pending_callout_for(opponent["id"]):
        return [OutMessage(platform, sender_platform_id, f"{opponent['username']} already has a pending callout. Try again later.")]

    db.create_callout(challenger["id"], opponent["id"])

    notify = _deliver_to_user(
        opponent["id"],
        f"♟️ {challenger['username']} has called you out for a chess match!\n"
        f"You have 5 minutes to respond.\n\n/yes to play\n/no to reject",
    )
    out = [OutMessage(platform, sender_platform_id, f"Callout sent to {opponent['username']}. They have 5 minutes to respond.")]
    if notify:
        out.append(notify)
    else:
        out.append(OutMessage(platform, sender_platform_id, f"(Note: {opponent['username']} has no reachable platform on file.)"))
    return out


# ------------------------------------------------------------------
# /yes
# ------------------------------------------------------------------
def handle_accept(platform: str, sender_platform_id: str, sender_username: str) -> list[OutMessage]:
    opponent = db.get_or_create_user(platform, sender_platform_id, sender_username)
    callout = db.get_pending_callout_for(opponent["id"])
    if not callout:
        return [OutMessage(platform, sender_platform_id, "You don't have an active callout to respond to.")]

    db.resolve_callout(callout["id"], "accepted")
    game = db.create_game(callout["id"], white_id=callout["challenger_id"], black_id=opponent["id"])

    # Each player gets their own signed link — proves who they are and
    # which color they're playing, so the game can enforce that only
    # white can move white's pieces, only black can move black's.
    challenger_row = db.get_user_by_id(callout["challenger_id"])
    white_token = game_tokens.generate_player_token(game["id"], callout["challenger_id"], "white")
    black_token = game_tokens.generate_player_token(game["id"], opponent["id"], "black")
    white_link = f"{config.GAME_WEB_BASE_URL}/{white_token}"
    black_link = f"{config.GAME_WEB_BASE_URL}/{black_token}"

    out = [OutMessage(platform, sender_platform_id, f"Game on! You're playing black. Play here: {black_link}")]
    notify = _deliver_to_user(callout["challenger_id"], f"{opponent['username']} accepted! You're playing white. Play here: {white_link}")
    if notify:
        out.append(notify)
    return out


# ------------------------------------------------------------------
# /no
# ------------------------------------------------------------------
def handle_decline(platform: str, sender_platform_id: str, sender_username: str) -> list[OutMessage]:
    opponent = db.get_or_create_user(platform, sender_platform_id, sender_username)
    callout = db.get_pending_callout_for(opponent["id"])
    if not callout:
        return [OutMessage(platform, sender_platform_id, "You don't have an active callout to respond to.")]

    db.resolve_callout(callout["id"], "declined")
    db.adjust_user(opponent["id"], points_delta=config.DECLINE_PENALTY_POINTS, declines=1)
    db.log_transaction(opponent["id"], "decline_penalty", points_delta=config.DECLINE_PENALTY_POINTS, ref_id=callout["id"])

    updated = db.get_user_by_id(opponent["id"])
    out = [OutMessage(platform, sender_platform_id, _with_footer(updated, f"You declined. {config.DECLINE_PENALTY_POINTS} points."))]
    notify = _deliver_to_user(callout["challenger_id"], f"{opponent['username']} declined your callout.")
    if notify:
        out.append(notify)
    return out


# ------------------------------------------------------------------
# /leaderboard
# ------------------------------------------------------------------
def handle_leaderboard(platform: str, sender_platform_id: str, sender_username: str) -> list[OutMessage]:
    rows = db.get_leaderboard(limit=10)
    lines = [f"{r['rank']}. {r['username']} — {r['points']} pts" for r in rows]
    text = "🏆 Leaderboard\n" + "\n".join(lines) if lines else "Leaderboard is empty — be the first!"
    return [OutMessage(platform, sender_platform_id, text)]


# ------------------------------------------------------------------
# /change_username <name>
# ------------------------------------------------------------------
def handle_change_username(platform: str, sender_platform_id: str, sender_username: str, new_name: str) -> list[OutMessage]:
    user = db.get_or_create_user(platform, sender_platform_id, sender_username)
    ok, message = db.set_username(user["id"], new_name)
    if not ok:
        return [OutMessage(platform, sender_platform_id, message)]
    return [OutMessage(platform, sender_platform_id, f"Username updated to {new_name}. Anyone can now /callout {new_name} from any platform.")]


# ------------------------------------------------------------------
# /link [code]
# No arg: generate a code to use on the *other* platform.
# With arg: consume a code generated elsewhere, linking this platform
#           identity to that account.
# ------------------------------------------------------------------
def handle_link(platform: str, sender_platform_id: str, sender_username: str, arg: str) -> list[OutMessage]:
    arg = arg.strip()

    if not arg:
        user = db.get_or_create_user(platform, sender_platform_id, sender_username)
        code = db.create_link_code(user["id"])
        return [
            OutMessage(
                platform,
                sender_platform_id,
                f"Your link code: {code}\n\n"
                f"Send `/link {code}` from your other platform's chat with this bot "
                f"within 10 minutes to link that account to '{user['username']}'.",
            )
        ]

    ok, message = db.consume_link_code(arg, platform, sender_platform_id)
    if not ok:
        return [OutMessage(platform, sender_platform_id, message)]
    return [OutMessage(platform, sender_platform_id, f"Linked! This platform is now part of your account '{message}'.")]


# ------------------------------------------------------------------
# /whoami
# ------------------------------------------------------------------
def handle_whoami(platform: str, sender_platform_id: str, sender_username: str) -> list[OutMessage]:
    user = db.get_or_create_user(platform, sender_platform_id, sender_username)
    return [OutMessage(platform, sender_platform_id, f"You are '{user['username']}'. Others can /callout {user['username']} to reach you from any platform.")]


# ------------------------------------------------------------------
# /random — matchmaking queue
# ------------------------------------------------------------------
def handle_random(platform: str, sender_platform_id: str, sender_username: str) -> list[OutMessage]:
    import random as _random

    player = db.get_or_create_user(platform, sender_platform_id, sender_username)

    if db.is_in_queue(player["id"]):
        return [OutMessage(platform, sender_platform_id, "You're already in the matchmaking queue. Send /cancel_random to leave it.")]

    opponent_id = db.find_and_claim_opponent(exclude_user_id=player["id"])
    if not opponent_id:
        db.join_queue(player["id"])
        return [OutMessage(platform, sender_platform_id, "Searching for an opponent... you'll get a link the moment someone else runs /random. (/cancel_random to stop waiting)")]

    opponent = db.get_user_by_id(opponent_id)
    white_id, black_id = (player["id"], opponent_id) if _random.random() < 0.5 else (opponent_id, player["id"])

    game = db.create_game(callout_id=None, white_id=white_id, black_id=black_id)
    white_token = game_tokens.generate_player_token(game["id"], white_id, "white")
    black_token = game_tokens.generate_player_token(game["id"], black_id, "black")
    white_link = f"{config.GAME_WEB_BASE_URL}/{white_token}"
    black_link = f"{config.GAME_WEB_BASE_URL}/{black_token}"

    player_color, player_link = ("white", white_link) if player["id"] == white_id else ("black", black_link)
    opp_color, opp_link = ("black", black_link) if player_color == "white" else ("white", white_link)

    out = [OutMessage(platform, sender_platform_id, f"Match found! You're playing {opponent['username']} as {player_color}. Play here: {player_link}")]
    notify = _deliver_to_user(opponent_id, f"Match found! You're playing {player['username']} as {opp_color}. Play here: {opp_link}")
    if notify:
        out.append(notify)
    return out


def handle_cancel_random(platform: str, sender_platform_id: str, sender_username: str) -> list[OutMessage]:
    player = db.get_or_create_user(platform, sender_platform_id, sender_username)
    db.leave_queue(player["id"])
    return [OutMessage(platform, sender_platform_id, "Left the matchmaking queue.")]


# ------------------------------------------------------------------
# /play_bot — instant game against a built-in bot opponent
# ------------------------------------------------------------------
def handle_play_bot(platform: str, sender_platform_id: str, sender_username: str) -> list[OutMessage]:
    import random as _random

    player = db.get_or_create_user(platform, sender_platform_id, sender_username)
    bot = db.get_or_create_bot_user()

    white_id, black_id = (player["id"], bot["id"]) if _random.random() < 0.5 else (bot["id"], player["id"])
    game = db.create_game(callout_id=None, white_id=white_id, black_id=black_id)

    player_color = "white" if player["id"] == white_id else "black"
    player_token = game_tokens.generate_player_token(game["id"], player["id"], player_color)
    link = f"{config.GAME_WEB_BASE_URL}/{player_token}"

    # If the bot is white, it needs to make the opening move before the
    # human even opens the link — the same helper runs after every
    # human move too, see web/game_server.py.
    from core.bot_engine import play_bot_move_if_needed
    play_bot_move_if_needed(game["id"])

    return [OutMessage(platform, sender_platform_id, f"Playing as {player_color} against {config.BOT_USERNAME}. Play here: {link}")]


# ------------------------------------------------------------------
# /shop, /help
# ------------------------------------------------------------------
def handle_shop(platform: str, sender_platform_id: str, sender_username: str) -> list[OutMessage]:
    user = db.get_or_create_user(platform, sender_platform_id, sender_username)
    items = db.supabase.table("shop_items").select("*").eq("active", True).execute().data
    lines = [f"- {i['name']} ({i['price_coins']} coins): {i['description']}" for i in items]
    text = "🛒 Shop\n" + "\n".join(lines) if lines else "Shop is empty right now."
    return [OutMessage(platform, sender_platform_id, _with_footer(user, text))]


def handle_help(platform: str, sender_platform_id: str, sender_username: str) -> list[OutMessage]:
    text = (
        "♟️ Chess Callout Bot\n"
        "/callout +2345678901 — challenge by phone (WhatsApp)\n"
        "/callout <username> — challenge by username (any platform)\n"
        "/yes — accept a pending callout\n"
        "/no — reject a pending callout\n"
        "/leaderboard — top players\n"
        "/change_username <name> — set your global unique name\n"
        "/link — get a code to link another platform to this account\n"
        "/link <code> — use a code generated on another platform\n"
        "/whoami — show your current username\n"
        "/random — get matched with the next available player\n"
        "/cancel_random — stop waiting in the matchmaking queue\n"
        "/play_bot — play an instant game against the built-in bot\n"
        "/shop — spend your coins\n"
        "/help — this message"
    )
    return [OutMessage(platform, sender_platform_id, text)]
