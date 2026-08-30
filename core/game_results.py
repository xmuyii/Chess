"""
Applies a finished game's outcome to both players' points/coins.
Shared by web/game_server.py (when a human's move ends the game) and
core/bot_engine.py (when the bot's move ends the game) so results are
applied identically no matter who made the final move.

Only games whose origin is in QUALIFYING_GAME_ORIGINS (callout,
scheduled) ever touch points — /random (casual matchmaking) and
/play_bot (practice) never do, on the weekly OR all-time leaderboard.
This is also what makes the weekly "clean slate" leaderboard work with
zero extra bookkeeping: it just sums this week's transactions, and
since non-qualifying games never write a scored transaction, the sum
is automatically scoped correctly — see the weekly_leaderboard view
in sql/schema.sql.
"""
from core import db, config


def apply_game_result(game: dict) -> None:
    if game.get("origin") not in config.QUALIFYING_GAME_ORIGINS:
        return  # casual game (random/bot) — doesn't touch the leaderboard at all

    white_id, black_id = game["white_id"], game["black_id"]
    status = game["status"]

    if status == "aborted":
        return  # voided — nobody engaged, so nobody wins/loses/draws

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
