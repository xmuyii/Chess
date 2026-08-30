"""
Minimal Flask + python-chess backend for the actual game session.

Each player has their OWN link (see core/commands.handle_accept), signed
with core/game_tokens so it encodes exactly who they are and which color
they're playing. That means:
  - the /move endpoint can enforce that only white's token can move on
    white's turn, and only black's on black's turn — a player literally
    cannot move the other side's pieces, even by editing the request
  - the page can show each player whether their opponent is a brand-new
    account or someone with game history, before/while they play

This is a fully custom board (not Lichess): python-chess enforces
legality and end-of-game detection server-side, and results are written
straight to Supabase the moment a game ends.

Abandonment handling: if your opponent stops responding mid-game (closed
the tab, left the bot, whatever), games.updated_at is touched on every
move. Once FORFEIT_TIMEOUT_MINUTES has passed with no move from them on
their turn, /claim_forfeit lets you claim the win instead of being stuck
waiting forever.

Bot games (/play_bot): after every human move, play_bot_move_if_needed()
checks whether it's now the bot's turn and, if so, plays immediately —
server-side, no separate request needed.
"""
from dotenv import load_dotenv
load_dotenv()  # must run before importing core.* modules that read env vars at import time

from datetime import datetime, timedelta, timezone
import traceback

import chess
from flask import Flask, jsonify, render_template_string, request

from core import db, config, game_tokens
from core.game_results import apply_game_result
from core.time_control import (
    check_and_apply_timeout,
    clock_update_after_move,
    clock_display_fields,
    check_and_void_if_missed_deadline,
    deadline_noshow_claim_eligible,
)
from core.bot_engine import play_bot_move_if_needed
from api.core_api import bp as core_api_bp
import os

app = Flask(__name__)
app.register_blueprint(core_api_bp)

