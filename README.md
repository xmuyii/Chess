# Chess Callout Bot

A cross-platform chess callout game. Challenge someone, they accept or decline
in chat, and if they accept they play in a web-based board. Supabase is the
single source of truth for users, callouts, games, and points — so any number
of chat-platform adapters can share the same leaderboard.

## Identity model

- **`username` is the one global, public ID** — unique (case-insensitive)
  across every platform. It's what shows on the leaderboard and what
  `/callout <username>` targets.
- **Phone numbers and chat IDs are just delivery addresses**, stored in
  `platform_identities`, linked to an account. They're never shown publicly.
- **`/callout +2345678901`** is a WhatsApp-specific shortcut: it lets you
  reach someone purely by phone number, even if they've never used the bot —
  an account gets created for them on the spot with a placeholder username.
  This is the acquisition/virality path.
- **`/callout <username>`** works from *any* platform, once the target has
  set a username. The bot looks up their account and delivers the callout
  to whichever platform they're actually linked to — a Telegram user can
  call out a WhatsApp user this way, and vice versa.
- Every new account gets an auto-generated placeholder username at sign-up
  (from their platform display name where available). `/change_username`
  lets them claim their permanent one — first-come, first-served, enforced
  as a unique index in Postgres.
- **`/link`** attaches a second platform to an existing account: run `/link`
  with no arguments on your main platform to get a 6-digit code (valid 10
  minutes), then send `/link <code>` from the other platform. If that other
  platform already had an untouched auto-created account (no games played),
  it's silently reassigned to your real account. If it already has game
  history, linking is refused rather than silently merging stats — that
  needs a manual/support path.

## Game link security — verifying color and player identity

Each accepted callout produces **two separate links**, not one shared
link — one for white, one for black (`core/game_tokens.py`). Each link
is a signed token encoding `(game_id, user_id, color)`, so:

- opening the link proves who you are and which color you're playing —
  the move API rejects a request if the token's color doesn't match
  whose turn it actually is in the DB, so white's link literally cannot
  move black's pieces, even by hand-editing the request
- the board page shows both players' usernames and a **NEW** badge next
  to anyone who's never finished a game before (`db.is_new_player`) —
  useful context before you commit to a match with a stranger
- tokens are stateless (no DB round-trip needed to verify one) and
  signed with `GAME_TOKEN_SECRET` — treat that env var like a password;
  anyone with it could forge a valid-looking link for any game/user/color

## The chess board — not Lichess, a fully custom interface

`web/game_server.py` is its own small app: `python-chess` enforces move
legality and detects checkmate/stalemate/draws server-side, and the moment
a game ends it writes the result straight to Supabase and adjusts points/
coins in the same request. Nothing is delegated to Lichess or any other
chess platform — that's deliberate, since your leaderboard needs to be the
authority on outcomes, not something synced from an external API.

The board itself is dependency-free vanilla HTML/CSS/JS (click a piece,
click a destination square) so there's nothing to install or rely on a CDN
for — good enough to actually play a full game today. If you want
drag-and-drop, animations, or piece-capture sounds later, swap in
`react-chessboard` or `chessground` against the same two endpoints
(`/api/game/<token>/state` and `/move`) — the backend doesn't need to change.

## Project layout

```
core/            platform-agnostic logic (never import from adapters/)
  config.py      tunable constants (points, coin amounts, timeouts)
  db.py          all Supabase reads/writes, username <-> platform_identity resolution
  commands.py    /callout /yes /no /leaderboard /shop /help business logic
  router.py      parses raw text, runs the command, delivers replies via senders
  senders/       one module per platform's outbound API (registry pattern) —
                  this is what lets a reply be delivered to a *different*
                  platform than the one the incoming command arrived on

adapters/        one file per chat platform; only parses inbound payloads
                  and calls core.router.handle_incoming() — no send logic here
  whatsapp_adapter.py   Flask webhook using WhatsApp Cloud API
  telegram_adapter.py   long-polling bot using Telegram Bot API

web/
  game_server.py  the actual chess board (Flask + python-chess), reached
                   via the link sent when a callout is accepted

sql/
  schema.sql      run this in the Supabase SQL editor to create all tables
```

