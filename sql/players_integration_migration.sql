-- ============================================================
-- Chess Callout <> shared-players integration migration
-- ============================================================
-- Safe to run multiple times (idempotent) — every statement uses
-- IF NOT EXISTS / DO-block guards, same pattern as the standalone
-- chess-callout schema.
--
-- Identity model: `players.user_id` (text) is now the ONE shared
-- identity across every game. `players.username` is already shared —
-- chess never creates its own username column. Chess-specific stats
-- (points, wins, coins, etc.) live as new `chess_`-prefixed columns
-- directly on `players`, following the same convention as your
-- existing `fusion_*` / `trivia_*` columns.
-- ============================================================

-- players.user_id needs a uniqueness guarantee to be a valid foreign
-- key target for every chess table below. A unique INDEX (not a table
-- constraint) is used specifically because it's safely re-runnable —
-- unlike `ALTER TABLE ... ADD CONSTRAINT`, which errors on a second run.
--
-- IMPORTANT: run this preflight check FIRST, separately, before the
-- index creation below. If it returns any rows, the index creation
-- will fail outright — duplicate user_id values need resolving first,
-- since I have no way to know from a column listing alone whether
-- your existing data actually guarantees uniqueness:
--   select user_id, count(*) from players group by user_id having count(*) > 1;

create extension if not exists pgcrypto;  -- provides gen_random_uuid(), used below

create unique index if not exists idx_players_user_id_unique on players (user_id);

-- ---------------------------------------------------------------
-- New chess-specific columns on the shared players table
-- ---------------------------------------------------------------
alter table players add column if not exists chess_coins integer not null default 100;
alter table players add column if not exists chess_all_time_points integer not null default 0;
alter table players add column if not exists chess_wins integer not null default 0;
alter table players add column if not exists chess_losses integer not null default 0;
alter table players add column if not exists chess_draws integer not null default 0;
alter table players add column if not exists chess_declines integer not null default 0;
-- Daily callout faucet (non-stackable) and target-cooldown, same rules
-- as the standalone version.
alter table players add column if not exists chess_callouts_used_today integer not null default 0;
alter table players add column if not exists chess_callouts_reset_date date not null default current_date;
alter table players add column if not exists chess_last_called_out_at timestamptz;
alter table players add column if not exists chess_username_changes_used integer not null default 0;

create index if not exists idx_players_chess_points on players (chess_all_time_points desc);

-- ---------------------------------------------------------------
-- Platform identities — where to deliver a chess message for a given
-- player. Distinct from any login/auth mechanism the other game(s)
-- use; this is purely "which chat platform + address reaches them."
-- ---------------------------------------------------------------
create table if not exists chess_platform_identities (
    id              uuid primary key default gen_random_uuid(),
    user_id         text not null references players(user_id) on delete cascade,
    platform        text not null,
    platform_id     text not null,
    is_primary      boolean not null default true,
    created_at      timestamptz not null default now(),
    unique (platform, platform_id)
);

create index if not exists idx_chess_platform_identities_user on chess_platform_identities (user_id);

-- ---------------------------------------------------------------
-- Callouts
-- ---------------------------------------------------------------
do $$ begin
    create type chess_callout_status as enum ('pending', 'accepted', 'declined', 'expired', 'cancelled');
exception
    when duplicate_object then null;
end $$;

create table if not exists chess_callouts (
    id              uuid primary key default gen_random_uuid(),
    challenger_id   text not null references players(user_id),
    opponent_id     text not null references players(user_id),
    status          chess_callout_status not null default 'pending',
    created_at      timestamptz not null default now(),
    expires_at      timestamptz not null default (now() + interval '5 minutes'),
    responded_at    timestamptz,
    game_id         uuid
);

create index if not exists idx_chess_callouts_opponent_status on chess_callouts (opponent_id, status);
create index if not exists idx_chess_callouts_expiry on chess_callouts (status, expires_at);

