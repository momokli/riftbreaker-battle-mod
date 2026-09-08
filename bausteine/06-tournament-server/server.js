#!/usr/bin/env node
/**
 * Baustein 06 — Tournament-Server
 *
 * Zentraler Server der Turnier-Architektur (docs/concept.md):
 * N Spielinstanzen (je Lua-Mod + Trainer-DLL) kommunizieren über einen
 * Relay-Client mit EINEM zentralen Tournament-Server. Rundenbasiert, kein
 * Echtzeit — Pull-/Polling-Modell.
 *
 * NUR node:http — keine npm-Dependencies, In-Memory-State, keine Persistenz.
 *
 * Events folgen dem Vertrag aus trainer/protocol.md:
 *   game -> server: score_update, wave_sent, wave_received, round_start,
 *                   round_end, match_end   (eingeliefert per POST /event)
 *   server -> game: round_start, incoming_wave, round_end, match_end
 *                   (Zustellung per GET /poll/:player_id, Outbox leert sich)
 *
 * Start: node server.js   (PORT via env, Default 8080)
 */
'use strict';

const http = require('http');

const PORT = Number(process.env.PORT || 8080);

// Nach der letzten Runde wartet der Server kurz (Settle-Fenster), damit die
// Clients ihre finalen score_update/round_end-Meldungen einliefern können,
// bevor match_end mit korrektem winner/reason erzeugt wird.
const SETTLE_MS = 3000;

const MAX_BODY_BYTES = 64 * 1024;

/** Erlaubte game->server Event-Typen (trainer/protocol.md, Tabelle oben). */
const GAME_EVENT_TYPES = new Set([
  'score_update',
  'wave_sent',
  'wave_received',
  'round_start',
  'round_end',
  'match_end',
]);

/** Globaler In-Memory-State. */
const players = new Map();  // player_id -> { player_id, registered_at }
const matches = new Map();  // match_id  -> match (siehe createMatch)

let matchCounter = 0;

function now() {
  return Date.now();
}

function isNum(v) {
  return typeof v === 'number' && Number.isFinite(v);
}

function isPosInt(v) {
  return isNum(v) && Number.isInteger(v) && v >= 1;
}

function makeMatchId() {
  matchCounter += 1;
  return 'm' + matchCounter.toString(36) + '-' + now().toString(36);
}

function createMatch(playersArr, roundsTotal) {
  const match = {
    match_id: null,
    players: playersArr,           // [playerA, playerB]
    rounds_total: roundsTotal,
    round: 0,                      // aktuelle Rundennummer (0 = noch keine)
    status: 'waiting',             // waiting | active | settling | finished
    winner: null,
    reason: null,
    scoreboard: {},                // player_id -> { score, resources?, wave?, at }
    outbox: { [playersArr[0]]: [], [playersArr[1]]: [] }, // player_id -> [events]
    timer: null,                   // round timer handle
    settle_timer: null,            // settle handle nach letzter Runde
    created_at: now(),
  };
  match.match_id = makeMatchId();
  return match;
}

/** Event in die Outbox eines Spielers legen. */
function deliver(match, playerId, event) {
  match.outbox[playerId].push(event);
}

/** Event in BEIDE Outboxen legen (Rundenevents, match_end). */
function broadcast(match, event) {
  for (const pid of match.players) deliver(match, pid, event);
}

function scoreOf(match, pid) {
  const sb = match.scoreboard[pid];
  return sb && isNum(sb.score) ? sb.score : 0;
}

function roundSummary(match) {
  const summary = {};
  for (const pid of match.players) summary['score_' + pid] = scoreOf(match, pid);
  return summary;
}

function cancelTimers(match) {
  if (match.timer) { clearTimeout(match.timer); match.timer = null; }
  if (match.settle_timer) { clearTimeout(match.settle_timer); match.settle_timer = null; }
}