## Setup

1. **Create a Supabase project**, run `sql/schema.sql` in the SQL editor.
2. Set environment variables:
   ```
   SUPABASE_URL=...
   SUPABASE_SERVICE_ROLE_KEY=...      # server-side only, keep secret
   GAME_TOKEN_SECRET=...              # signs per-player game links, keep secret
   WA_VERIFY_TOKEN=...                # for WhatsApp webhook verification
   WA_ACCESS_TOKEN=...                # WhatsApp Cloud API token
   WA_PHONE_NUMBER_ID=...
   TELEGRAM_BOT_TOKEN=...             # only if using Telegram
   ```
3. `pip install -r requirements.txt`

**Always run scripts with `-m` from the project root** (the folder that
directly contains `core/`, `adapters/`, `web/`), never as a direct file
path like `python web/game_server.py`. Python resolves imports
differently in the two modes — `-m` treats the project root as the
import root; a direct path only adds the script's own folder, so
`from core import ...` fails with `ModuleNotFoundError: No module named 'core'`.
4. Run the game web server: `python -m web.game_server`
5. Run whichever adapter(s) you want:
   - WhatsApp: `flask --app adapters.whatsapp_adapter run -p 8000`, then point
     your WhatsApp Cloud API webhook at `https://<yourhost>/webhook`.
   - Telegram: `python -m adapters.telegram_adapter`

## Adding a new platform purely over HTTP (no code changes here)

Every platform up to now (WhatsApp, Telegram) works because this repo
has a dedicated Python module for it in `core/senders/`. If you want to
add a platform without touching this codebase at all — a Discord bot
written in JS, a partner's existing service, a website chat widget — use
`api/core_api.py` instead, mounted on the same web service as the board.

**Inbound** (their service -> this app): they POST every command here.
```
POST https://your-app.up.railway.app/api/v1/incoming
X-API-Key: <CORE_API_KEY>
{ "platform": "discord", "platform_id": "1234567890",
  "display_name": "someuser", "text": "/callout alice" }
```
This runs exactly like a message from any built-in adapter — same
command parsing, same points/coins logic, same leaderboard.

**Outbound** (this app -> their service): since this codebase has no
idea how to call Discord's API, it can't deliver replies directly. So
their service registers a webhook once:
```
POST https://your-app.up.railway.app/api/v1/platforms
X-API-Key: <CORE_API_KEY>
{ "platform": "discord", "outbound_url": "https://their-service.com/webhook-out",
  "outbound_secret": "some-shared-secret-they-chose" }
```
From then on, any reply meant for a Discord user gets POSTed to
`outbound_url` as `{"platform_id": "...", "text": "..."}`, with
`X-Webhook-Secret` set to what they registered — they check that header
matches before trusting the push, then use their own Discord SDK to
actually send it. This repo never needs to know Discord's API exists.

**Public read endpoint** — no API key needed, safe for a website widget:
```
GET https://your-app.up.railway.app/api/v1/leaderboard?limit=10
```

Set `CORE_API_KEY` (any long random string, same idea as `GAME_TOKEN_SECRET`)
in `.env` and on the Railway `web` service to enable this.

## Testing locally with Telegram + the browser (skip WhatsApp for now)

1. `cp .env.example .env` and fill in `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`,
   `GAME_TOKEN_SECRET`, and `TELEGRAM_BOT_TOKEN`. Leave the `WA_*` lines out —
   nothing reads them unless you actually run `adapters/whatsapp_adapter.py`.
2. `pip install -r requirements.txt`
3. Terminal 1: `python -m web.game_server` — serves the board at `http://localhost:5000`
4. Terminal 2: `python -m adapters.telegram_adapter`
5. Message your bot on Telegram: `/callout <your own username won't work,
   use a second Telegram account or a friend>`, `/yes`, then open the link
   it sends you — it'll be a `localhost:5000` URL, which is expected at
   this stage.

## Bot games and the leaderboard

`/play_bot` games are practice, full stop — win, lose, or forfeit, they
never touch points, coins, wins, or losses for either side. See
`core/game_results.py`: if the bot account is involved in a finished
game at all, the whole scoring step is skipped. This was a deliberate
change — earlier versions gave the human normal win points for beating
the bot, which would've let people farm easy leaderboard points against
a weak, non-adversarial opponent instead of playing real people.