-- ---------------------------------------------------------------
-- Games
-- ---------------------------------------------------------------
do $$ begin
    create type chess_game_status as enum ('active', 'white_won', 'black_won', 'draw', 'aborted');
exception
    when duplicate_object then null;
end $$;

do $$ begin
    create type chess_game_origin as enum ('callout', 'random', 'bot', 'scheduled');
exception
    when duplicate_object then null;
end $$;

create table if not exists chess_games (
    id              uuid primary key default gen_random_uuid(),
    callout_id      uuid references chess_callouts(id),
    white_id        text not null references players(user_id),
    black_id        text not null references players(user_id),
    fen             text not null default 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1',
    pgn             text not null default '',
    status          chess_game_status not null default 'active',
    winner_id       text references players(user_id),
    session_token   text not null unique,
    origin          chess_game_origin not null default 'callout',
    white_time_used_ms integer not null default 0,
    black_time_used_ms integer not null default 0,
    turn_started_at timestamptz not null default now(),
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now(),
    finished_at     timestamptz
);

create index if not exists idx_chess_games_token on chess_games (session_token);
create index if not exists idx_chess_games_origin on chess_games (origin);

create table if not exists chess_moves (
    id              bigserial primary key,
    game_id         uuid not null references chess_games(id),
    ply             integer not null,
    player_id       text not null references players(user_id),
    move_san        text not null,
    fen_after       text not null,
    created_at      timestamptz not null default now(),
    unique (game_id, ply)
);

-- ---------------------------------------------------------------
-- Transactions — the audit trail that also powers the "clean slate"
-- weekly leaderboard below (no reset job needed, see the view).
-- ---------------------------------------------------------------
do $$ begin
    create type chess_txn_type as enum ('decline_penalty', 'win_reward', 'loss_penalty', 'shop_purchase', 'signup_bonus', 'admin_adjust');
exception
    when duplicate_object then null;
end $$;

create table if not exists chess_transactions (
    id              bigserial primary key,
    user_id         text not null references players(user_id),
    type            chess_txn_type not null,
    coins_delta     integer not null default 0,
    points_delta    integer not null default 0,
    ref_id          uuid,
    created_at      timestamptz not null default now()
);

-- ---------------------------------------------------------------
-- Shop
-- ---------------------------------------------------------------
create table if not exists chess_shop_items (
    id              uuid primary key default gen_random_uuid(),
    name            text not null,
    description     text,
    price_coins     integer not null,
    active          boolean not null default true
);

create table if not exists chess_user_inventory (
    user_id         text not null references players(user_id),
    item_id         uuid not null references chess_shop_items(id),
    acquired_at     timestamptz not null default now(),
    primary key (user_id, item_id)
);

-- ---------------------------------------------------------------
-- Matchmaking queue (/random)
-- ---------------------------------------------------------------
create table if not exists chess_matchmaking_queue (
    user_id         text primary key references players(user_id) on delete cascade,
    joined_at       timestamptz not null default now()
);

-- ---------------------------------------------------------------
-- Weekly league — signups + daily pairings
-- ---------------------------------------------------------------
create table if not exists chess_weekly_signups (
    id              uuid primary key default gen_random_uuid(),
    user_id         text not null references players(user_id) on delete cascade,
    week_start      date not null,
    created_at      timestamptz not null default now(),
    unique (user_id, week_start)
);

do $$ begin
    create type chess_scheduled_match_status as enum ('paired', 'bye');
exception
    when duplicate_object then null;
end $$;

create table if not exists chess_scheduled_matches (
    id              uuid primary key default gen_random_uuid(),
    week_start      date not null,
    match_date      date not null,
    player_a_id     text not null references players(user_id),
    player_b_id     text references players(user_id),
    game_id         uuid references chess_games(id),
    status          chess_scheduled_match_status not null default 'paired',
    created_at      timestamptz not null default now()
);

