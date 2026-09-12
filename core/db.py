"""
Thin data-access layer over Supabase.

Identity model (post-integration): `players` is a SHARED table owned
by another game, not by chess-callout. `players.user_id` (text) is the
one identity used across every game on this platform; `players.username`
is already shared too — chess never creates or owns a username column.
Chess-specific stats live as `chess_`-prefixed columns directly on
`players` (chess_coins, chess_all_time_points, chess_wins, etc.),
matching the existing `fusion_*` / `trivia_*` convention already used
by the other games sharing this database.

Every other chess table (chess_callouts, chess_games, ...) is owned by
chess-callout and prefixed accordingly, to avoid any collision with
the sprawling existing schema.
"""
import os
import random
import secrets
import string
import uuid
from datetime import datetime, timedelta, timezone

from supabase import create_client, Client

from core import config

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Every NOT NULL column on the shared `players` table that chess-callout
# doesn't own, with a safe zero-equivalent value. Supplied explicitly on
# every new-player insert so this doesn't depend on database-level
# defaults existing for someone else's game columns — if those defaults
# are missing, an insert with these fields present still succeeds.
_OTHER_GAME_NOT_NULL_DEFAULTS = {
    "all_time_points": 0,
    "weekly_points": 0,
    "total_words": 0,
    "bitcoin": 0,
    "xp": 0,
    "war_points": 0,
    "wins": 0,
    "losses": 0,
    "kings_captured": 0,
    "times_captured": 0,
    "combo_count": 0,
    "credits": 0,
    "gold": 0,
    "energy": 0,
    "total_power": 0,
    "teleport_charges": 0,
    "base_shielded": False,
    "login_streak": 0,
}


# ------------------------------------------------------------------
# Username helpers
# ------------------------------------------------------------------
def _username_taken(username: str) -> bool:
    res = supabase.table("players").select("user_id").ilike("username", username).execute()
    return bool(res.data)


def _generate_unique_username(hint: str) -> str:
    base = "".join(ch for ch in hint if ch.isalnum()) or "Player"
    base = base[:16]
    candidate = base
    while _username_taken(candidate):
        suffix = "".join(random.choices(string.digits, k=4))
        candidate = f"{base}{suffix}"
    return candidate


def _create_new_player_row(username: str) -> dict:
    """Creates a brand-new `players` row for someone chess-callout has
    never seen before (and who may never have touched the other
    game(s) sharing this database either). Explicitly supplies every
    NOT NULL column that belongs to those other games — see
    _OTHER_GAME_NOT_NULL_DEFAULTS above — plus chess's own starting
    values, so this doesn't depend on unverified database defaults."""
    new_user_id = str(uuid.uuid4())
    row = {
        "user_id": new_user_id,
        "username": username,
        "chess_coins": config.STARTING_COINS,
        "chess_all_time_points": 0,
        **_OTHER_GAME_NOT_NULL_DEFAULTS,
    }
    return supabase.table("players").insert(row).execute().data[0]


# ------------------------------------------------------------------
# Users (players) + platform identities
# ------------------------------------------------------------------
def get_user_by_platform(platform: str, platform_id: str) -> dict | None:
    res = (
        supabase.table("chess_platform_identities")
        .select("*, players(*)")
        .eq("platform", platform)
        .eq("platform_id", platform_id)
        .execute()
    )
    if not res.data:
        return None
    return res.data[0]["players"]


def get_user_by_username(username: str) -> dict | None:
    res = supabase.table("players").select("*").ilike("username", username).execute()
    return res.data[0] if res.data else None


def find_existing_player_by_raw_id(user_id: str) -> dict | None:
    """Non-throwing existence check (unlike get_user_by_id, which
    assumes the row exists) — used for cross-game identity recognition,
    where 'not found' is the common, expected outcome."""
    res = supabase.table("players").select("*").eq("user_id", user_id).execute()
    return res.data[0] if res.data else None