## If the bot ever seems to "stop responding" mid-game

The bot only moves as a side-effect of your own move request — if that
step ever silently failed (a transient error, a bug), the game would
get stuck on "bot's turn" forever with nothing to retry it, and after
`FORFEIT_TIMEOUT_MINUTES` you'd be able to (incorrectly) claim a
forfeit win against a bot that never actually got the chance to move.

Fixed as of this version: `/api/game/<token>/state` now also checks
whether it's currently the bot's turn and retries the bot's move if so,
every time the board polls (every 3 seconds). A failed bot move now
self-heals on the very next poll instead of requiring the human to
trigger another move, or worse, sitting stuck until forfeit-eligible.

## Deploying to Railway

Railway runs each process as its own **service** inside one project, all
pointed at the same GitHub repo. You need up to three services:

1. **Push this repo to GitHub**, then in Railway: New Project -> Deploy from
   GitHub repo -> select it.
2. Railway creates one service automatically. Rename it `web`, and under
   **Settings -> Deploy**, set the **Custom Start Command** to
   `python -m web.game_server`. Under **Settings -> Networking**, click
   **Generate Domain** — Railway gives you a public URL like
   `https://your-app.up.railway.app`. This service hosts the chess
   board AND the WhatsApp webhook (see step 4) — they're the same Flask
   app, just different routes.
3. Add a **second service** for Telegram (New -> Empty Service, same
   repo). Call it `telegram-worker`, Custom Start Command
   `python -m adapters.telegram_adapter`. No public domain needed — it
   polls Telegram rather than receiving webhooks, so it just needs to
   run continuously.
4. **WhatsApp does NOT get its own separate service** — unlike Telegram,
   `adapters/whatsapp_adapter.py` is a Flask app of its own, but rather
   than deploying it separately, point Meta's webhook straight at your
   `web` service's domain instead: this repo doesn't currently merge
   the two Flask apps together, so for now, deploy
   `adapters/whatsapp_adapter.py` as its own **third service** (New ->
   Empty Service, same repo), Custom Start Command
   `flask --app adapters.whatsapp_adapter run --host 0.0.0.0 --port $PORT`,
   with its own **Generate Domain**. In Meta's WhatsApp -> Configuration
   page, set the Callback URL to that service's domain + `/webhook`
   (replacing whatever ngrok URL you used for local testing).
5. **Set environment variables** (Settings -> Variables per service):
   - `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `GAME_TOKEN_SECRET` —
     must be **identical across all three services**. `GAME_TOKEN_SECRET`
     especially: whichever service creates a game signs the link with
     it, and `web` verifies it — any mismatch means every link fails.
   - `TELEGRAM_BOT_TOKEN` — only on `telegram-worker`.
   - `WA_ACCESS_TOKEN`, `WA_PHONE_NUMBER_ID`, `WA_VERIFY_TOKEN` — only
     on the WhatsApp service.
   - `CORE_API_KEY` — only on `web`, only if using `/api/v1/...`.
6. Set `GAME_WEB_BASE_URL` to `https://<your web service domain>/game`
   on **every** service that can create a game (`web`, `telegram-worker`,
   and the WhatsApp service) — redeploy each after changing it.
7. Test end to end: message your Telegram bot, confirm the link it
   sends is a real Railway URL. Then send a WhatsApp message to your
   Meta test number and confirm it reaches your WhatsApp service's logs.

Railway automatically sets a `$PORT` env var for services with a public
domain; `web/game_server.py` already reads it (`os.environ.get("PORT", 5000)`),
so you don't need to set `PORT` yourself. The WhatsApp start command
above reads `$PORT` too via Flask's `--port` flag.

Note: Flask's built-in dev server (what `app.run()` / `flask run` use)
is fine for this stage of testing but isn't meant for real production
traffic — once you're past testing, swap both Flask services' start
commands for `gunicorn` (add it to `requirements.txt` first):
`gunicorn -w 2 -b 0.0.0.0:$PORT web.game_server:app` and
`gunicorn -w 2 -b 0.0.0.0:$PORT adapters.whatsapp_adapter:app` respectively.

