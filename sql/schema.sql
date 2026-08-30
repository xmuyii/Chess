-- ============================================================
-- Chess Callout Game — Supabase Schema (v2)
-- Identity model: `username` is the single global unique ID,
-- used for /callout, the leaderboard, everything user-facing.
-- Phone numbers / chat IDs are just delivery addresses attached
-- to an account via platform_identities.
-- ============================================================

create extension if not exists "uuid-ossp";

-- ---------------------------------------------------------------
-- USERS — one row per player, globally unique username
-- ---------------------------------------------------------------
create table if not exists users (
    id              uuid primary key default uuid_generate_v4(),
    username        text not null unique,        -- the public, callable ID everywhere
    coins           integer not null default 100,
    points          integer not null default 0,
    wins            integer not null default 0,
    losses          integer not null default 0,
    draws           integer not null default 0,
    declines        integer not null default 0,
    -- Daily callout faucet: FREE_CALLOUTS_PER_DAY per day, non-stackable
    -- (unused ones don't carry over to the next day).
    callouts_used_today  integer not null default 0,
    callouts_reset_date  date not null default current_date,
    -- Cooldown on being TARGETED: once someone calls you out, nobody
    -- else can call you out again for CALLOUT_COOLDOWN_HOURS.
    last_called_out_at   timestamptz,
    -- First /change_username is free; every one after that costs coins
    -- (deters squatting/spam-renaming while still allowing rebranding).
    username_changes_used integer not null default 0,
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now()
);

create index if not exists idx_users_points on users (points desc);
-- case-insensitive uniqueness/lookup on username
create unique index if not exists idx_users_username_lower on users (lower(username));

-- ---------------------------------------------------------------
-- PLATFORM IDENTITIES
-- Where to actually deliver a message for a given account. One
-- account can eventually have several (WhatsApp + Telegram linked
-- to the same person) via a future /link command; for now each
-- account typically has exactly one, created at sign-up.
-- ---------------------------------------------------------------
create table if not exists platform_identities (
    id              uuid primary key default uuid_generate_v4(),
    user_id         uuid not null references users(id) on delete cascade,
    platform        text not null,                -- 'whatsapp' | 'telegram' | 'discord' | ...
    platform_id     text not null,                -- phone number, chat id, etc.
    is_primary      boolean not null default true, -- which identity to deliver to by default
    created_at      timestamptz not null default now(),
    unique (platform, platform_id)
);

create index if not exists idx_platform_identities_user on platform_identities (user_id);

-- ---------------------------------------------------------------
-- PLATFORM WEBHOOKS
-- Lets a brand-new platform integrate purely over HTTP, with zero
-- code changes to this repo. The platform's own service:
--   1. POSTs inbound commands to /api/v1/incoming here
--   2. registers an outbound_url + secret here, so this app can push
--      replies back to THEM, and they handle actual delivery using
--      whatever SDK that platform requires
-- ---------------------------------------------------------------
create table if not exists platform_webhooks (
    platform        text primary key,            -- e.g. 'discord', 'slack'
    outbound_url    text not null,                -- where we POST replies for this platform
    outbound_secret text not null,                -- sent as X-Webhook-Secret so they can verify it's really us
    active          boolean not null default true,
    created_at      timestamptz not null default now()
);

-- ---------------------------------------------------------------
-- PENDING INVITES — cold-callout support for platforms that CANNOT
-- message a stranger (Telegram, Discord, etc. — everything except
-- WhatsApp's phone-number reach). A stub account is created
-- immediately with an ugly auto-generated username; the real callout
-- only activates once the target actually clicks the invite link and
-- starts the bot, at which point their real platform_identity gets
-- attached and a normal 5-minute callout window begins.
-- ---------------------------------------------------------------
create table if not exists pending_invites (
    claim_code      text primary key,
    user_id         uuid not null references users(id) on delete cascade,  -- the stub account
    target_platform text not null,
    target_handle   text not null,   -- the native username as typed, for reference/debugging
    created_by      uuid not null references users(id),                    -- the challenger
    created_at      timestamptz not null default now(),
    claimed         boolean not null default false,
    claimed_at      timestamptz
);

-- ---------------------------------------------------------------
-- LINK CODES — short-lived codes used to attach a second platform
-- identity to an existing account (e.g. link Telegram to the same
-- account as an existing WhatsApp account).
-- ---------------------------------------------------------------
create table if not exists link_codes (
    code            text primary key,
    user_id         uuid not null references users(id) on delete cascade,
    created_at      timestamptz not null default now(),
    expires_at      timestamptz not null default (now() + interval '10 minutes')
);

-- ---------------------------------------------------------------
-- MATCHMAKING QUEUE — for /random. Whoever's been waiting longest
-- gets matched with the next person who also runs /random.
-- ---------------------------------------------------------------
create table if not exists matchmaking_queue (
    user_id         uuid primary key references users(id) on delete cascade,
    joined_at       timestamptz not null default now()
);

-- ---------------------------------------------------------------
-- CALLOUTS
-- ---------------------------------------------------------------
create type callout_status as enum ('pending', 'accepted', 'declined', 'expired', 'cancelled');

create table if not exists callouts (
    id              uuid primary key default uuid_generate_v4(),
    challenger_id   uuid not null references users(id),
    opponent_id     uuid not null references users(id),
    status          callout_status not null default 'pending',
    created_at      timestamptz not null default now(),
    expires_at      timestamptz not null default (now() + interval '5 minutes'),
    responded_at    timestamptz,
    game_id         uuid
);

create index if not exists idx_callouts_opponent_status on callouts (opponent_id, status);
create index if not exists idx_callouts_expiry on callouts (status, expires_at);

-- ---------------------------------------------------------------
-- GAMES
-- ---------------------------------------------------------------
create type game_status as enum ('active', 'white_won', 'black_won', 'draw', 'aborted');

-- What kind of game this was — determines whether it counts toward the
-- leaderboard at all. Only 'callout' and 'scheduled' games do; 'random'
-- (quick matchmaking) and 'bot' (/play_bot) are casual/practice and
-- never touch points, on either the weekly or all-time leaderboard.
create type game_origin as enum ('callout', 'random', 'bot', 'scheduled');

create table if not exists games (
    id              uuid primary key default uuid_generate_v4(),
    callout_id      uuid references callouts(id),
    white_id        uuid not null references users(id),
    black_id        uuid not null references users(id),
    fen             text not null default 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1',
    pgn             text not null default '',
    status          game_status not null default 'active',
    winner_id       uuid references users(id),
    session_token   text not null unique,
    origin          game_origin not null default 'callout',
    -- Chess clock: each side has a total time budget (white/black_time_used_ms
    -- tracks cumulative milliseconds spent across all their completed moves).
    -- turn_started_at resets every time a move is made, marking when the
    -- new side-to-move's clock started running.
    white_time_used_ms integer not null default 0,
    black_time_used_ms integer not null default 0,
    turn_started_at timestamptz not null default now(),
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now(),
    finished_at     timestamptz
);

create index if not exists idx_games_token on games (session_token);
create index if not exists idx_games_origin on games (origin);

-- ---------------------------------------------------------------
-- WEEKLY LEAGUE — opt-in scheduled matches, separate from casual
-- /callout, /random, and /play_bot games. Players apply to play in the
-- upcoming week; a pairing run (see core/weekly_league.py, triggered
-- by a Railway Cron Job) matches everyone who applied and creates the
-- actual games + sends each player their link.
-- ---------------------------------------------------------------
create table if not exists weekly_signups (
    id              uuid primary key default uuid_generate_v4(),
    user_id         uuid not null references users(id) on delete cascade,
    week_start      date not null,   -- the Monday of the week being signed up for
    created_at      timestamptz not null default now(),
    unique (user_id, week_start)
);

create type scheduled_match_status as enum ('paired', 'bye');

create table if not exists scheduled_matches (
    id              uuid primary key default uuid_generate_v4(),
    week_start      date not null,
    match_date      date not null,   -- the specific day this match is for (daily league)
    player_a_id     uuid not null references users(id),
    player_b_id     uuid references users(id),  -- null if player_a drew a bye this day
    game_id         uuid references games(id),  -- null for a bye
    status          scheduled_match_status not null default 'paired',
    created_at      timestamptz not null default now()
    -- Idempotency (nobody paired twice on the same day, whether as
    -- player_a or player_b) is enforced in application code — see
    -- core/weekly_league.py — not by a DB constraint, since a simple
    -- unique index can't express "neither column repeats" cleanly.
);

create index if not exists idx_scheduled_matches_week on scheduled_matches (week_start);
create index if not exists idx_scheduled_matches_date on scheduled_matches (match_date);

-- ---------------------------------------------------------------
-- MOVES
-- ---------------------------------------------------------------
create table if not exists moves (
    id              bigserial primary key,
    game_id         uuid not null references games(id),
    ply             integer not null,
    player_id       uuid not null references users(id),
    move_san        text not null,
    fen_after       text not null,
    created_at      timestamptz not null default now(),
    unique (game_id, ply)
);

-- ---------------------------------------------------------------
-- TRANSACTIONS (audit trail)
-- ---------------------------------------------------------------
create type txn_type as enum ('decline_penalty', 'win_reward', 'loss_penalty', 'shop_purchase', 'signup_bonus', 'admin_adjust');

create table if not exists transactions (
    id              bigserial primary key,
    user_id         uuid not null references users(id),
    type            txn_type not null,
    coins_delta     integer not null default 0,
    points_delta    integer not null default 0,
    ref_id          uuid,
    created_at      timestamptz not null default now()
);

-- ---------------------------------------------------------------
-- SHOP
-- ---------------------------------------------------------------
create table if not exists shop_items (
    id              uuid primary key default uuid_generate_v4(),
    name            text not null,
    description     text,
    price_coins     integer not null,
    active          boolean not null default true
);

create table if not exists user_inventory (
    user_id         uuid not null references users(id),
    item_id         uuid not null references shop_items(id),
    acquired_at     timestamptz not null default now(),
    primary key (user_id, item_id)
);

-- ---------------------------------------------------------------
-- LEADERBOARD VIEW
-- ---------------------------------------------------------------
create or replace view leaderboard as
select
    row_number() over (order by points desc, wins desc) as rank,
    id, username, points, coins, wins, losses, draws
from users
order by points desc, wins desc;

-- ---------------------------------------------------------------
-- WEEKLY LEADERBOARD — "clean slate" every week, giving new players
-- a real chance to top it. No reset job needed: this just sums
-- transactions.points_delta from the current ISO week (Mon-Sun UTC)
-- onward. It's automatically scoped to only callout/scheduled games
-- because non-qualifying games (random, bot) never write a scored
-- transaction in the first place — see core/game_results.py.
-- ---------------------------------------------------------------
create or replace view weekly_leaderboard as
select
    row_number() over (order by coalesce(sum(t.points_delta), 0) desc) as rank,
    u.id, u.username, coalesce(sum(t.points_delta), 0)::int as points, u.coins
from users u
left join transactions t
    on t.user_id = u.id
    and t.created_at >= date_trunc('week', now())
group by u.id, u.username, u.coins
order by points desc;

-- ---------------------------------------------------------------
-- LAST WEEK'S TOP 10 — a frozen snapshot of last week's winners,
-- shown in /help so players see familiar names reinforced every time
-- they check the menu. Same "sum this week's qualifying transactions"
-- approach as weekly_leaderboard, just shifted back one week.
-- ---------------------------------------------------------------
create or replace view last_week_leaderboard as
select
    row_number() over (order by coalesce(sum(t.points_delta), 0) desc) as rank,
    u.id, u.username, coalesce(sum(t.points_delta), 0)::int as points
from users u
left join transactions t
    on t.user_id = u.id
    and t.created_at >= date_trunc('week', now() - interval '1 week')
    and t.created_at <  date_trunc('week', now())
group by u.id, u.username
having coalesce(sum(t.points_delta), 0) > 0
order by points desc;

-- ---------------------------------------------------------------
-- Expire stale callouts — schedule via pg_cron every 1 minute
-- ---------------------------------------------------------------
create or replace function expire_stale_callouts() returns void as $$
begin
    -- Same penalty as an explicit /no, applied to anyone who simply
    -- never responded — no incentive to go silent instead of declining.
    -- Keep the -10 in sync with DECLINE_PENALTY_POINTS in core/config.py.
    update users
    set points = points - 10, declines = declines + 1
    from callouts
    where callouts.opponent_id = users.id
      and callouts.status = 'pending'
      and callouts.expires_at < now();

    insert into transactions (user_id, type, points_delta, ref_id)
    select opponent_id, 'decline_penalty', -10, id
    from callouts
    where status = 'pending' and expires_at < now();

    update callouts
    set status = 'expired'
    where status = 'pending' and expires_at < now();
end;
$$ language plpgsql;

-- select cron.schedule('expire-callouts', '* * * * *', $$select expire_stale_callouts()$$);
