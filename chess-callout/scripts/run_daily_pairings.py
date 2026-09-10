"""
Run once a day, e.g. 8 AM UTC. Does two things:

1. Sweeps every still-active callout/scheduled game whose deadline has
   passed with nobody ever moving, and voids it. This is the backstop
   for games where NEITHER player ever revisits the link at all — the
   usual poll-triggered self-heal (in web/game_server.py) can't fire if
   nobody ever polls, so this catches it once a day regardless.
2. Pairs up today's matches for everyone in the current week's league
   pool. Safe to re-run the same day (idempotent) — see
   core/weekly_league.py.

Deploy as a Railway Cron Job: Custom Start Command
`python -m scripts.run_daily_pairings`, Cron Schedule e.g. `0 8 * * *`.
Needs SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, GAME_TOKEN_SECRET,
GAME_WEB_BASE_URL — same as every other service in this repo.
"""
from dotenv import load_dotenv
load_dotenv()

from datetime import date

from core import db
from core.time_control import check_and_void_if_missed_deadline
from core.weekly_league import generate_pairings_for_day, get_this_week_start

if __name__ == "__main__":
    active_games = db.get_active_qualifying_games()
    voided = 0
    for game in active_games:
        result = check_and_void_if_missed_deadline(game)
        if result["status"] == "aborted":
            voided += 1
    print(f"Deadline sweep: checked {len(active_games)} active qualifying games, voided {voided} with no activity.")

    today = date.today()
    week = get_this_week_start()
    matches = generate_pairings_for_day(week, today)
    paired = sum(1 for m in matches if m["status"] == "paired")
    byes = sum(1 for m in matches if m["status"] == "bye")
    print(f"Today's pairings ({today}): {paired} paired matches, {byes} bye{'s' if byes != 1 else ''}.")