BOARD_PAGE = """
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Chess Callout</title>
<style>
  body { font-family: system-ui, sans-serif; display: flex; flex-direction: column;
         align-items: center; background: #1e1e1e; color: #eee; padding: 20px; }
  #board { display: grid; grid-template-columns: repeat(8, 56px); grid-template-rows: repeat(8, 56px);
           border: 3px solid #444; }
  .sq { width: 56px; height: 56px; display: flex; align-items: center; justify-content: center;
        font-size: 36px; cursor: pointer; user-select: none; }
  .light { background: #ebecd0; }
  .dark  { background: #779556; }
  .selected { outline: 4px solid #ffcc00; outline-offset: -4px; }
  .piece-white {
    color: #fbfbfb;
    text-shadow: -1.5px -1.5px 0 #000, 1.5px -1.5px 0 #000,
                 -1.5px 1.5px 0 #000, 1.5px 1.5px 0 #000,
                 0 0 3px rgba(0, 0, 0, 0.5);
  }
  .piece-black {
    color: #0a0a0a;
    text-shadow: -1.5px -1.5px 0 #fff, 1.5px -1.5px 0 #fff,
                 -1.5px 1.5px 0 #fff, 1.5px 1.5px 0 #fff,
                 0 0 3px rgba(255, 255, 255, 0.5);
  }
  #status { margin-top: 14px; font-size: 18px; min-height: 24px; }
  #players { margin-top: 6px; font-size: 14px; color: #aaa; }
  .badge { background: #2d7; color: #111; border-radius: 4px; padding: 1px 6px; font-size: 11px; margin-left: 6px; }
  #clocks { display: flex; gap: 16px; margin-top: 10px; font-variant-numeric: tabular-nums; }
  .clock { padding: 8px 16px; border-radius: 8px; background: #2a2a2a; font-size: 20px; min-width: 90px; text-align: center; }
  .clock.ticking { background: #3a5a2a; box-shadow: 0 0 0 2px #6c6; }
  .clock.low { background: #5a2a2a; box-shadow: 0 0 0 2px #c66; }
  #move-warning { margin-top: 8px; font-size: 14px; color: #f88; min-height: 18px; }
  #forfeit { margin-top: 12px; padding: 8px 14px; border-radius: 8px; border: none;
             background: #a33; color: #fff; cursor: pointer; display: none; }
  #forfeit:disabled { background: #555; cursor: default; }
</style>
</head>
<body>
<h2>♟️ Chess Callout</h2>
<div id="players">Loading…</div>
<div id="clocks">
  <div id="clock-white" class="clock">White 5:00</div>
  <div id="clock-black" class="clock">Black 5:00</div>
</div>
<div id="move-warning"></div>
<div id="board"></div>
<div id="status"></div>
<button id="forfeit" onclick="claimForfeit()">Claim win (opponent inactive)</button>

<script>
const token = "{{ token }}";
const PIECES = {
  'p':'♟','r':'♜','n':'♞','b':'♝','q':'♛','k':'♚',
  'P':'♙','R':'♖','N':'♘','B':'♗','Q':'♕','K':'♔'
};
let selected = null;
let currentFen = null;
let myColor = null;

// Clock state, resynced from the server on every /state poll (every 3s)
// and ticked smoothly client-side every 250ms in between, so the
// displayed numbers don't visibly jump/stall between polls. The SERVER
// is always the source of truth for actually ending a game on time —
// this is display-only.
let clockState = null; // { whiteRemainingMs, blackRemainingMs, turn, moveDeadlineAtMs, syncedAtClientMs }

function formatClock(ms) {
  if (ms < 0) ms = 0;
  const totalSeconds = Math.ceil(ms / 1000);
  const m = Math.floor(totalSeconds / 60);
  const s = totalSeconds % 60;
  return `${m}:${String(s).padStart(2, '0')}`;
}

function tickClocks() {
  if (!clockState) return;
  const elapsedSinceSync = Date.now() - clockState.syncedAtClientMs;

  const whiteMs = clockState.turn === 'white' ? clockState.whiteRemainingMs - elapsedSinceSync : clockState.whiteRemainingMs;
  const blackMs = clockState.turn === 'black' ? clockState.blackRemainingMs - elapsedSinceSync : clockState.blackRemainingMs;

  const whiteEl = document.getElementById('clock-white');
  const blackEl = document.getElementById('clock-black');
  whiteEl.textContent = `White ${formatClock(whiteMs)}`;
  blackEl.textContent = `Black ${formatClock(blackMs)}`;
  whiteEl.className = 'clock' + (clockState.turn === 'white' ? ' ticking' : '') + (whiteMs < 30000 ? ' low' : '');
  blackEl.className = 'clock' + (clockState.turn === 'black' ? ' ticking' : '') + (blackMs < 30000 ? ' low' : '');

  const moveRemainingMs = clockState.moveDeadlineAtMs - Date.now();
  const warningEl = document.getElementById('move-warning');
  if (clockState.turn === myColor && moveRemainingMs > 0 && moveRemainingMs < 20000) {
    warningEl.textContent = `Move within ${Math.ceil(moveRemainingMs / 1000)}s or you'll auto-lose this game`;
  } else {
    warningEl.textContent = '';
  }
}
setInterval(tickClocks, 250);

function fenToGrid(fen) {
  const rows = fen.split(' ')[0].split('/');
  const grid = [];
  for (const row of rows) {
    const line = [];
    for (const ch of row) {
      if (/\\d/.test(ch)) { for (let i = 0; i < parseInt(ch); i++) line.push(''); }
      else line.push(ch);
    }
    grid.push(line);
  }
  return grid;
}

function squareName(fileIdx, rankRow) {
  const file = 'abcdefgh'[fileIdx];
  const rank = 8 - rankRow;
  return `${file}${rank}`;
}

function render(fen) {
  currentFen = fen;
  const grid = fenToGrid(fen);
  const boardEl = document.getElementById('board');
  boardEl.innerHTML = '';
  const rowOrder = myColor === 'black' ? [...Array(8).keys()].reverse() : [...Array(8).keys()];
  const colOrder = myColor === 'black' ? [...Array(8).keys()].reverse() : [...Array(8).keys()];
  for (const r of rowOrder) {
    for (const f of colOrder) {
      const sq = document.createElement('div');
      const name = squareName(f, r);
      sq.className = 'sq ' + ((r + f) % 2 === 0 ? 'light' : 'dark');
      if (name === selected) sq.className += ' selected';
      const pieceChar = grid[r][f];
      if (pieceChar) {
        const isWhitePiece = pieceChar === pieceChar.toUpperCase();
        const pieceSpan = document.createElement('span');
        pieceSpan.className = isWhitePiece ? 'piece-white' : 'piece-black';
        pieceSpan.textContent = PIECES[pieceChar];
        sq.appendChild(pieceSpan);
      }
      sq.onclick = () => onSquareClick(name);
      boardEl.appendChild(sq);
    }
  }
}

function badge(isNew) {
  return isNew ? '<span class="badge">NEW</span>' : '';
}

async function refresh() {
  const res = await fetch(`/api/game/${token}/state`);
  const data = await res.json();
  if (data.error) {
    document.getElementById('players').textContent = data.error;
    return;
  }
  myColor = data.you.color;
  document.getElementById('players').innerHTML =
    `You: <b>${data.you.username}</b> (${data.you.color})${badge(data.you.is_new)}` +
    ` &nbsp;vs&nbsp; ` +
    `<b>${data.opponent.username}</b> (${data.opponent.color})${badge(data.opponent.is_new)}`;

  clockState = {
    whiteRemainingMs: data.white_remaining_ms,
    blackRemainingMs: data.black_remaining_ms,
    turn: data.turn,
    moveDeadlineAtMs: data.move_deadline_at_ms,
    syncedAtClientMs: Date.now(),
  };
  tickClocks();

  let statusText;
  const forfeitBtn = document.getElementById('forfeit');
  if (data.status === 'active') {
    statusText = data.turn === myColor ? "Your move" : `Waiting for ${data.opponent.username}…`;

    const isOpponentsTurn = data.turn !== myColor;
    const forfeitAt = new Date(data.forfeit_eligible_at);
    if (isOpponentsTurn && Date.now() >= forfeitAt.getTime()) {
      forfeitBtn.style.display = 'block';
      forfeitBtn.disabled = false;
      forfeitBtn.textContent = 'Claim win (opponent inactive)';
    } else if (isOpponentsTurn) {
      forfeitBtn.style.display = 'block';
      forfeitBtn.disabled = true;
      const minsLeft = Math.ceil((forfeitAt.getTime() - Date.now()) / 60000);
      forfeitBtn.textContent = `Claim win available in ~${minsLeft} min`;
    } else {
      forfeitBtn.style.display = 'none';
    }
  } else {
    statusText = `Game over: ${data.status}`;
    forfeitBtn.style.display = 'none';
    clockState = null; // stop the clock from ticking once the game's decided
    document.getElementById('move-warning').textContent = '';
  }
  document.getElementById('status').textContent = statusText;
  render(data.fen);
}

async function onSquareClick(name) {
  if (!selected) { selected = name; render(currentFen); return; }
  if (selected === name) { selected = null; render(currentFen); return; }

  const move = selected + name;
  selected = null;
  const res = await fetch(`/api/game/${token}/move`, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({move})
  });
  const data = await res.json();
  if (data.error) { document.getElementById('status').textContent = data.error; }
  refresh();
}

async function claimForfeit() {
  const res = await fetch(`/api/game/${token}/claim_forfeit`, { method: 'POST' });
  const data = await res.json();
  if (data.error) { document.getElementById('status').textContent = data.error; }
  refresh();
}

refresh();
setInterval(refresh, 3000);
</script>
</body>
</html>
"""


