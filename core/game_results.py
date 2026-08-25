"""
Applies a finished game's outcome to both players' points/coins.
Shared by web/game_server.py (when a human's move ends the game) and
core/bot_engine.py (when the bot's move ends the game) so results are
applied identically no matter who made the final move.

Bot games (/play_bot) are practice, not ranked play: NEITHER side gets
points/coins/win-loss stats from them, not just the bot's own account.
This is deliberate — /play_bot exists so nobody's stuck waiting for a
human opponent, not as a way to farm easy leaderboard points against a
weak bot. If a bot game is detected, this function is a no-op entirely.
"""
from core import db, config


def apply_game_result(game: dict) -> None:
    white_id, black_id = game["white_id"], game["black_id"]
    bot_id = db.get_bot_user_id()

    if bot_id and bot_id in (white_id, black_id):
        return  # practice game against the bot — doesn't touch the leaderboard at all

    status = game["status"]
    if status == "white_won":
        winner, loser = white_id, black_id
    elif status == "black_won":
        winner, loser = black_id, white_id
    else:
        winner = loser = None

    if winner:
        db.adjust_user(winner, points_delta=config.WIN_POINTS, wins=1)
        db.log_transaction(winner, "win_reward", points_delta=config.WIN_POINTS, ref_id=game["id"])
        db.adjust_user(loser, points_delta=config.LOSS_POINTS, losses=1)
        db.log_transaction(loser, "loss_penalty", points_delta=config.LOSS_POINTS, ref_id=game["id"])
    else:
        for uid in (white_id, black_id):
            db.adjust_user(uid, points_delta=config.DRAW_POINTS, draws=1)