/** Rundentimer abgelaufen: round_end in beide Outboxen, ggf. Settle starten. */
function onRoundTimeout(match) {
  match.timer = null;
  const round = match.round;
  broadcast(match, {
    event: 'round_end',
    round,
    summary: roundSummary(match),
    t: now(),
  });
  log(`[server] round_end round=${round} match=${match.match_id} (auto, timer)`);
  if (match.round >= match.rounds_total) {
    match.status = 'settling';
    match.settle_timer = setTimeout(() => onSettle(match), SETTLE_MS);
    log(`[server] match=${match.match_id}: letzte Runde erreicht, match_end in ${SETTLE_MS}ms`);
  } else {
    match.status = 'waiting';
  }
}

/** Settle-Fenster vorbei: Sieger aus dem Scoreboard bestimmen, match_end senden. */
function onSettle(match) {
  match.settle_timer = null;
  const [a, b] = match.players;
  const sa = scoreOf(match, a);
  const sb = scoreOf(match, b);
  let winner = null;
  let reason = 'score';
  if (sa > sb) winner = a;
  else if (sb > sa) winner = b;
  else reason = 'draw';
  match.winner = winner;
  match.reason = reason;
  match.status = 'finished';
  broadcast(match, {
    event: 'match_end',
    winner,
    reason,
    t: now(),
  });
  log(`[server] match_end match=${match.match_id} winner=${winner} reason=${reason} (${a}=${sa}, ${b}=${sb})`);
}

/** Spielseitig gemeldetes match_end (z. B. base_destroyed): sofort beenden. */
function finishByGame(match, ev) {
  cancelTimers(match);
  match.winner = ev.winner;
  match.reason = typeof ev.reason === 'string' ? ev.reason : 'game';
  match.status = 'finished';
  broadcast(match, {
    event: 'match_end',
    winner: match.winner,
    reason: match.reason,
    t: now(),
  });
}

// ---------------------------------------------------------------------------
// HTTP-Teil
// ---------------------------------------------------------------------------

function sendJson(res, code, obj) {
  const body = JSON.stringify(obj);
  res.writeHead(code, {
    'Content-Type': 'application/json; charset=utf-8',
    'Content-Length': Buffer.byteLength(body),
  });
  res.end(body);
}

function sendError(res, code, message, extra) {
  const body = { error: message };
  if (extra !== undefined) body.type = extra;
  sendJson(res, code, body);
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    let size = 0;
    const chunks = [];
    req.on('data', (c) => {
      size += c.length;
      if (size > MAX_BODY_BYTES) {
        reject(new Error('body too large'));
        req.destroy();
        return;
      }
      chunks.push(c);
    });
    req.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')));
    req.on('error', reject);
  });
}

async function readJsonBody(req) {
  const raw = await readBody(req);
  if (raw.trim() === '') return {};
  try {
    return JSON.parse(raw);
  } catch (e) {
    throw new Error('invalid JSON body');
  }
}

function handleRegister(req, res) {
  readJsonBody(req).then((body) => {
    const pid = body.player_id;
    if (typeof pid !== 'string' || pid.length === 0 || pid.length > 64) {
      sendError(res, 400, 'player_id required (string 1..64)');
      return;
    }
    if (players.has(pid)) {
      sendJson(res, 200, { player_id: pid, existed: true });
      return;
    }
    players.set(pid, { player_id: pid, registered_at: now() });
    log(`[server] register player=${pid}`);
    sendJson(res, 200, { player_id: pid, existed: false });
  }).catch((e) => sendError(res, 400, e.message));
}