create index if not exists idx_chess_scheduled_matches_week on chess_scheduled_matches (week_start);
create index if not exists idx_chess_scheduled_matches_date on chess_scheduled_matches (match_date);

-- ---------------------------------------------------------------
-- Pending invites (cold-callout by native handle)
-- ---------------------------------------------------------------
create table if not exists chess_pending_invites (
    claim_code      text primary key,
    user_id         text not null references players(user_id) on delete cascade,
    target_platform text not null,
    target_handle   text not null,
    created_by      text not null references players(user_id),
    created_at      timestamptz not null default now(),
    claimed         boolean not null default false,
    claimed_at      timestamptz
);

-- ---------------------------------------------------------------
-- Account linking (/link) — attach a second platform to an account
-- ---------------------------------------------------------------
create table if not exists chess_link_codes (
    code            text primary key,
    user_id         text not null references players(user_id) on delete cascade,
    created_at      timestamptz not null default now(),
    expires_at      timestamptz not null default (now() + interval '10 minutes')
);

-- ---------------------------------------------------------------
-- Platform webhooks (generic HTTP platform integration)
-- ---------------------------------------------------------------
create table if not exists chess_platform_webhooks (
    platform        text primary key,
    outbound_url    text not null,
    outbound_secret text not null,
    active          boolean not null default true,
    created_at      timestamptz not null default now()
);

-- ---------------------------------------------------------------
-- Leaderboards
-- ---------------------------------------------------------------
create or replace view chess_alltime_leaderboard as
select
    row_number() over (order by chess_all_time_points desc, chess_wins desc) as rank,
    user_id, username, chess_all_time_points as points, chess_coins as coins,
    chess_wins as wins, chess_losses as losses, chess_draws as draws
from players
order by chess_all_time_points desc, chess_wins desc;

-- "Clean slate" every week — sums chess_transactions since the current
-- ISO week started. No reset job: non-qualifying games (random/bot)
-- never write a scored transaction, so this stays correctly scoped
-- automatically. Same design as the standalone version.
create or replace view chess_weekly_leaderboard as
select
    row_number() over (order by coalesce(sum(t.points_delta), 0) desc) as rank,
    p.user_id, p.username, coalesce(sum(t.points_delta), 0)::int as points, p.chess_coins as coins
from players p
left join chess_transactions t
    on t.user_id = p.user_id
    and t.created_at >= date_trunc('week', now())
group by p.user_id, p.username, p.chess_coins
order by points desc;

create or replace view chess_last_week_leaderboard as
select
    row_number() over (order by coalesce(sum(t.points_delta), 0) desc) as rank,
    p.user_id, p.username, coalesce(sum(t.points_delta), 0)::int as points
from players p
left join chess_transactions t
    on t.user_id = p.user_id
    and t.created_at >= date_trunc('week', now() - interval '1 week')
    and t.created_at <  date_trunc('week', now())
group by p.user_id, p.username
having coalesce(sum(t.points_delta), 0) > 0
order by points desc;

-- ---------------------------------------------------------------
-- Expire stale callouts — schedule via pg_cron every 1 minute.
-- Ignoring a callout gets the same penalty as explicitly declining.
-- ---------------------------------------------------------------
create or replace function expire_stale_chess_callouts() returns void as $$
begin
    update players
    set chess_all_time_points = chess_all_time_points - 10,
        chess_declines = chess_declines + 1
    from chess_callouts
    where chess_callouts.opponent_id = players.user_id
      and chess_callouts.status = 'pending'
      and chess_callouts.expires_at < now();

    insert into chess_transactions (user_id, type, points_delta, ref_id)
    select opponent_id, 'decline_penalty', -10, id
    from chess_callouts
    where status = 'pending' and expires_at < now();

    update chess_callouts
    set status = 'expired'
    where status = 'pending' and expires_at < now();
end;
$$ language plpgsql;

-- select cron.schedule('expire-chess-callouts', '* * * * *', $$select expire_stale_chess_callouts()$$);
