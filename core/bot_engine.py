"""
Server-side logic for the bot opponent used by /play_bot. Deliberately
simple — no external engine binary (e.g. Stockfish) required, so there's
nothing extra to install or deploy: prefers captures, otherwise plays a
random legal move. Good enough for a casual bot game.

To upgrade to real playing strength later: python-chess supports UCI
engines via chess.engine.SimpleEngine — you'd download a Stockfish
binary, ship it alongside the app, and swap select_bot_move() for a
call to the engine. Not done here to keep deployment (Railway) simple.
"""
import random
from datetime import datetime, timezone

import chess

from core import db
from core.game_results import apply_game_result
from core.time_control import check_and_apply_timeout, clock_update_after_move


def select_bot_move(board: chess.Board) -> chess.Move:
    legal_moves = list(board.legal_moves)
    captures = [m for m in legal_moves if board.is_capture(m)]
    return random.choice(captures) if captures else random.choice(legal_moves)


def play_bot_move_if_needed(game_id: str) -> dict | None:
    """Call this after every human move, and once right after creating
    a /play_bot game (in case the bot is white and moves first).
    Returns the updated game row if the bot moved, else None."""
    game = db.get_game_by_id(game_id)
    if not game or game["status"] != "active":
        return None

    bot_id = db.get_bot_user_id()
    if not bot_id or bot_id not in (game["white_id"], game["black_id"]):
        return None  # not a bot game

    board = chess.Board(game["fen"])
    bot_is_white = game["white_id"] == bot_id
    if board.turn != bot_is_white:
        return None  # not the bot's turn yet

    move = select_bot_move(board)
    board.push(move)
    new_fen = board.fen()

    status = "active"
    winner_id = None
    if board.is_checkmate():
        status = "white_won" if not board.turn else "black_won"
        winner_id = game["white_id"] if status == "white_won" else game["black_id"]
    elif board.is_stalemate() or board.is_insufficient_material() or board.is_seventyfive_moves():
        status = "draw"

    # Bank the (near-instant) time this "took" and reset the clock so
    # it correctly starts timing the HUMAN's upcoming turn — without
    # this, the human's move-time countdown would start from whenever
    # their opponent's last move was, not from when the bot actually replied.
    clock_update = clock_update_after_move(game, mover_is_white=bot_is_white)
    db.supabase.table("chess_games").update(
        {"fen": new_fen, "updated_at": datetime.now(timezone.utc).isoformat(), **clock_update}
    ).eq("id", game["id"]).execute()

    game["fen"] = new_fen
    game.update(clock_update)
    if status != "active":
        db.finish_game(game["id"], status, winner_id)
        game["status"] = status
        apply_game_result(game)

    return game
