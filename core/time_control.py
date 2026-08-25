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