Also remember: the WhatsApp access token from Meta's quick-start test
setup **expires in ~24 hours**. Before this needs to stay running for
more than a day, generate a permanent token via a System User in Meta
Business Settings and update `WA_ACCESS_TOKEN` with that instead.
`requirements.txt` first).

## New in this round: matchmaking, a bot opponent, and abandonment handling

- **`/random`** joins a matchmaking queue (`matchmaking_queue` table). The
  next person to also run `/random` gets matched immediately — no need
  to know anyone's username. `/cancel_random` leaves the queue.
- **`/play_bot`** creates an instant game against a built-in bot account
  (`core/bot_engine.py`). No external engine binary required — it
  prefers captures, otherwise plays a random legal move. Good enough for
  a casual game; see the comment in that file for how to upgrade to a
  real UCI engine (e.g. Stockfish) later if you want actual playing
  strength. The bot never appears on the leaderboard or accumulates
  points — see `core/game_results.py`.
- **Abandonment / "left the bot by accident"**: if your opponent stops
  responding mid-game, `games.updated_at` is touched on every move.
  Once `FORFEIT_TIMEOUT_MINUTES` (10, in `core/config.py`) has passed
  with no move from them on their turn, a "Claim win" button appears on
  the board page. This is the general fix for any kind of abandonment —
  closing the tab, blocking the bot, losing their phone, doesn't matter
  why they stopped, the timeout doesn't care.
- **Cross-platform play already worked before this round** and still
  does — the board is a web page, not something rendered inside a chat
  app, so a WhatsApp player and a Telegram player can already play each
  other via `/callout <username>`. `/random` and `/play_bot` don't
  change that; they're just other ways to start a game besides `/callout`.

After pulling these changes, run the two new lines from `sql/schema.sql`
in the Supabase SQL editor (just the `matchmaking_queue` table — don't
re-run the whole file, since the `create type ... as enum` statements
aren't safe to run twice):
```sql
create table if not exists matchmaking_queue (
    user_id         uuid primary key references users(id) on delete cascade,
    joined_at       timestamptz not null default now()
);
```

## New in this round: chess clock, distinct piece colors, unified decline penalty

- **Chess clock**: each player gets a 5-minute total budget
  (`GAME_TIME_LIMIT_SECONDS`), plus a hard, separate 1-minute cap on any
  single move (`MOVE_TIME_LIMIT_SECONDS`) — even with time left in your
  bank, taking too long on one move is an automatic loss. Both are
  enforced server-side, checked on every board poll by **either**
  player, so it's consistent regardless of whose connection is slow —
  as long as one side's browser is still polling (every 3 seconds), a
  timeout gets caught within a few seconds of the deadline. See
  `core/time_control.py`. The board shows two live countdown clocks and
  a "move within Ns or auto-lose" warning near the deadline.
- **Piece colors**: white and black pieces now render with real
  contrast (solid fill + an outline in the opposite color) instead of
  both inheriting the same text color, which made them hard to tell
  apart at a glance.
- **Declining a callout and ignoring one now behave identically** —
  previously only an explicit `/no` cost you `-10` points; letting it
  silently expire had zero consequence. Now both apply the same
  penalty, whether caught by `core/db.py`'s lazy check (when someone
  next looks up your pending callout) or by the scheduled
  `expire_stale_callouts()` cleanup if nobody ever checks again.

After pulling these changes, also run this against your Supabase
project (adds the new clock columns to existing `games` rows):
```sql
alter table games add column if not exists white_time_used_ms integer not null default 0;
alter table games add column if not exists black_time_used_ms integer not null default 0;
alter table games add column if not exists turn_started_at timestamptz not null default now();
```
And re-run the updated `expire_stale_callouts()` function definition
from `sql/schema.sql` (the `create or replace function` block) to pick
up the new decline-on-timeout penalty logic.

## The daily league, the daily deadline, and what counts toward the leaderboard

This is a bigger system than a single feature, so here's the whole
picture in one place.