def _authenticate(token: str):
    """Decode + validate a player token against the actual game row.
    Returns (game, payload) or (None, error_message)."""
    payload = game_tokens.verify_player_token(token)
    if not payload:
        return None, "Invalid or tampered link."

    game = db.get_game_by_id(payload["game_id"])
    if not game:
        return None, "Game not found."

    expected_user_id = game["white_id"] if payload["color"] == "white" else game["black_id"]
    if payload["user_id"] != expected_user_id:
        return None, "This link no longer matches this game."

    return game, payload


@app.route("/game/<token>")
def game_page(token):
    return render_template_string(BOARD_PAGE, token=token)


@app.route("/api/game/<token>/state")
def game_state(token):
    game, payload = _authenticate(token)
    if not game:
        return jsonify({"error": payload}), 403

    # Self-healing: the bot normally moves as a side-effect of your own
    # /move request. If that ever silently failed (a bug, a transient
    # DB error), the game would otherwise be stuck on "bot's turn"
    # forever with nothing to retry it. Since the frontend polls this
    # endpoint every 3 seconds anyway, checking-and-nudging here means
    # a failed bot move gets automatically retried on the very next
    # poll instead of requiring the human to make another move first.
    # play_bot_move_if_needed() is a no-op if it's not actually the
    # bot's turn, so this is safe to call unconditionally.
    try:
        refreshed = play_bot_move_if_needed(game["id"])
        if refreshed:
            game = refreshed
    except Exception:
        print(f"WARNING: bot move failed for game {game['id']} (state poll retry)")
        traceback.print_exc()

    # Chess clock check — runs on EVERY poll by EITHER player, not just
    # the one whose turn it is. This is what makes the timeout fire
    # consistently regardless of whose connection is slow: as long as
    # one side's browser is still polling, a timeout gets caught within
    # ~3 seconds of the deadline, even if the other side has gone dark.
    game = check_and_apply_timeout(game)

    # Daily deadline check — only for qualifying (callout/scheduled)
    # games, and only auto-resolves the "neither of us showed up" case.
    # If exactly one side engaged, this leaves the game alone — that
    # case needs a manual claim via /claim_forfeit instead.
    game = check_and_void_if_missed_deadline(game)

    my_color = payload["color"]
    my_id = payload["user_id"]
    opp_color = "black" if my_color == "white" else "white"
    opp_id = game["black_id"] if my_color == "white" else game["white_id"]

    me = db.get_user_by_id(my_id)
    opponent = db.get_user_by_id(opp_id)
    board = chess.Board(game["fen"])

    last_move_at = datetime.fromisoformat(game["updated_at"])
    forfeit_eligible_at = last_move_at + timedelta(minutes=config.FORFEIT_TIMEOUT_MINUTES)

    return jsonify({
        "fen": game["fen"],
        "status": game["status"],
        "turn": "white" if board.turn else "black",
        "you": {"username": me["username"], "color": my_color, "is_new": db.is_new_player(my_id)},
        "opponent": {"username": opponent["username"], "color": opp_color, "is_new": db.is_new_player(opp_id)},
        "forfeit_eligible_at": forfeit_eligible_at.isoformat(),
        **clock_display_fields(game),
    })


