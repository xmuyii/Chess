"""
The opt-in league: players /apply_weekly for the coming week, and once
that week begins, a NEW pairing runs every day (not just once), so
everyone in the pool gets a match most/all days of the week — not a
single weekly duel. See scripts/run_daily_pairings.py, meant to run
once a day via a Railway Cron Job.

Nobody is drafted into a match they didn't sign up for — /apply_weekly
before the week starts is the only way into the pool.

Pairing tries to avoid rematching the same opponent twice in one week
when the pool is large enough to support it, falling back to a repeat
only when there's no other option (e.g. a pool of 2).
"""
import random
from datetime import date, timedelta

from core import db, config, game_tokens


def get_this_week_start() -> date:
    today = date.today()
    return today - timedelta(days=today.weekday())  # Monday of the current week


def get_next_week_start() -> date:
    return get_this_week_start() + timedelta(days=7)


def sign_up(user_id: str) -> tuple[bool, str]:
    week_start = get_next_week_start()
    created = db.sign_up_for_week(user_id, week_start)
    if not created:
        return False, f"You're already signed up for the week of {week_start.strftime('%b %d')}."
    return True, f"You're in for the week of {week_start.strftime('%b %d')}. You'll get a new match most days that week."


def cancel_signup(user_id: str) -> str:
    week_start = get_next_week_start()
    db.cancel_signup_for_week(user_id, week_start)
    return f"Removed you from the week of {week_start.strftime('%b %d')}."


def get_my_current_match(user_id: str) -> dict | None:
    """Today's (already-paired) scheduled match for this user, if any."""
    return db.get_current_scheduled_match_for_user(user_id, date.today())


def generate_pairings_for_day(week_start: date, match_date: date) -> list[dict]:
    """Pairs up everyone signed up for week_start who doesn't already
    have a match for match_date (idempotent — safe to re-run the same
    day without double-pairing anyone), preferring opponents they
    haven't already played this week."""
    signups = db.get_signups_for_week(week_start)
    pool = {s["user_id"] for s in signups}

    already_matched_today = db.get_scheduled_matches_for_day(match_date)
    already_covered = set()
    for m in already_matched_today:
        already_covered.add(m["player_a_id"])
        if m["player_b_id"]:
            already_covered.add(m["player_b_id"])
    pool -= already_covered

    past_opponents = db.get_past_opponents_this_week(week_start)

    user_ids = list(pool)
    random.shuffle(user_ids)

    created_matches = []
    unpaired = list(user_ids)

    while unpaired:
        a_id = unpaired.pop()
        if not unpaired:
            match = db.create_scheduled_match(week_start, match_date, a_id, None, None, status="bye")
            created_matches.append(match)
            _notify_bye(a_id, match_date)
            break

        # prefer someone a_id hasn't played yet this week; fall back to
        # anyone left if that's not possible (small pool, everyone's
        # already played everyone)
        already_played = past_opponents.get(a_id, set())
        b_index = next((i for i, uid in enumerate(unpaired) if uid not in already_played), None)
        if b_index is None:
            b_index = 0  # forced repeat — pool too small to avoid it
        b_id = unpaired.pop(b_index)

        match = _create_paired_match(week_start, match_date, a_id, b_id)
        created_matches.append(match)

    return created_matches


def _create_paired_match(week_start: date, match_date: date, a_id: str, b_id: str) -> dict:
    white_id, black_id = (a_id, b_id) if random.random() < 0.5 else (b_id, a_id)
    game = db.create_game(callout_id=None, white_id=white_id, black_id=black_id, origin="scheduled")
    match = db.create_scheduled_match(week_start, match_date, a_id, b_id, game["id"], status="paired")

    _send_match_reminder(a_id, b_id, game, match_date)
    return match


def _send_match_reminder(a_id: str, b_id: str, game: dict, match_date: date, prefix: str = "🗓️ Today's scheduled match is ready!") -> None:
    from core.senders import send

    for player_id in (a_id, b_id):
        color = "white" if game["white_id"] == player_id else "black"
        token = game_tokens.generate_player_token(game["id"], player_id, color)
        link = f"{config.GAME_WEB_BASE_URL}/{token}"
        opponent_id = b_id if player_id == a_id else a_id
        opponent = db.get_user_by_id(opponent_id)
        identity = db.get_primary_identity(player_id)
        if not identity:
            continue
        text = (
            f"{prefix}\n"
            f"Today vs {opponent['username']}, you're playing {color}.\n"
            f"Play by 9 PM UTC today or the deadline rules apply.\n"
            f"Play here: {link}"
        )
        send(identity["platform"], identity["platform_id"], text)


def _notify_bye(user_id: str, match_date: date) -> None:
    from core.senders import send

    identity = db.get_primary_identity(user_id)
    if not identity:
        return
    send(
        identity["platform"], identity["platform_id"],
        f"No opponent available today ({match_date.strftime('%b %d')}, odd number of players) — you drew a bye. No penalty, back in the pool tomorrow.",
    )


def send_reminders_for_todays_unfinished_matches(match_date: date) -> int:
    """Nudges both players of any of today's scheduled matches that
    haven't finished yet. Meant to run once daily, a few hours before
    the 9 PM deadline — see scripts/send_weekly_reminders.py."""
    matches = db.get_scheduled_matches_for_day(match_date)
    reminded = 0
    for match in matches:
        if match["status"] != "paired" or not match["game_id"]:
            continue
        game = db.get_game_by_id(match["game_id"])
        if not game or game["status"] != "active":
            continue  # already finished, nothing to remind about
        _send_match_reminder(
            match["player_a_id"], match["player_b_id"], game, match_date,
            prefix="⏰ Reminder: you still have a scheduled match today!",
        )
        reminded += 1
    return reminded