**Only two kinds of games count toward the leaderboard — weekly or
all-time**: games from an accepted `/callout`, and games from the
league (`/apply_weekly`). `/random` (quick matchmaking) and `/play_bot`
are casual/practice — they never touch points, no matter who wins.
Enforced by a single `origin` field on every game (`callout` /
`random` / `bot` / `scheduled`) — see `core/game_results.py`.

**Weekly leaderboard is a genuine "clean slate," not a mutable counter
that needs resetting**: `/weekly` sums everyone's points from
`transactions` created since the current week started (Monday, UTC).
Since non-qualifying games never write a scored transaction, the sum
is automatically scoped correctly — nothing to reset, nothing to drift.
`/help` also shows **last week's top 10** every time — a frozen
snapshot (`last_week_leaderboard` view), so recent winners' names stay
visible even after the current week's board has moved on.

**The callout faucet**: everyone gets `FREE_CALLOUTS_PER_DAY` (3) free
callouts per day, non-stackable. **Being called out gives you a
2-hour shield** (`CALLOUT_COOLDOWN_HOURS`) — nobody else can call you
out again for 2 hours, so one inactive/asleep player can't get piled
on by multiple challengers at once. The cooldown is checked *before*
the faucet is charged, so a blocked callout doesn't cost you a charge.

**Calling someone out puts you both on the same daily-deadline system
as the league** — accepted `/callout` games and league games share
identical rules from here on (that's exactly what `QUALIFYING_GAME_ORIGINS`
represents: `callout` and `scheduled` are treated identically for
scoring AND for the deadline system below).

**The daily deadline (9 PM UTC)** — every qualifying game gets a
deadline: today's 9 PM if it was created earlier that day, tomorrow's
9 PM if created after 9 PM (`core/time_control.py`,
`get_deadline_for_game`). Once the deadline passes:
- **Neither player moved** → the game is automatically voided (no
  winner, no points) — this is self-healing, checked on every `/state`
  poll, so it fires the moment either player's browser next checks in.
- **One player moved, the other never showed** → the player who
  showed up gets a **Claim win** button (not automatic — a deliberate
  button-press, not a silent resolution). Only the player who actually
  moved can claim it; the no-show can't claim against themselves.
- **Both players engaged** → this isn't a "no-show" situation anymore;
  the existing chess clock (5 min total, 1 min per move) and the
  general 5-minute-inactivity claim already handle an abandoned
  mid-game on their own, well before 9 PM would even come into play.

**The league itself is daily, not a single weekly pairing**
(`core/weekly_league.py`):
- `/apply_weekly` opts you into the *upcoming* week's pool (next
  Monday) — nobody is auto-drafted without signing up.
- Once that week begins, a **new pairing runs every day** for
  everyone still in the pool, so you get a match most/all days of the
  week, not just once. Pairing prefers opponents you haven't already
  played that week, falling back to a repeat only when the pool's too
  small to avoid it (e.g. exactly 2 people).
- An odd headcount means one random person draws a bye that specific
  day — no penalty, back in the pool the next day.
- `/my_schedule` shows today's match + link. `/cancel_weekly` withdraws
  you from the pool before the week starts.

**Running it all** — two Railway Cron Job services:
- `daily-pairings`: Custom Start Command `python -m scripts.run_daily_pairings`,
  Cron Schedule `0 8 * * *` (8 AM UTC). Sweeps yesterday's fully-abandoned
  games (nobody ever revisited the link, so the poll-triggered self-heal
  never got a chance to run) and generates today's pairings. Idempotent —
  safe if it somehow runs twice the same day.
- `weekly-reminders`: Custom Start Command `python -m scripts.send_weekly_reminders`,
  Cron Schedule `0 18 * * *` (6 PM UTC, a few hours before the 9 PM
  deadline). Nudges anyone with an unfinished match today.

Both need the same `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`,
`GAME_TOKEN_SECRET`, and `GAME_WEB_BASE_URL` as your other services.

After pulling this update, run the new parts of `sql/schema.sql`
against Supabase: the new `users` columns (`callouts_used_today`,
`callouts_reset_date`, `last_called_out_at`), the `game_origin` type +
`games.origin` column, `weekly_signups`, `scheduled_matches` (now with
`match_date`), and the `weekly_leaderboard` / `last_week_leaderboard`
views.