@app.route("/api/game/<token>/move", methods=["POST"])
def make_move(token):
    game, payload = _authenticate(token)
    if not game:
        return jsonify({"error": payload}), 403

    if game["status"] != "active":
        return jsonify({"error": "game not active"}), 400

    # Catch a move that arrives after time already ran out, rather than
    # accepting a late move. Same check as /state, just also gating
    # whether this move is even allowed to be processed.
    game = check_and_apply_timeout(game)
    if game["status"] != "active":
        return jsonify({"error": "time's up", "status": game["status"]}), 400

    board = chess.Board(game["fen"])
    is_my_turn = (board.turn and payload["color"] == "white") or (not board.turn and payload["color"] == "black")
    if not is_my_turn:
        return jsonify({"error": "not your turn"}), 403

    move_uci = request.json.get("move", "")
    try:
        move = chess.Move.from_uci(move_uci)
    except ValueError:
        return jsonify({"error": "invalid move"}), 400

    if move not in board.legal_moves:
        return jsonify({"error": "illegal move"}), 400

    mover_is_white = board.turn
    board.push(move)
    new_fen = board.fen()

    status = "active"
    winner_id = None
    if board.is_checkmate():
        status = "white_won" if not board.turn else "black_won"
        winner_id = game["white_id"] if status == "white_won" else game["black_id"]
    elif board.is_stalemate() or board.is_insufficient_material() or board.is_seventyfive_moves():
        status = "draw"

    clock_update = clock_update_after_move(game, mover_is_white)
    db.supabase.table("games").update(
        {"fen": new_fen, "updated_at": datetime.now(timezone.utc).isoformat(), **clock_update}
    ).eq("id", game["id"]).execute()

    if status != "active":
        db.finish_game(game["id"], status, winner_id)
        game["status"] = status
        apply_game_result(game)
    else:
        # If this move handed the turn to the bot, it plays immediately —
        # no separate request needed. If it fails for any reason, don't
        # let that break the human's own successful move; the /state
        # self-healing check above will retry it on the next poll.
        try:
            play_bot_move_if_needed(game["id"])
        except Exception:
            print(f"WARNING: bot move failed for game {game['id']} (will retry on next /state poll)")
            traceback.print_exc()

    return jsonify({"fen": new_fen, "status": status})


