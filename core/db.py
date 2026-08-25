"""
Thin data-access layer over Supabase.

Identity model: `username` is the one global, public ID (unique,
case-insensitive). `platform_identities` holds the actual delivery
address(es) for an account — a WhatsApp phone number, a Telegram
chat id, etc. Everything user-facing (/callout target, leaderboard)
uses username; delivery always resolves through platform_identities.
"""
import os
import random
import secrets
import string
from datetime import datetime, timedelta, timezone

from supabase import create_client, Client

from core import config

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


# ------------------------------------------------------------------
# Username helpers
# ------------------------------------------------------------------
def _username_taken(username: str) -> bool:
    res = supabase.table("users").select("id").ilike("username", username).execute()
    return bool(res.data)


def _generate_unique_username(hint: str) -> str:
    """hint = platform-provided display name if any, else a generic base."""
    base = "".join(ch for ch in hint if ch.isalnum()) or "Player"
    base = base[:16]
    candidate = base
    while _username_taken(candidate):
        suffix = "".join(random.choices(string.digits, k=4))
        candidate = f"{base}{suffix}"
    return candidate


# ------------------------------------------------------------------
# Users + platform identities
# ------------------------------------------------------------------
def get_user_by_platform(platform: str, platform_id: str) -> dict | None:
    res = (
        supabase.table("platform_identities")
        .select("*, users(*)")
        .eq("platform", platform)
        .eq("platform_id", platform_id)
        .execute()
    )
    if not res.data:
        return None
    return res.data[0]["users"]


def get_user_by_username(username: str) -> dict | None:
    res = supabase.table("users").select("*").ilike("username", username).execute()
    return res.data[0] if res.data else None


def get_or_create_user(platform: str, platform_id: str, display_name_hint: str) -> dict:
    """Find the account linked to this platform identity, or create a
    brand-new account (with a fresh unique username) and link it."""
    existing = get_user_by_platform(platform, platform_id)
    if existing:
        return existing

    username = _generate_unique_username(display_name_hint)
    new_user = (
        supabase.table("users")
        .insert({"username": username, "coins": config.STARTING_COINS, "points": 0})
        .execute()
        .data[0]
    )
    supabase.table("platform_identities").insert(
        {"user_id": new_user["id"], "platform": platform, "platform_id": platform_id, "is_primary": True}
    ).execute()
    log_transaction(new_user["id"], "signup_bonus", coins_delta=config.STARTING_COINS)
    return new_user


def get_primary_identity(user_id: str) -> dict | None:
    """Where to deliver messages for this account by default."""
    res = (
        supabase.table("platform_identities")
        .select("*")
        .eq("user_id", user_id)
        .eq("is_primary", True)
        .limit(1)
        .execute()
    )
    if res.data:
        return res.data[0]
    # fall back to any linked identity if no primary flag is set for some reason
    res = supabase.table("platform_identities").select("*").eq("user_id", user_id).limit(1).execute()
    return res.data[0] if res.data else None


# ------------------------------------------------------------------
# Account linking (/link) — attach a second platform to an account
# ------------------------------------------------------------------
def create_link_code(user_id: str) -> str:
    code = "".join(random.choices(string.digits, k=6))
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)
    supabase.table("link_codes").insert(
        {"code": code, "user_id": user_id, "expires_at": expires_at.isoformat()}
    ).execute()
    return code