## GOWA — unofficial WhatsApp integration (alternative to the official adapter)

**Read this before using it.** GOWA's own README says, verbatim: *"This
project is unofficial and not affiliated with WhatsApp. Please use
official WhatsApp API to avoid any issues."* That's the maintainer, not
me being cautious. It works outside WhatsApp's Terms of Service, and
the number you connect can be banned without warning or appeal — a
different risk than Meta's business-verification bureaucracy, not a
risk-free replacement for it. Prefer `adapters/whatsapp_adapter.py`
(the official Cloud API) if you can; use this if you've decided that
tradeoff is worth it for you.

### How it works

`adapters/gowa_adapter.py` is a webhook receiver, same shape as every
other adapter in this repo: GOWA POSTs every incoming WhatsApp message
to it, HMAC-signed so forged requests get rejected (confirmed: a
missing/wrong signature returns 401 before anything else runs). Real
messages get handed to `core.router.handle_incoming("gowa", ...)` —
the exact same entrypoint Telegram and the official WhatsApp adapter
use. `core/senders/gowa.py` handles the reverse direction, calling
GOWA's `POST /send/message` whenever a reply needs to go out.

**Does it hit your database? Yes — the same one, automatically.**
There's no separate database for GOWA. It's just a new `platform`
value (`'gowa'`) flowing through the exact same `core/db.py`,
`platform_identities` table, and Supabase project every other platform
uses. A GOWA user is created via `get_or_create_user('gowa', <their
WhatsApp JID>, <display name>)` — identical mechanism to how a
Telegram or official-WhatsApp user gets created.

**Does the multi-platform username system still hold? Yes,
unchanged.** `/callout <username>`, `/link`, `/whoami` — none of this
cares which adapter a message came through. A person reachable via
GOWA can be called out by a Telegram user, linked to their Discord
account (once that exists), etc., exactly like today. Nothing about
adding GOWA changes that architecture; it's designed to be adapter-agnostic
specifically so this kind of addition doesn't require touching core logic.

### Setup — verify the send API first, then wire up the pipeline

1. **Run GOWA** (Docker is easiest):
   ```
   docker run --detach --publish=3000:3000 --name=gowa --restart=always \
     --volume=$(docker volume create --name=gowa):/app/storages \
     aldinokemal2104/go-whatsapp-web-multidevice rest \
     --webhook-secret=chesscallout2030
   ```
2. **Open `http://localhost:3000`**, scan the QR code with the WhatsApp
   you want to use as the bot (Linked Devices in the WhatsApp app).
3. **Verify the send API before anything else** — this is the step
   that catches it immediately if the request body field names in
   `core/senders/gowa.py` don't match your GOWA version, rather than
   discovering it buried in the full pipeline:
   ```
   curl -X POST http://localhost:3000/send/message \
     -H "Content-Type: application/json" \
     -d '{"phone": "<your own WhatsApp number>@s.whatsapp.net", "message": "test"}'
   ```
   If you get the test message on your phone, the field names are
   right and you can move on. **If not**, open
   `http://localhost:3000/docs/openapi.yaml` (or check your running
   instance's Swagger UI) to see the actual field names your version
   expects, and update the `payload` dict in `core/senders/gowa.py`
   to match — it's a single, obvious spot to fix.
4. **Set your `.env`**: `GOWA_BASE_URL=http://localhost:3000`,
   `GOWA_WEBHOOK_SECRET=some-secret-you-choose` (must match what you
   ran GOWA with in step 1).
5. **Run the adapter**: `python -m adapters.gowa_adapter` (serves on
   port 8001 by default).
6. **Point GOWA's webhook at it.** For local testing, GOWA and your
   adapter are both on your machine, so this is straightforward; for
   anything beyond local testing, you'd deploy both to Railway (same
   pattern as the WhatsApp/Telegram services already documented above)
   and set `--webhook=https://<your-adapter-domain>/webhook` on GOWA.
7. **Test**: message the connected WhatsApp number from any other
   phone. Check your adapter's logs for the incoming message, confirm
   you get a real reply back.

## Cold-callouts and the username economy