function handleMatchCreate(req, res) {
  readJsonBody(req).then((body) => {
    const p = body.players;
    if (!Array.isArray(p) || p.length !== 2 ||
        typeof p[0] !== 'string' || typeof p[1] !== 'string' || p[0] === p[1] ||
        p[0].length === 0 || p[1].length === 0) {
      sendError(res, 400, 'players required: [playerA, playerB] (2 distinct ids)');
      return;
    }
    for (const pid of p) {
      if (!players.has(pid)) {
        sendError(res, 400, 'player not registered', pid);
        return;
      }
    }
    let roundsTotal = 3;
    if (body.rounds !== undefined) {
      if (!isPosInt(body.rounds) || body.rounds > 100) {
        sendError(res, 400, 'rounds must be an integer 1..100');
        return;
      }
      roundsTotal = body.rounds;
    }
    const match = createMatch(p, roundsTotal);
    matches.set(match.match_id, match);
    log(`[server] match create id=${match.match_id} players=${p.join(',')} rounds=${roundsTotal}`);
    sendJson(res, 200, {
      match_id: match.match_id,
      players: match.players,
      rounds_total: match.rounds_total,
      status: match.status,
    });
  }).catch((e) => sendError(res, 400, e.message));
}

function publicState(match) {
  return {
    match_id: match.match_id,
    players: match.players,
    status: match.status,
    round: match.round,
    rounds_total: match.rounds_total,
    winner: match.winner,
    reason: match.reason,
    scoreboard: match.scoreboard,
  };
}

function handleMatchState(req, res, matchId) {
  const match = matches.get(matchId);
  if (!match) { sendError(res, 404, 'match not found', matchId); return; }
  sendJson(res, 200, publicState(match));
}

function handlePoll(req, res, playerId) {
  if (!players.has(playerId)) { sendError(res, 404, 'player not found', playerId); return; }
  let events = [];
  for (const match of matches.values()) {
    if (match.outbox[playerId] && match.outbox[playerId].length > 0) {
      events = events.concat(match.outbox[playerId]);
      match.outbox[playerId] = [];
    }
  }
  sendJson(res, 200, { player_id: playerId, events });
}

function handleEvent(req, res) {
  readJsonBody(req).then((body) => {
    const match = matches.get(body.match_id);
    if (!match) { sendError(res, 404, 'match not found', body.match_id); return; }
    const pid = body.player_id;
    if (!match.players.includes(pid)) {
      sendError(res, 400, 'player not in match', pid);
      return;
    }
    const ev = body.event;
    if (ev === null || typeof ev !== 'object') {
      sendError(res, 400, 'event object required');
      return;
    }
    const type = ev.type;
    if (!GAME_EVENT_TYPES.has(type)) {
      sendError(res, 400, 'invalid event type', type);
      return;
    }

    const other = match.players.find((x) => x !== pid);
    const scb = match.scoreboard;

    switch (type) {
      case 'score_update': {
        if (!isNum(ev.score) || ev.score < 0) {
          sendError(res, 400, 'invalid event payload: score (number >= 0) required', type);
          return;
        }
        const entry = {
          score: ev.score,
          at: now(),
        };
        if (ev.resources !== undefined && ev.resources !== null && typeof ev.resources === 'object') {
          entry.resources = ev.resources;
        }
        if (isNum(ev.wave)) entry.wave = ev.wave;
        scb[pid] = entry;
        break;
      }
      case 'wave_sent': {
        if (!isPosInt(ev.level)) {
          sendError(res, 400, 'invalid event payload: level (integer >= 1) required', type);
          return;
        }
        const delay = isNum(ev.delay_s) && ev.delay_s >= 0 ? ev.delay_s : 5;
        deliver(match, other, {
          event: 'incoming_wave',
          level: ev.level,
          from: pid,
          delay_s: delay,
          t: now(),
        });
        log(`[server] wave_sent ${pid} -> ${other} level=${ev.level} match=${match.match_id}`);
        break;
      }
      case 'wave_received': {
        if (!isPosInt(ev.level)) {
          sendError(res, 400, 'invalid event payload: level (integer >= 1) required', type);
          return;
        }
        // Quittung der Gegner-Welle — Server protokolliert sie nur.
        break;
      }
      case 'round_start': {
        if (!isPosInt(ev.round)) {
          sendError(res, 400, 'invalid event payload: round (integer >= 1) required', type);
          return;
        }
        break; // Spielseitige Bestätigung — Server protokolliert sie nur.
      }
      case 'round_end': {
        if (!isPosInt(ev.round)) {
          sendError(res, 400, 'invalid event payload: round (integer >= 1) required', type);
          return;
        }
        break; // Spielseitige Auswertung — Score kommt per score_update.
      }
      case 'match_end': {
        if (typeof ev.winner !== 'string' || ev.winner.length === 0) {
          sendError(res, 400, 'invalid event payload: winner required', type);
          return;
        }
        finishByGame(match, ev);
        log(`[server] match_end (game) match=${match.match_id} winner=${ev.winner} reason=${match.reason}`);
        break;
      }
      default:
        sendError(res, 400, 'invalid event type', type);
        return;
    }
    sendJson(res, 200, { ok: true, event: type });
  }).catch((e) => sendError(res, 400, e.message));
}

