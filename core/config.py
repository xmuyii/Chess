"""Tunable game constants — change these without touching logic."""
import os

STARTING_COINS = 100
DECLINE_PENALTY_POINTS = -10
CALLOUT_TIMEOUT_SECONDS = 5 * 60  # 5 minutes

WIN_POINTS = 20
LOSS_POINTS = -10
DRAW_POINTS = 2

# Chess clock. GAME_TIME_LIMIT is each player's total time budget for
# the whole game (like a real chess clock) — run out and you lose on
# time. MOVE_TIME_LIMIT is a hard, separate cap on any single move: even
# with plenty of total time left, taking longer than this on one move
# is an automatic loss. This is checked server-side on every board poll
# by EITHER player, so it's enforced consistently regardless of whose
# connection is slow — "it doesn't matter whether internet or speed."
GAME_TIME_LIMIT_SECONDS = 5 * 60
MOVE_TIME_LIMIT_SECONDS = 60

# How long an opponent can go without moving before you can claim a
# forfeit win. Covers "left the bot by accident" / walked away mid-game.
# In practice MOVE_TIME_LIMIT_SECONDS above will usually trigger first
# (it's much shorter and fully automatic) — this stays as a backstop.
FORFEIT_TIMEOUT_MINUTES = 5

# The reserved account used for /play_bot. Not a real platform user —
# it has no platform_identity, so it never receives chat messages;
# its moves are computed server-side in web/game_server.py.
BOT_USERNAME = "ChessBot"
BOT_PLATFORM = "system"
BOT_PLATFORM_ID = "bot-opponent"

# Base URL where the chess web app is hosted. Read from env so the same
# code works locally (http://localhost:5000/game) and once deployed
# (https://<your-app>.up.railway.app/game) without editing source.
# IMPORTANT: this must be set to the SAME value on every service that
# generates game links (e.g. the Telegram worker) and on the web
# service itself, or links will point to the wrong place.
GAME_WEB_BASE_URL = os.environ.get("GAME_WEB_BASE_URL", "http://localhost:5000/game")