**`/callout <handle> <platform>`** (e.g. `/callout bobsmith telegram`) —
for reaching someone who's never used this bot at all, on a platform
where bots genuinely cannot message a stranger (every platform except
WhatsApp — this is a real platform restriction, not a permissions
setting). It doesn't message them directly: it creates a stub account
with an ugly auto-generated username and hands the *challenger* a
shareable invite link. Only once the target clicks that link and sends
`/start` does the real callout (with a real 5-minute window) actually
begin — see `core/cold_invite.py`. Currently only `telegram` is wired
up (`COLD_INVITE_SUPPORTED_PLATFORMS` in `core/config.py`) since that's
the only platform with both a real adapter and deep-link support in
this repo; asking for an unsupported platform gives an honest error,
not a silent failure. Needs `TELEGRAM_BOT_USERNAME` set in `.env` (your
bot's `@handle`, shown in the adapter's startup log) to build the
`t.me/...` links.

**Username changes cost coins after the first one** — everyone's first
`/change_username` is free (their auto-generated default is never
pretty), every one after that costs `USERNAME_CHANGE_COST_COINS` (50).
This is a deliberate coin sink: it gives coins earned from wins
somewhere to go, and discourages squatting/spam-renaming without
blocking legitimate rebranding outright.

## Known limitations / next steps

- **Daily pairing is fully random**, not skill-balanced (no attempt at
  seeding by rank) — deliberate for v1 simplicity, worth revisiting if
  you want closer matches.
- **`/buy`-ing extra callouts isn't built** — `shop_items` supports it
  structurally, no purchase flow wired up yet.
- **Discord/Instagram/Messenger cold-callout isn't functional** —
  `core/cold_invite.py` is built to support them, but this repo has no
  actual Discord/Instagram/Messenger adapter yet, only Telegram and
  WhatsApp. Adding one of those platforms means writing its adapter
  (like `adapters/telegram_adapter.py`) AND its deep-link support in
  `build_invite_link()` before `/callout <handle> discord` etc. works.
- **General username discovery** (finding out what someone's app
  username even is, beyond a friend telling you directly) currently
  happens via `/leaderboard`/`/weekly` visibility and the notifications
  sent during callouts/matches — there's no `/search` or player
  directory. Worth building if this grows past friend groups who
  already know each other.
- **Rate limiting on `/callout` is now covered** by the daily faucet
  and cooldown — the old "spam /callout" limitation below is resolved.

- **Matchmaking queue has a small race window**: `find_and_claim_opponent`
  does a select-then-delete rather than one atomic operation, so two
  people running `/random` at the exact same instant could theoretically
  both claim the same opponent. Low-risk at casual scale; fix with a
  Postgres function using `FOR UPDATE SKIP LOCKED` if usage grows.
- **Bot opponent is intentionally weak** (captures-when-available, else
  random) — it's there so nobody's stuck waiting for a human, not to be
  a real chess challenge.
- **Username squatting**: since usernames are first-come-first-served and
  cheap to generate a new account for, someone could spam-claim usernames.
  Worth adding a cooldown or a minimum-games-played requirement before
  allowing `/change_username`.
- **5-minute expiry enforcement**: `expire_stale_callouts()` in the SQL
  schema needs to run on a schedule (Supabase `pg_cron`, or an external
  cron hitting an Edge Function) — right now expiry is only checked lazily
  when someone looks up a pending callout.
- **Chess board polish**: the board is functional (click a piece, click a
  destination, legality/checkmate enforced server-side) but plain — no
  animations or drag-and-drop. Swap in `react-chessboard` or `chessground`
  against the same `/api/game/<token>/state` and `/move` endpoints later.
- **The `session_token` column on `games` is now unused** — it was the
  old shared-link mechanism, superseded by the per-player signed tokens
  in `core/game_tokens.py`. Harmless to leave for now (still useful as
  an internal opaque game reference), but fine to drop in a later
  migration if you want to tidy up.
- **`api/core_api.py`'s `/platforms` endpoint has no de-registration or
  ownership check** — anyone with `CORE_API_KEY` can overwrite any
  platform's webhook registration. Fine while it's just you operating
  this, but add per-platform auth before letting outside teams register
  their own webhooks against your instance.
