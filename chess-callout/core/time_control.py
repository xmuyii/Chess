"""
Server-side chess clock enforcement — see GAME_TIME_LIMIT_SECONDS and
MOVE_TIME_LIMIT_SECONDS in core/config.py.

Critically: this is checked from EITHER player's /state poll, not just
the player who's out of time. Since the board polls automatically every
3 seconds, a timeout gets caught by whichever player's connection is
still working — the tardy player's own bad connection doesn't grant
them extra time, and the waiting player doesn't need to click anything.
This is what "doesn't matter whose internet is slow, it's a constant
rule" means in practice: the threshold is wall-clock time measured
against a server timestamp, never adjusted for anyone's connection.

One real limitation: if BOTH players close their tabs and nobody polls
again, nothing checks the clock until someone comes back — there's no
background scheduler in this architecture. Acceptable for now; a
periodic server-side sweep would close that gap if it ever matters.
"""
from datetime import datetime, timezone

import chess

from core import db, config
from core.game_results import apply_game_result

# 9 PM UTC — the daily cutoff for qualifying (callout/scheduled) games.
# Each game's own deadline is the next occurrence of this after its
# creation time: today's 9 PM if created earlier that day, tomorrow's
# 9 PM if created after 9 PM.
DEADLINE_HOUR_UTC = 21


def get_deadline_for_game(created_at: datetime) -> datetime:
    today_9pm = created_at.replace(hour=DEADLINE_HOUR_UTC, minute=0, second=0, microsecond=0)
    if created_at < today_9pm:
        return today_9pm
    from datetime import timedelta
    return today_9pm + timedelta(days=1)


def ply_count(fen: str) -> int:
    """How many half-moves (plies) have been played, derived from the
    FEN's own move counters. 0 = nobody's moved. 1 = only white has
    (it's black's turn but black hasn't gone). 2+ = both sides have
    moved at least once each."""
    board = chess.Board(fen)
    return 2 * (board.fullmove_number - 1) + (0 if board.turn else 1)


def check_and_void_if_missed_deadline(game: dict) -> dict:
    """If a qualifying game's daily deadline has passed and NEITHER
    side ever moved, void it (status='aborted', nobody wins/loses).
    Games where at least one side moved are left for the claim-win
    button instead — see deadline_noshow_claim_eligible() below.
    Self-healing, same pattern as the bot-move and chess-clock checks:
    runs on every /state poll, so it fires automatically the next time
    either player's browser checks in, without needing a cron job for
    this specific case."""
    if game["status"] != "active" or game.get("origin") not in config.QUALIFYING_GAME_ORIGINS:
        return game

    created_at = datetime.fromisoformat(game["created_at"])
    deadline = get_deadline_for_game(created_at)
    if datetime.now(timezone.utc) < deadline:
        return game
    if ply_count(game["fen"]) > 0:
        return game  # somebody engaged — not a "nobody showed up" case

    db.finish_game(game["id"], "aborted", None)
    game["status"] = "aborted"
    apply_game_result(game)
    return game


def deadline_noshow_claim_eligible(game: dict, claimant_color: str) -> tuple[bool, str]:
    """Whether claimant_color can claim a win because today's deadline
    passed and they're the only one who ever moved — the "one player is
    ready, the other doesn't show" case. This is a manual claim (a
    button), not auto-resolved, unlike the ply==0 case above."""
    if game.get("origin") not in config.QUALIFYING_GAME_ORIGINS:
        return False, "This game type doesn't use the daily deadline rule."

    created_at = datetime.fromisoformat(game["created_at"])
    deadline = get_deadline_for_game(created_at)
    now = datetime.now(timezone.utc)
    if now < deadline:
        remaining = deadline - now
        hours, minutes = divmod(int(remaining.total_seconds()) // 60, 60)
        return False, f"Today's deadline hasn't passed yet ({hours}h {minutes}m left)."

    plies = ply_count(game["fen"])
    if plies == 0:
        return False, "Neither of you played — nothing to claim."
    if plies >= 2:
        return False, "Both sides have moved — use the regular inactivity claim instead."

    mover_color = "white"  # after exactly 1 ply, white is who moved
    if claimant_color != mover_color:
        return False, f"You haven't made a move yet — {mover_color} is the one who can claim this."
    return True, "ok"


def _elapsed_seconds(since_iso: str) -> float:
    since = datetime.fromisoformat(since_iso)
    return (datetime.now(timezone.utc) - since).total_seconds()


def check_and_apply_timeout(game: dict) -> dict:
    """If the side currently to move has exceeded either the per-move
    cap or their total game budget, end the game as a loss for them.
    Returns the (possibly updated) game dict either way."""
    if game["status"] != "active":
        return game

    board = chess.Board(game["fen"])
    mover_is_white = board.turn
    elapsed_this_turn = _elapsed_seconds(game["turn_started_at"])
    used_so_far_ms = game["white_time_used_ms"] if mover_is_white else game["black_time_used_ms"]
    total_used_seconds = (used_so_far_ms / 1000) + elapsed_this_turn

    timed_out = (
        elapsed_this_turn > config.MOVE_TIME_LIMIT_SECONDS
        or total_used_seconds > config.GAME_TIME_LIMIT_SECONDS
    )
    if not timed_out:
        return game

    status = "black_won" if mover_is_white else "white_won"
    winner_id = game["black_id"] if mover_is_white else game["white_id"]
    db.finish_game(game["id"], status, winner_id)
    game["status"] = status
    apply_game_result(game)
    return game


def clock_update_after_move(game: dict, mover_is_white: bool) -> dict:
    """Fields to merge into the games-row update right after a move is
    applied: banks the time that move took against the mover's total
    budget, and resets the clock so it starts timing the other side."""
    elapsed_ms = int(_elapsed_seconds(game["turn_started_at"]) * 1000)
    field = "white_time_used_ms" if mover_is_white else "black_time_used_ms"
    return {
        field: game[field] + elapsed_ms,
        "turn_started_at": datetime.now(timezone.utc).isoformat(),
    }


def clock_display_fields(game: dict) -> dict:
    """Values for the /state response so the frontend can render live
    countdown clocks without re-deriving this logic client-side."""
    board = chess.Board(game["fen"])
    mover_is_white = board.turn
    elapsed_this_turn_ms = int(_elapsed_seconds(game["turn_started_at"]) * 1000)

    white_used_ms = game["white_time_used_ms"] + (elapsed_this_turn_ms if mover_is_white else 0)
    black_used_ms = game["black_time_used_ms"] + (elapsed_this_turn_ms if not mover_is_white else 0)
    limit_ms = config.GAME_TIME_LIMIT_SECONDS * 1000

    turn_started_at = datetime.fromisoformat(game["turn_started_at"])
    move_deadline_at = turn_started_at.timestamp() * 1000 + config.MOVE_TIME_LIMIT_SECONDS * 1000

    return {
        "white_remaining_ms": max(0, limit_ms - white_used_ms),
        "black_remaining_ms": max(0, limit_ms - black_used_ms),
        "move_deadline_at_ms": move_deadline_at,
    }