function handleRoundStart(req, res) {
  readJsonBody(req).then((body) => {
    const match = matches.get(body.match_id);
    if (!match) { sendError(res, 404, 'match not found', body.match_id); return; }
    if (match.status === 'finished' || match.status === 'settling') {
      sendError(res, 409, 'match already finished');
      return;
    }
    if (match.status === 'active') {
      sendError(res, 409, 'round already running');
      return;
    }
    if (match.round >= match.rounds_total) {
      sendError(res, 409, 'round limit reached');
      return;
    }
    let duration = 60;
    if (body.duration_s !== undefined) {
      if (!isNum(body.duration_s) || body.duration_s < 1 || body.duration_s > 3600) {
        sendError(res, 400, 'duration_s must be a number 1..3600');
        return;
      }
      duration = body.duration_s;
    }
    const round = match.round + 1;
    match.round = round;
    match.status = 'active';
    broadcast(match, {
      event: 'round_start',
      round,
      duration_s: duration,
      phase: 'planning',
      t: now(),
    });
    match.timer = setTimeout(() => onRoundTimeout(match), duration * 1000);
    log(`[server] round_start match=${match.match_id} round=${round} duration_s=${duration}`);
    sendJson(res, 200, { ok: true, round, duration_s: duration });
  }).catch((e) => sendError(res, 400, e.message));
}

function route(req, res) {
  const url = new URL(req.url, 'http://localhost');
  const parts = url.pathname.split('/').filter(Boolean);

  // POST /register
  if (req.method === 'POST' && parts.length === 1 && parts[0] === 'register') {
    return handleRegister(req, res);
  }
  // POST /match/create
  if (req.method === 'POST' && parts.length === 2 && parts[0] === 'match' && parts[1] === 'create') {
    return handleMatchCreate(req, res);
  }
  // GET /match/:id/state
  if (req.method === 'GET' && parts.length === 3 && parts[0] === 'match' && parts[2] === 'state') {
    return handleMatchState(req, res, decodeURIComponent(parts[1]));
  }
  // POST /event
  if (req.method === 'POST' && parts.length === 1 && parts[0] === 'event') {
    return handleEvent(req, res);
  }
  // GET /poll/:player_id
  if (req.method === 'GET' && parts.length === 2 && parts[0] === 'poll') {
    return handlePoll(req, res, decodeURIComponent(parts[1]));
  }
  // POST /round/start
  if (req.method === 'POST' && parts.length === 2 && parts[0] === 'round' && parts[1] === 'start') {
    return handleRoundStart(req, res);
  }

  if (req.method === 'GET' && (parts.length === 0 || parts[0] === 'health')) {
    return sendJson(res, 200, { ok: true, service: 'tournament-server', players: players.size, matches: matches.size });
  }
  sendError(res, 404, 'not found');
}

function log(msg) {
  process.stdout.write(msg + '\n');
}

const server = http.createServer((req, res) => {
  try {
    route(req, res);
  } catch (e) {
    sendError(res, 400, e.message);
  }
});

server.listen(PORT, () => {
  log(`[server] tournament-server listening on port ${PORT} (settle_ms=${SETTLE_MS})`);
});
