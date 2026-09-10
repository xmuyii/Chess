"""Tunable game constants — change these without touching logic."""
import os

STARTING_COINS = 100
DECLINE_PENALTY_POINTS = -10
CALLOUT_TIMEOUT_SECONDS = 5 * 60  # 5 minutes

# Daily callout faucet: everyone gets this many free callouts per day,
# non-stackable — unused ones don't roll over to tomorrow.
FREE_CALLOUTS_PER_DAY = 3

# Once someone is called out, nobody else can call them out again for
# this long — stops one inactive/asleep player from being spammed by
# many different challengers at once.
CALLOUT_COOLDOWN_HOURS = 2

# Game origins that count toward the leaderboard (weekly AND all-time).
# /random (quick matchmaking) and /play_bot are casual — they never
# touch points regardless of outcome. See core/game_results.py.
QUALIFYING_GAME_ORIGINS = {"callout", "scheduled"}

# Username changes: your first one is free, every one after that costs
# coins — funded by wins, discourages squatting/spam-renaming.
FREE_USERNAME_CHANGES = 1
USERNAME_CHANGE_COST_COINS = 50

# Cold-callout by native platform handle (/callout <handle> <platform>)
# for platforms that can't message a stranger directly — see
# core/cold_invite.py. Only platforms with a real adapter AND deep-link
# support belong here; listing one that isn't wired up would silently
# fail, so this is the source of truth for what's actually supported.
COLD_INVITE_SUPPORTED_PLATFORMS = {"telegram"}
# Needed to build t.me/<bot>?start=... links from core/commands.py,
# which doesn't have direct access to what adapters/telegram_adapter.py
# discovers dynamically via getMe. Set this once you know your bot's
# @username (shown in the adapter's startup log).
TELEGRAM_BOT_USERNAME = os.environ.get("TELEGRAM_BOT_USERNAME", "")

# GOWA (go-whatsapp-web-multidevice) — an UNOFFICIAL WhatsApp integration,
# alternative to the official Cloud API adapter. See README for the real
# tradeoffs (ToS violation, ban risk) before using this in production.
GOWA_BASE_URL = os.environ.get("GOWA_BASE_URL", "")  # e.g. http://localhost:3000 or your hosted GOWA URL
GOWA_WEBHOOK_SECRET = os.environ.get("GOWA_WEBHOOK_SECRET", "secret")  # must match --webhook-secret on GOWA
GOWA_DEVICE_ID = os.environ.get("GOWA_DEVICE_ID", "")  # optional, only needed if GOWA manages multiple numbers
GOWA_BASIC_AUTH_USER = os.environ.get("GOWA_BASIC_AUTH_USER", "")  # optional, if GOWA's --basic-auth is set
GOWA_BASIC_AUTH_PASS = os.environ.get("GOWA_BASIC_AUTH_PASS", "")

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
