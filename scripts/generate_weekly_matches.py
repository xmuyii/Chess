"""
Pairs everyone signed up for next week's league and sends each player
their link. Run this ONCE, right when signups should close.

Deploy as a Railway Cron Job: create a new service pointed at this
repo, Custom Start Command `python -m scripts.generate_weekly_matches`,
Settings -> Cron Schedule e.g. `0 20 * * 0` (Sunday 20:00 UTC — adjust
for whenever you want signups to close and the coming week to start).
Needs the same env vars as the other services: SUPABASE_URL,
SUPABASE_SERVICE_ROLE_KEY, GAME_TOKEN_SECRET, GAME_WEB_BASE_URL.

Must be run via `-m` from the project root, same as every other
entrypoint in this repo — see README for why.
"""
from dotenv import load_dotenv
load_dotenv()

from core.weekly_league import generate_pairings_for_week, get_next_week_start

if __name__ == "__main__":
    week = get_next_week_start()
    matches = generate_pairings_for_week(week)
    paired = sum(1 for m in matches if m["status"] == "paired")
    byes = sum(1 for m in matches if m["status"] == "bye")
    print(f"Generated {len(matches)} match records for week of {week} "
          f"({paired} paired matches, {byes} bye{'s' if byes != 1 else ''}).")