@app.route("/api/game/<token>/claim_forfeit", methods=["POST"])
def claim_forfeit(token):
    game, payload = _authenticate(token)
    if not game:
        return jsonify({"error": payload}), 403
    if game["status"] != "active":
        return jsonify({"error": "game not active"}), 400

    # Path 1: daily-deadline no-show claim (callout/scheduled games only,
    # exactly one side ever moved) — checked first since it's the more
    # specific rule for these game types.
    if game.get("origin") in config.QUALIFYING_GAME_ORIGINS:
        eligible, message = deadline_noshow_claim_eligible(game, payload["color"])
        if eligible:
            status = "white_won" if payload["color"] == "white" else "black_won"
            winner_id = game["white_id"] if payload["color"] == "white" else game["black_id"]
            db.finish_game(game["id"], status, winner_id)
            game["status"] = status
            apply_game_result(game)
            return jsonify({"status": status, "reason": "deadline_noshow"})
        # "Neither played" and "wrong claimant" are hard blocks specific
        # to this rule — return them directly instead of falling through
        # to the general check below, which would give a confusing or
        # simply wrong error for these two cases.
        if "Neither of you played" in message or "the one who can claim" in message:
            return jsonify({"error": message}), 400

    # Path 2: general inactivity-based claim — applies to every game
    # type (including /random), and to callout/scheduled games once
    # both sides have actually engaged (ply >= 2).
    board = chess.Board(game["fen"])
    is_my_turn = (board.turn and payload["color"] == "white") or (not board.turn and payload["color"] == "black")
    if is_my_turn:
        return jsonify({"error": "it's your turn — you can't claim a forfeit right now"}), 400

    last_move_at = datetime.fromisoformat(game["updated_at"])
    elapsed_minutes = (datetime.now(timezone.utc) - last_move_at).total_seconds() / 60
    if elapsed_minutes < config.FORFEIT_TIMEOUT_MINUTES:
        remaining = config.FORFEIT_TIMEOUT_MINUTES - elapsed_minutes
        return jsonify({"error": f"opponent still has ~{remaining:.1f} more minutes before you can claim a forfeit"}), 400

    status = "white_won" if payload["color"] == "white" else "black_won"
    winner_id = game["white_id"] if payload["color"] == "white" else game["black_id"]
    db.finish_game(game["id"], status, winner_id)
    game["status"] = status
    apply_game_result(game)

    return jsonify({"status": status})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