def get_or_create_user(platform: str, platform_id: str, display_name_hint: str) -> dict:
    existing = get_user_by_platform(platform, platform_id)
    if existing:
        return existing

    # Telegram is a special case: the other game(s) sharing this
    # database are Telegram-based, and their players.user_id IS the
    # raw Telegram chat ID directly — confirmed against real sample
    # data, no transformation or prefix involved. So before creating a
    # brand-new account, check whether this exact chat ID already
    # belongs to an existing player from the other game, and link to
    # THAT account instead of creating a duplicate identity for the
    # same person with an ugly auto-generated username.
    if platform == "telegram":
        existing_player = find_existing_player_by_raw_id(platform_id)
        if existing_player:
            supabase.table("chess_platform_identities").insert(
                {"user_id": existing_player["user_id"], "platform": platform, "platform_id": platform_id, "is_primary": True}
            ).execute()
            return existing_player

    username = _generate_unique_username(display_name_hint)
    new_player = _create_new_player_row(username)
    supabase.table("chess_platform_identities").insert(
        {"user_id": new_player["user_id"], "platform": platform, "platform_id": platform_id, "is_primary": True}
    ).execute()
    log_transaction(new_player["user_id"], "signup_bonus", coins_delta=config.STARTING_COINS)
    return new_player


def get_user_by_id(user_id: str) -> dict:
    """Despite the name (kept for minimal disruption to existing call
    sites), this looks up by players.user_id, not the internal
    bigint players.id."""
    return supabase.table("players").select("*").eq("user_id", user_id).single().execute().data


def get_primary_identity(user_id: str) -> dict | None:
    res = (
        supabase.table("chess_platform_identities")
        .select("*")
        .eq("user_id", user_id)
        .eq("is_primary", True)
        .limit(1)
        .execute()
    )
    if res.data:
        return res.data[0]
    res = supabase.table("chess_platform_identities").select("*").eq("user_id", user_id).limit(1).execute()
    return res.data[0] if res.data else None


def set_username(user_id: str, new_username: str) -> tuple[bool, str]:
    new_username = new_username.strip()
    if not (2 <= len(new_username) <= 20) or not new_username.replace("_", "").isalnum():
        return False, "Username must be 2-20 characters, letters/numbers/underscore only."
    if _username_taken(new_username):
        existing = get_user_by_username(new_username)
        if existing and existing["user_id"] != user_id:
            return False, "That username is already taken."
    supabase.table("players").update({"username": new_username}).eq("user_id", user_id).execute()
    return True, "ok"


def record_username_change(user_id: str, cost_coins: int) -> None:
    user = get_user_by_id(user_id)
    update = {"chess_username_changes_used": user["chess_username_changes_used"] + 1}
    if cost_coins > 0:
        update["chess_coins"] = user["chess_coins"] - cost_coins
    supabase.table("players").update(update).eq("user_id", user_id).execute()
    if cost_coins > 0:
        log_transaction(user_id, "shop_purchase", coins_delta=-cost_coins, points_delta=0)


def adjust_user(user_id: str, coins_delta: int = 0, points_delta: int = 0, **counters) -> None:
    user = get_user_by_id(user_id)
    update = {
        "chess_coins": user["chess_coins"] + coins_delta,
        "chess_all_time_points": user["chess_all_time_points"] + points_delta,
    }
    field_map = {"wins": "chess_wins", "losses": "chess_losses", "draws": "chess_draws", "declines": "chess_declines"}
    for key, delta in counters.items():
        col = field_map.get(key)
        if col:
            update[col] = user[col] + delta
    supabase.table("players").update(update).eq("user_id", user_id).execute()


def get_leaderboard(limit: int = 10) -> list[dict]:
    return supabase.table("chess_alltime_leaderboard").select("*").limit(limit).execute().data


def get_weekly_leaderboard(limit: int = 10) -> list[dict]:
    return supabase.table("chess_weekly_leaderboard").select("*").limit(limit).execute().data


def get_last_week_leaderboard(limit: int = 10) -> list[dict]:
    return supabase.table("chess_last_week_leaderboard").select("*").limit(limit).execute().data


def is_new_player(user_id: str) -> bool:
    user = get_user_by_id(user_id)
    return (user["chess_wins"] + user["chess_losses"] + user["chess_draws"]) == 0


# ------------------------------------------------------------------
# Transactions
# ------------------------------------------------------------------
def log_transaction(user_id: str, txn_type: str, coins_delta: int = 0, points_delta: int = 0, ref_id: str | None = None):
    supabase.table("chess_transactions").insert(
        {"user_id": user_id, "type": txn_type, "coins_delta": coins_delta, "points_delta": points_delta, "ref_id": ref_id}
    ).execute()


