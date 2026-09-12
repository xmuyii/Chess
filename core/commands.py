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
    return f"{text}\n\nCoins: {user['chess_coins']} | Points: {user['chess_all_time_points']}{FOOTER}"


def _deliver_to_user(user_id: str, text: str) -> OutMessage | None:
    identity = db.get_primary_identity(user_id)
    if not identity:
        return None  # account exists but has no linked platform (shouldn't normally happen)
    return OutMessage(identity["platform"], identity["platform_id"], text)


# ------------------------------------------------------------------
# /callout <+phone or username>  or  /callout <native_handle> <platform>
# ------------------------------------------------------------------
def handle_callout(platform: str, sender_platform_id: str, sender_username: str, target: str) -> list[OutMessage]:
    challenger = db.get_or_create_user(platform, sender_platform_id, sender_username)
    target = target.strip()

    if not target:
        return [OutMessage(platform, sender_platform_id, "Usage: /callout +2345678901  or  /callout <username>  or  /callout <handle> <platform>")]

    # Cold-callout by native handle: "/callout someone telegram" — two
    # tokens. A real username can never contain a space (see
    # set_username's validation), so any two-token target is
    # unambiguously this syntax, never a single-word username lookup.
    # This is genuinely different from every other callout path (the
    # target has never used this bot at all) — delegated to a dedicated
    # module, see core/cold_invite.py for why it can't just message them.
    parts = target.split()
    if len(parts) == 2:
        from core.cold_invite import handle_cold_callout
        return handle_cold_callout(platform, sender_platform_id, sender_username, parts[0], parts[1])

    if target.startswith("+"):
        # WhatsApp-specific: reach someone by phone, creating their account if needed.
        opponent = db.get_user_by_platform("whatsapp", target)
        if opponent is None:
            opponent = db.get_or_create_user("whatsapp", target, target)
    else:
        opponent = db.get_user_by_username(target)
        if opponent is None:
            return [OutMessage(platform, sender_platform_id, f"No player found with username '{target}'.")]

    if opponent["user_id"] == challenger["user_id"]:
        return [OutMessage(platform, sender_platform_id, "You can't call yourself out.")]

    if db.get_pending_callout_for(opponent["user_id"]):
        return [OutMessage(platform, sender_platform_id, f"{opponent['username']} already has a pending callout. Try again later.")]

    # Check the target's cooldown BEFORE consuming a faucet charge — a
    # callout that can't happen anyway shouldn't cost you one of your
    # limited daily callouts.
    cooldown_ok, cooldown_message = db.check_callout_cooldown(opponent["user_id"])
    if not cooldown_ok:
        return [OutMessage(platform, sender_platform_id, cooldown_message)]

    faucet_ok, faucet_message = db.check_and_consume_callout(challenger["user_id"])
    if not faucet_ok:
        return [OutMessage(platform, sender_platform_id, faucet_message)]

    db.create_callout(challenger["user_id"], opponent["user_id"])
    db.mark_called_out(opponent["user_id"])

    notify = _deliver_to_user(
        opponent["user_id"],
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
    callout = db.get_pending_callout_for(opponent["user_id"])
    if not callout:
        return [OutMessage(platform, sender_platform_id, "You don't have an active callout to respond to.")]

    db.resolve_callout(callout["id"], "accepted")
    game = db.create_game(callout["id"], white_id=callout["challenger_id"], black_id=opponent["user_id"], origin="callout")

    # Each player gets their own signed link — proves who they are and
    # which color they're playing, so the game can enforce that only
    # white can move white's pieces, only black can move black's.
    challenger_row = db.get_user_by_id(callout["challenger_id"])
    white_token = game_tokens.generate_player_token(game["id"], callout["challenger_id"], "white")
    black_token = game_tokens.generate_player_token(game["id"], opponent["user_id"], "black")
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
    callout = db.get_pending_callout_for(opponent["user_id"])
    if not callout:
        return [OutMessage(platform, sender_platform_id, "You don't have an active callout to respond to.")]

    db.resolve_callout(callout["id"], "declined")
    db.adjust_user(opponent["user_id"], points_delta=config.DECLINE_PENALTY_POINTS, declines=1)
    db.log_transaction(opponent["user_id"], "decline_penalty", points_delta=config.DECLINE_PENALTY_POINTS, ref_id=callout["id"])

    updated = db.get_user_by_id(opponent["user_id"])
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
    text = "🏆 All-time leaderboard\n" + "\n".join(lines) if lines else "Leaderboard is empty — be the first!"
    return [OutMessage(platform, sender_platform_id, text)]


def handle_weekly_leaderboard(platform: str, sender_platform_id: str, sender_username: str) -> list[OutMessage]:
    rows = db.get_weekly_leaderboard(limit=10)
    lines = [f"{r['rank']}. {r['username']} — {r['points']} pts" for r in rows]
    text = "📅 This week's leaderboard (resets every Monday)\n" + "\n".join(lines) if lines else "No points scored yet this week — be the first!"
    return [OutMessage(platform, sender_platform_id, text)]


# ------------------------------------------------------------------
# /change_username <name>
# ------------------------------------------------------------------
def handle_change_username(platform: str, sender_platform_id: str, sender_username: str, new_name: str) -> list[OutMessage]:
    user = db.get_or_create_user(platform, sender_platform_id, sender_username)

    changes_used = user.get("chess_username_changes_used", 0)
    cost = 0 if changes_used < config.FREE_USERNAME_CHANGES else config.USERNAME_CHANGE_COST_COINS
    if cost > 0 and user["chess_coins"] < cost:
        return [OutMessage(platform, sender_platform_id, f"Your free username change is used up — changing again costs {cost} coins, you have {user['chess_coins']}.")]

    ok, message = db.set_username(user["user_id"], new_name)
    if not ok:
        return [OutMessage(platform, sender_platform_id, message)]

    db.record_username_change(user["user_id"], cost)
    cost_note = f" (cost {cost} coins)" if cost > 0 else " (your one free change)"
    return [OutMessage(platform, sender_platform_id, f"Username updated to {new_name}{cost_note}. Anyone can now /callout {new_name} from any platform.")]


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
        code = db.create_link_code(user["user_id"])
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

    if db.is_in_queue(player["user_id"]):
        return [OutMessage(platform, sender_platform_id, "You're already in the matchmaking queue. Send /cancel_random to leave it.")]

    opponent_id = db.find_and_claim_opponent(exclude_user_id=player["user_id"])
    if not opponent_id:
        db.join_queue(player["user_id"])
        return [OutMessage(platform, sender_platform_id, "Searching for an opponent... you'll get a link the moment someone else runs /random. (/cancel_random to stop waiting)")]

    opponent = db.get_user_by_id(opponent_id)
    white_id, black_id = (player["user_id"], opponent_id) if _random.random() < 0.5 else (opponent_id, player["user_id"])

    game = db.create_game(callout_id=None, white_id=white_id, black_id=black_id, origin="random")
    white_token = game_tokens.generate_player_token(game["id"], white_id, "white")
    black_token = game_tokens.generate_player_token(game["id"], black_id, "black")
    white_link = f"{config.GAME_WEB_BASE_URL}/{white_token}"
    black_link = f"{config.GAME_WEB_BASE_URL}/{black_token}"

    player_color, player_link = ("white", white_link) if player["user_id"] == white_id else ("black", black_link)
    opp_color, opp_link = ("black", black_link) if player_color == "white" else ("white", white_link)

    out = [OutMessage(platform, sender_platform_id, f"Match found! You're playing {opponent['username']} as {player_color}. Play here: {player_link}")]
    notify = _deliver_to_user(opponent_id, f"Match found! You're playing {player['username']} as {opp_color}. Play here: {opp_link}")
    if notify:
        out.append(notify)
    return out


def handle_cancel_random(platform: str, sender_platform_id: str, sender_username: str) -> list[OutMessage]:
    player = db.get_or_create_user(platform, sender_platform_id, sender_username)
    db.leave_queue(player["user_id"])
    return [OutMessage(platform, sender_platform_id, "Left the matchmaking queue.")]


# ------------------------------------------------------------------
# /play_bot — instant game against a built-in bot opponent
# ------------------------------------------------------------------
def handle_play_bot(platform: str, sender_platform_id: str, sender_username: str) -> list[OutMessage]:
    import random as _random

    player = db.get_or_create_user(platform, sender_platform_id, sender_username)
    bot = db.get_or_create_bot_user()

    white_id, black_id = (player["user_id"], bot["user_id"]) if _random.random() < 0.5 else (bot["user_id"], player["user_id"])
    game = db.create_game(callout_id=None, white_id=white_id, black_id=black_id, origin="bot")

    player_color = "white" if player["user_id"] == white_id else "black"
    player_token = game_tokens.generate_player_token(game["id"], player["user_id"], player_color)
    link = f"{config.GAME_WEB_BASE_URL}/{player_token}"

    # If the bot is white, it needs to make the opening move before the
    # human even opens the link — the same helper runs after every
    # human move too, see web/game_server.py.
    from core.bot_engine import play_bot_move_if_needed
    play_bot_move_if_needed(game["id"])

    return [OutMessage(platform, sender_platform_id, f"Playing as {player_color} against {config.BOT_USERNAME}. Play here: {link}")]


# ------------------------------------------------------------------
# /apply_weekly, /cancel_weekly, /my_schedule — the opt-in weekly league
# ------------------------------------------------------------------
def handle_apply_weekly(platform: str, sender_platform_id: str, sender_username: str) -> list[OutMessage]:
    from core import weekly_league

    player = db.get_or_create_user(platform, sender_platform_id, sender_username)
    ok, message = weekly_league.sign_up(player["user_id"])
    return [OutMessage(platform, sender_platform_id, message)]


def handle_cancel_weekly(platform: str, sender_platform_id: str, sender_username: str) -> list[OutMessage]:
    from core import weekly_league

    player = db.get_or_create_user(platform, sender_platform_id, sender_username)
    message = weekly_league.cancel_signup(player["user_id"])
    return [OutMessage(platform, sender_platform_id, message)]


def handle_my_schedule(platform: str, sender_platform_id: str, sender_username: str) -> list[OutMessage]:
    from core import weekly_league

    player = db.get_or_create_user(platform, sender_platform_id, sender_username)
    match = weekly_league.get_my_current_match(player["user_id"])
    if not match:
        return [OutMessage(platform, sender_platform_id, "No scheduled match today. /apply_weekly to join the pool for next week.")]
    if match["status"] == "bye":
        return [OutMessage(platform, sender_platform_id, "You drew a bye today — no scheduled match, back in the pool tomorrow.")]

    game = db.get_game_by_id(match["game_id"])
    opponent_id = match["player_b_id"] if match["player_a_id"] == player["user_id"] else match["player_a_id"]
    opponent = db.get_user_by_id(opponent_id)
    if not game:
        return [OutMessage(platform, sender_platform_id, f"Scheduled against {opponent['username']} today (link unavailable right now).")]

    color = "white" if game["white_id"] == player["user_id"] else "black"
    token = game_tokens.generate_player_token(game["id"], player["user_id"], color)
    link = f"{config.GAME_WEB_BASE_URL}/{token}"
    status_note = " (already finished)" if game["status"] != "active" else ""
    return [OutMessage(platform, sender_platform_id, f"Today vs {opponent['username']}, you're {color}{status_note}. Link: {link}")]


# ------------------------------------------------------------------
# /shop, /help
# ------------------------------------------------------------------
def handle_shop(platform: str, sender_platform_id: str, sender_username: str) -> list[OutMessage]:
    user = db.get_or_create_user(platform, sender_platform_id, sender_username)
    items = db.supabase.table("chess_shop_items").select("*").eq("active", True).execute().data
    lines = [f"- {i['name']} ({i['price_coins']} coins): {i['description']}" for i in items]
    text = "🛒 Shop\n" + "\n".join(lines) if lines else "Shop is empty right now."
    return [OutMessage(platform, sender_platform_id, _with_footer(user, text))]


def handle_help(platform: str, sender_platform_id: str, sender_username: str) -> list[OutMessage]:
    rows = db.get_last_week_leaderboard(limit=10)
    if rows:
        last_week_lines = "\n".join(f"{r['rank']}. {r['username']} — {r['points']} pts" for r in rows)
        last_week_block = f"\n\n🏆 Last week's top 10\n{last_week_lines}"
    else:
        last_week_block = ""

    text = (
        "♟️ Chess Callout Bot\n"
        "/callout +2345678901 — challenge by phone (WhatsApp), 3 free/day\n"
        "/callout <username> — challenge by username (any platform), 3 free/day\n"
        "/callout <handle> <platform> — challenge someone who's never used this bot yet (e.g. /callout bobsmith telegram)\n"
        "/yes — accept a pending callout\n"
        "/no — reject a pending callout\n"
        "/leaderboard — all-time top players\n"
        "/weekly — this week's leaderboard (resets every Monday)\n"
        "/change_username <name> — set your global unique name\n"
        "/link — get a code to link another platform to this account\n"
        "/link <code> — use a code generated on another platform\n"
        "/whoami — show your current username\n"
        "/random — casual instant match (doesn't affect leaderboard)\n"
        "/cancel_random — stop waiting in the matchmaking queue\n"
        "/play_bot — practice game vs the built-in bot (doesn't affect leaderboard)\n"
        "/apply_weekly — join the pool for next week's daily matches\n"
        "/cancel_weekly — leave next week's pool\n"
        "/my_schedule — see today's scheduled match, if any\n"
        "/shop — spend your coins\n"
        "/help — this message"
        f"{last_week_block}"
    )
    return [OutMessage(platform, sender_platform_id, text)]
