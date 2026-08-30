"""
Run once a day, a few hours before the 9 PM UTC deadline (e.g. 6 PM
UTC) — nudges anyone with an unfinished scheduled match today.

Deploy as a Railway Cron Job: Custom Start Command
`python -m scripts.send_weekly_reminders`, Cron Schedule `0 18 * * *`.
Same env vars as run_daily_pairings.py.
"""
from dotenv import load_dotenv
load_dotenv()

from datetime import date

from core.weekly_league import send_reminders_for_todays_unfinished_matches

if __name__ == "__main__":
    today = date.today()
    count = send_reminders_for_todays_unfinished_matches(today)
    print(f"Sent reminders for {count} unfinished scheduled match(es) today ({today}).")