# ------------------------------------------------------------------
# Callout faucet (3/day, non-stackable) and target cooldown (2h)
# ------------------------------------------------------------------
def check_and_consume_callout(user_id: str) -> tuple[bool, str]:
    user = get_user_by_id(user_id)
    today = datetime.now(timezone.utc).date()
    reset_date = user["chess_callouts_reset_date"]
    if isinstance(reset_date, str):
        reset_date = datetime.fromisoformat(reset_date).date()

    used_today = user["chess_callouts_used_today"]
    if reset_date < today:
        used_today = 0

    if used_today >= config.FREE_CALLOUTS_PER_DAY:
        return False, f"You've used all {config.FREE_CALLOUTS_PER_DAY} of your free callouts today. More available tomorrow."

    supabase.table("players").update(
        {"chess_callouts_used_today": used_today + 1, "chess_callouts_reset_date": today.isoformat()}
    ).eq("user_id", user_id).execute()
    return True, "ok"


def check_callout_cooldown(target_user_id: str) -> tuple[bool, str]:
    user = get_user_by_id(target_user_id)
    last_called = user.get("chess_last_called_out_at")
    if not last_called:
        return True, "ok"
    last_called_at = datetime.fromisoformat(last_called)
    cooldown_ends = last_called_at + timedelta(hours=config.CALLOUT_COOLDOWN_HOURS)
    now = datetime.now(timezone.utc)
    if now < cooldown_ends:
        remaining_minutes = int((cooldown_ends - now).total_seconds() / 60)
        return False, f"{user['username']} was called out recently — try again in {remaining_minutes} min."
    return True, "ok"


def mark_called_out(target_user_id: str) -> None:
    supabase.table("players").update({"chess_last_called_out_at": datetime.now(timezone.utc).isoformat()}).eq(
        "user_id", target_user_id
    ).execute()


# ------------------------------------------------------------------
# Account linking (/link)
# ------------------------------------------------------------------
def create_link_code(user_id: str) -> str:
    code = "".join(random.choices(string.digits, k=6))
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=config.LINK_CODE_TTL_MINUTES if hasattr(config, "LINK_CODE_TTL_MINUTES") else 10)
    supabase.table("chess_link_codes").insert(
        {"code": code, "user_id": user_id, "expires_at": expires_at.isoformat()}
    ).execute()
    return code


def consume_link_code(code: str, platform: str, platform_id: str) -> tuple[bool, str]:
    res = supabase.table("chess_link_codes").select("*").eq("code", code).execute()
    if not res.data:
        return False, "Invalid or expired code."
    link = res.data[0]
    if datetime.fromisoformat(link["expires_at"]) < datetime.now(timezone.utc):
        supabase.table("chess_link_codes").delete().eq("code", code).execute()
        return False, "That code has expired. Generate a new one with /link."

    target_user_id = link["user_id"]
    existing_identity = (
        supabase.table("chess_platform_identities")
        .select("*")
        .eq("platform", platform)
        .eq("platform_id", platform_id)
        .execute()
        .data
    )

    if existing_identity:
        identity = existing_identity[0]
        if identity["user_id"] == target_user_id:
            return False, "This platform is already linked to that account."
        other_user = get_user_by_id(identity["user_id"])
        has_history = (other_user["chess_wins"] + other_user["chess_losses"] + other_user["chess_draws"]) > 0
        if has_history:
            return False, (
                "This platform is already linked to an existing account with game "
                "history, so it can't be auto-merged. Contact support if you need help."
            )
        supabase.table("chess_platform_identities").update(
            {"user_id": target_user_id, "is_primary": False}
        ).eq("id", identity["id"]).execute()
    else:
        supabase.table("chess_platform_identities").insert(
            {"user_id": target_user_id, "platform": platform, "platform_id": platform_id, "is_primary": False}
        ).execute()

    supabase.table("chess_link_codes").delete().eq("code", code).execute()
    linked_user = get_user_by_id(target_user_id)
    return True, linked_user["username"]