def consume_link_code(code: str, platform: str, platform_id: str) -> tuple[bool, str]:
    """Attach (platform, platform_id) to the account that generated `code`.

    Safety rule: if this platform identity already belongs to a *different*
    account that has actual game history, refuse rather than silently
    merging accounts (prevents accidentally — or maliciously — combining
    stats/points from two real accounts). A fresh, untouched auto-created
    account for that identity is fine to reassign.
    """
    res = supabase.table("link_codes").select("*").eq("code", code).execute()
    if not res.data:
        return False, "Invalid or expired code."
    link = res.data[0]
    if datetime.fromisoformat(link["expires_at"]) < datetime.now(timezone.utc):
        supabase.table("link_codes").delete().eq("code", code).execute()
        return False, "That code has expired. Generate a new one with /link."

    target_user_id = link["user_id"]
    existing_identity = (
        supabase.table("platform_identities")
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
        has_history = (other_user["wins"] + other_user["losses"] + other_user["draws"]) > 0
        if has_history:
            return False, (
                "This platform is already linked to an existing account with game "
                "history, so it can't be auto-merged. Contact support if you need help."
            )

        # Untouched auto-created account for this identity — safe to reassign.
        supabase.table("platform_identities").update(
            {"user_id": target_user_id, "is_primary": False}
        ).eq("id", identity["id"]).execute()
    else:
        supabase.table("platform_identities").insert(
            {"user_id": target_user_id, "platform": platform, "platform_id": platform_id, "is_primary": False}
        ).execute()

    supabase.table("link_codes").delete().eq("code", code).execute()
    linked_user = get_user_by_id(target_user_id)
    return True, linked_user["username"]


def set_username(user_id: str, new_username: str) -> tuple[bool, str]:
    new_username = new_username.strip()
    if not (2 <= len(new_username) <= 20) or not new_username.replace("_", "").isalnum():
        return False, "Username must be 2-20 characters, letters/numbers/underscore only."
    if _username_taken(new_username):
        existing = get_user_by_username(new_username)
        if existing and existing["id"] != user_id:
            return False, "That username is already taken."
    supabase.table("users").update({"username": new_username}).eq("id", user_id).execute()
    return True, "ok"


def get_user_by_id(user_id: str) -> dict:
    return supabase.table("users").select("*").eq("id", user_id).single().execute().data


def adjust_user(user_id: str, coins_delta: int = 0, points_delta: int = 0, **counters) -> None:
    user = get_user_by_id(user_id)
    update = {"coins": user["coins"] + coins_delta, "points": user["points"] + points_delta}
    for field in ("wins", "losses", "draws", "declines"):
        if field in counters:
            update[field] = user[field] + counters[field]
    supabase.table("users").update(update).eq("id", user_id).execute()


def get_leaderboard(limit: int = 10) -> list[dict]:
    return supabase.table("leaderboard").select("*").limit(limit).execute().data


# ------------------------------------------------------------------
# Transactions
# ------------------------------------------------------------------
def log_transaction(user_id: str, txn_type: str, coins_delta: int = 0, points_delta: int = 0, ref_id: str | None = None):
    supabase.table("transactions").insert(
        {"user_id": user_id, "type": txn_type, "coins_delta": coins_delta, "points_delta": points_delta, "ref_id": ref_id}
    ).execute()


# ------------------------------------------------------------------
# Callouts
# ------------------------------------------------------------------
def create_callout(challenger_id: str, opponent_id: str) -> dict:
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=config.CALLOUT_TIMEOUT_SECONDS)
    return (
        supabase.table("callouts")
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
        supabase.table("callouts")
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
        supabase.table("callouts").update({"status": "expired"}).eq("id", callout["id"]).execute()
        # Ignoring a callout gets the same penalty as explicitly
        # declining — your record reflects it either way, so there's
        # no incentive to just go silent instead of sending /no.
        adjust_user(opponent_id, points_delta=config.DECLINE_PENALTY_POINTS, declines=1)
        log_transaction(opponent_id, "decline_penalty", points_delta=config.DECLINE_PENALTY_POINTS, ref_id=callout["id"])
        return None
    return callout


def resolve_callout(callout_id: str, status: str) -> None:
    supabase.table("callouts").update(
        {"status": status, "responded_at": datetime.now(timezone.utc).isoformat()}
    ).eq("id", callout_id).execute()


# ------------------------------------------------------------------
# Games
# ------------------------------------------------------------------
def create_game(callout_id: str | None, white_id: str, black_id: str) -> dict:
    """callout_id is None for games that didn't start from a /callout —
    /random matchmaking and /play_bot both create games directly."""
    token = secrets.token_urlsafe(24)
    game = (
        supabase.table("games")
        .insert({"callout_id": callout_id, "white_id": white_id, "black_id": black_id, "session_token": token})
        .execute()
        .data[0]
    )
    if callout_id:
        supabase.table("callouts").update({"game_id": game["id"]}).eq("id", callout_id).execute()
    return game


def get_game_by_token(token: str) -> dict | None:
    res = supabase.table("games").select("*").eq("session_token", token).execute()
    return res.data[0] if res.data else None


def get_game_by_id(game_id: str) -> dict | None:
    res = supabase.table("games").select("*").eq("id", game_id).execute()
    return res.data[0] if res.data else None


def is_new_player(user_id: str) -> bool:
    """A player is 'new' if they've never finished a game before this one
    (declines don't count — only actual completed games)."""
    user = get_user_by_id(user_id)
    return (user["wins"] + user["losses"] + user["draws"]) == 0


# ------------------------------------------------------------------
# Platform webhooks — for platforms integrated purely via HTTP,
# not via a Python module in core/senders/
# ------------------------------------------------------------------
def get_platform_webhook(platform: str) -> dict | None:
    res = (
        supabase.table("platform_webhooks")
        .select("*")
        .eq("platform", platform)
        .eq("active", True)
        .execute()
    )
    return res.data[0] if res.data else None


def register_platform_webhook(platform: str, outbound_url: str, outbound_secret: str) -> dict:
    return (
        supabase.table("platform_webhooks")
        .upsert({"platform": platform, "outbound_url": outbound_url, "outbound_secret": outbound_secret, "active": True})
        .execute()
        .data[0]
    )


def finish_game(game_id: str, status: str, winner_id: str | None) -> None:
    supabase.table("games").update(
        {"status": status, "winner_id": winner_id, "finished_at": datetime.now(timezone.utc).isoformat()}
    ).eq("id", game_id).execute()


# ------------------------------------------------------------------
# Matchmaking queue (/random)
# ------------------------------------------------------------------
def is_in_queue(user_id: str) -> bool:
    res = supabase.table("matchmaking_queue").select("user_id").eq("user_id", user_id).execute()
    return bool(res.data)


def join_queue(user_id: str) -> None:
    supabase.table("matchmaking_queue").upsert({"user_id": user_id}).execute()


def leave_queue(user_id: str) -> None:
    supabase.table("matchmaking_queue").delete().eq("user_id", user_id).execute()


def find_and_claim_opponent(exclude_user_id: str) -> str | None:
    """Pop the longest-waiting person in the queue who isn't the caller.

    Note: this does a select-then-delete rather than a single atomic
    operation, so two people running /random at the exact same instant
    could theoretically both claim the same opponent. Low-risk for a
    casual game at this scale — worth a proper row lock (e.g. a
    Postgres function with `FOR UPDATE SKIP LOCKED`) if usage grows.
    """
    res = (
        supabase.table("matchmaking_queue")
        .select("user_id")
        .neq("user_id", exclude_user_id)
        .order("joined_at")
        .limit(1)
        .execute()
    )
    if not res.data:
        return None
    opponent_id = res.data[0]["user_id"]
    supabase.table("matchmaking_queue").delete().eq("user_id", opponent_id).execute()
    return opponent_id


# ------------------------------------------------------------------
# Bot opponent (/play_bot)
# ------------------------------------------------------------------
def get_or_create_bot_user() -> dict:
    return get_or_create_user(config.BOT_PLATFORM, config.BOT_PLATFORM_ID, config.BOT_USERNAME)


def get_bot_user_id() -> str | None:
    """Cheap lookup, never creates the account — used by game_results.py
    on every game finish to decide whether to skip stat updates."""
    user = get_user_by_platform(config.BOT_PLATFORM, config.BOT_PLATFORM_ID)
    return user["id"] if user else None