# ------------------------------------------------------------------
# Callouts
# ------------------------------------------------------------------
def create_callout(challenger_id: str, opponent_id: str) -> dict:
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=config.CALLOUT_TIMEOUT_SECONDS)
    return (
        supabase.table("chess_callouts")
        .insert(
            {
                "challenger_id": challenger_id,
                "opponent_id": opponent_id,
                "status": "pending",
                "expires_at": expires_at.isoformat(),
            }
        )
        .execute()
        .data[0]
    )


def get_pending_callout_for(opponent_id: str) -> dict | None:
    res = (
        supabase.table("chess_callouts")
        .select("*")
        .eq("opponent_id", opponent_id)
        .eq("status", "pending")
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    if not res.data:
        return None
    callout = res.data[0]
    if datetime.fromisoformat(callout["expires_at"]) < datetime.now(timezone.utc):
        supabase.table("chess_callouts").update({"status": "expired"}).eq("id", callout["id"]).execute()
        adjust_user(opponent_id, points_delta=config.DECLINE_PENALTY_POINTS, declines=1)
        log_transaction(opponent_id, "decline_penalty", points_delta=config.DECLINE_PENALTY_POINTS, ref_id=callout["id"])
        return None
    return callout


def resolve_callout(callout_id: str, status: str) -> None:
    supabase.table("chess_callouts").update(
        {"status": status, "responded_at": datetime.now(timezone.utc).isoformat()}
    ).eq("id", callout_id).execute()


# ------------------------------------------------------------------
# Games
# ------------------------------------------------------------------
def create_game(callout_id: str | None, white_id: str, black_id: str, origin: str) -> dict:
    token = secrets.token_urlsafe(24)
    game = (
        supabase.table("chess_games")
        .insert({"callout_id": callout_id, "white_id": white_id, "black_id": black_id, "session_token": token, "origin": origin})
        .execute()
        .data[0]
    )
    if callout_id:
        supabase.table("chess_callouts").update({"game_id": game["id"]}).eq("id", callout_id).execute()
    return game


def get_game_by_token(token: str) -> dict | None:
    res = supabase.table("chess_games").select("*").eq("session_token", token).execute()
    return res.data[0] if res.data else None


def get_game_by_id(game_id: str) -> dict | None:
    res = supabase.table("chess_games").select("*").eq("id", game_id).execute()
    return res.data[0] if res.data else None


def finish_game(game_id: str, status: str, winner_id: str | None) -> None:
    supabase.table("chess_games").update(
        {"status": status, "winner_id": winner_id, "finished_at": datetime.now(timezone.utc).isoformat()}
    ).eq("id", game_id).execute()


def get_active_qualifying_games() -> list[dict]:
    return (
        supabase.table("chess_games")
        .select("*")
        .eq("status", "active")
        .in_("origin", list(config.QUALIFYING_GAME_ORIGINS))
        .execute()
        .data
    )


# ------------------------------------------------------------------
# Matchmaking queue (/random)
# ------------------------------------------------------------------
def is_in_queue(user_id: str) -> bool:
    res = supabase.table("chess_matchmaking_queue").select("user_id").eq("user_id", user_id).execute()
    return bool(res.data)


def join_queue(user_id: str) -> None:
    supabase.table("chess_matchmaking_queue").upsert({"user_id": user_id}).execute()


def leave_queue(user_id: str) -> None:
    supabase.table("chess_matchmaking_queue").delete().eq("user_id", user_id).execute()


def find_and_claim_opponent(exclude_user_id: str) -> str | None:
    res = (
        supabase.table("chess_matchmaking_queue")
        .select("user_id")
        .neq("user_id", exclude_user_id)
        .order("joined_at")
        .limit(1)
        .execute()
    )
    if not res.data:
        return None
    opponent_id = res.data[0]["user_id"]
    supabase.table("chess_matchmaking_queue").delete().eq("user_id", opponent_id).execute()
    return opponent_id


# ------------------------------------------------------------------
# Bot opponent (/play_bot)
# ------------------------------------------------------------------
def get_or_create_bot_user() -> dict:
    return get_or_create_user(config.BOT_PLATFORM, config.BOT_PLATFORM_ID, config.BOT_USERNAME)


def get_bot_user_id() -> str | None:
    user = get_user_by_platform(config.BOT_PLATFORM, config.BOT_PLATFORM_ID)
    return user["user_id"] if user else None


# ------------------------------------------------------------------
# Platform webhooks
# ------------------------------------------------------------------
def get_platform_webhook(platform: str) -> dict | None:
    res = supabase.table("chess_platform_webhooks").select("*").eq("platform", platform).eq("active", True).execute()
    return res.data[0] if res.data else None


def register_platform_webhook(platform: str, outbound_url: str, outbound_secret: str) -> dict:
    return (
        supabase.table("chess_platform_webhooks")
        .upsert({"platform": platform, "outbound_url": outbound_url, "outbound_secret": outbound_secret, "active": True})
        .execute()
        .data[0]
    )


# ------------------------------------------------------------------
# Pending invites — cold-callout by native handle
# ------------------------------------------------------------------
def create_pending_invite(target_platform: str, target_handle: str, created_by: str) -> tuple[dict, str]:
    username_hint = "".join(ch for ch in target_handle if ch.isalnum()) + target_platform
    stub_username = _generate_unique_username(username_hint)
    stub_user = _create_new_player_row(stub_username)

    claim_code = "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
    supabase.table("chess_pending_invites").insert(
        {
            "claim_code": claim_code,
            "user_id": stub_user["user_id"],
            "target_platform": target_platform,
            "target_handle": target_handle,
            "created_by": created_by,
        }
    ).execute()
    return stub_user, claim_code


def get_pending_invite(claim_code: str) -> dict | None:
    res = supabase.table("chess_pending_invites").select("*").eq("claim_code", claim_code).eq("claimed", False).execute()
    return res.data[0] if res.data else None


def claim_pending_invite(claim_code: str, platform: str, platform_id: str) -> dict | None:
    invite = get_pending_invite(claim_code)
    if not invite:
        return None

    supabase.table("chess_platform_identities").insert(
        {"user_id": invite["user_id"], "platform": platform, "platform_id": platform_id, "is_primary": True}
    ).execute()
    supabase.table("chess_pending_invites").update(
        {"claimed": True, "claimed_at": datetime.now(timezone.utc).isoformat()}
    ).eq("claim_code", claim_code).execute()
    return invite


# ------------------------------------------------------------------
# Weekly league — signups + daily pairings
# ------------------------------------------------------------------
def sign_up_for_week(user_id: str, week_start) -> bool:
    try:
        supabase.table("chess_weekly_signups").insert({"user_id": user_id, "week_start": str(week_start)}).execute()
        return True
    except Exception:
        return False


def cancel_signup_for_week(user_id: str, week_start) -> None:
    supabase.table("chess_weekly_signups").delete().eq("user_id", user_id).eq("week_start", str(week_start)).execute()


def get_signups_for_week(week_start) -> list[dict]:
    return supabase.table("chess_weekly_signups").select("*").eq("week_start", str(week_start)).execute().data


def create_scheduled_match(week_start, match_date, player_a_id: str, player_b_id: str | None, game_id: str | None, status: str) -> dict:
    return (
        supabase.table("chess_scheduled_matches")
        .insert(
            {
                "week_start": str(week_start),
                "match_date": str(match_date),
                "player_a_id": player_a_id,
                "player_b_id": player_b_id,
                "game_id": game_id,
                "status": status,
            }
        )
        .execute()
        .data[0]
    )


def get_scheduled_matches_for_day(match_date) -> list[dict]:
    return supabase.table("chess_scheduled_matches").select("*").eq("match_date", str(match_date)).execute().data


def get_scheduled_matches_for_week(week_start) -> list[dict]:
    return supabase.table("chess_scheduled_matches").select("*").eq("week_start", str(week_start)).execute().data


def get_past_opponents_this_week(week_start) -> dict[str, set[str]]:
    matches = get_scheduled_matches_for_week(week_start)
    opponents: dict[str, set[str]] = {}
    for m in matches:
        if m["status"] != "paired" or not m["player_b_id"]:
            continue
        a, b = m["player_a_id"], m["player_b_id"]
        opponents.setdefault(a, set()).add(b)
        opponents.setdefault(b, set()).add(a)
    return opponents


def get_current_scheduled_match_for_user(user_id: str, match_date) -> dict | None:
    res = (
        supabase.table("chess_scheduled_matches")
        .select("*")
        .eq("match_date", str(match_date))
        .or_(f"player_a_id.eq.{user_id},player_b_id.eq.{user_id}")
        .execute()
    )
    return res.data[0] if res.data else None
