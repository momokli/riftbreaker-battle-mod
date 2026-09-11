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
 *                   round_end, match_end, exec_result   (eingeliefert per POST /event)
 *   server -> game: round_start, incoming_wave, round_end, match_end
 *                   (Zustellung per GET /poll/:player_id, Outbox leert sich)
 *   control -> game: exec_command (Kommando von Web-UI/Operator via POST /event,
 *                   wird in die Outbox des Ziel-Spielers gelegt, Relay dispt)
 *
 * Zusaetzlich (Prototyp-Strecke Spiel -> Trainer -> Relay -> Server -> Web-UI):
 *   - GET /stream   SSE-Stream (text/event-stream): alle Events (Registrierungen,
 *                   Match-Änderungen, /event-Eingaenge, Outbox-Zustellungen) als
 *                   `data: <json>` + initiales snapshot
 *   - GET  /*       statische Dateien aus web/ (.html/.js/.css, sonst 404)
 *
 * Start: node server.js   (PORT via env, Default 8080, HOST 0.0.0.0)
 */
'use strict';

const http = require('http');
const fs = require('fs');
const pathMod = require('path');

const PORT = Number(process.env.PORT || 8080);
const HOST = '0.0.0.0';

// Statische Web-UI-Dateien (Baustein 08) — nur .html/.js/.css werden serviert.
const WEB_DIR = pathMod.join(__dirname, 'web');
const STATIC_TYPES = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
};

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
  'exec_result',
]);

/** Control->game Kommandos: kommen von Operator/Web-UI, gehen in die Outbox. */
const CONTROL_COMMAND_TYPES = new Set(['exec_command']);

/** SSE-Zuschauer (GET /stream). */
const sseClients = new Set();
let streamSeq = 0;
let cmdCounter = 0;

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
  pushEvent('delivery', {
    match_id: match.match_id,
    player_id: playerId,
    event,
  });
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
      pushEvent('player', {
        player_id: pid,
        existed: true,
        registered_at: players.get(pid).registered_at,
      });
      sendJson(res, 200, { player_id: pid, existed: true });
      return;
    }
    players.set(pid, { player_id: pid, registered_at: now() });
    log(`[server] register player=${pid}`);
    pushEvent('player', {
      player_id: pid,
      existed: false,
      registered_at: players.get(pid).registered_at,
    });
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
    pushEvent('match', {
      match_id: match.match_id,
      players: match.players,
      rounds_total: match.rounds_total,
      status: match.status,
    });
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
    if (!GAME_EVENT_TYPES.has(type) && !CONTROL_COMMAND_TYPES.has(type)) {
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
      case 'exec_command': {
        // Control-Kanal (Web-UI/Operator): Kommando in die Outbox des
        // Ziel-Spielers legen; der Relay dispt es dort ins Spiel (v0: TODO).
        if (typeof ev.command !== 'string' || ev.command.length === 0 || ev.command.length > 512) {
          sendError(res, 400, 'invalid event payload: command (string 1..512) required', type);
          return;
        }
        cmdCounter += 1;
        deliver(match, pid, {
          event: 'exec_command',
          command: ev.command,
          cmd_id: cmdCounter,
          from: 'control',
          t: now(),
        });
        log(`[server] exec_command control -> ${pid} command="${ev.command}" cmd_id=${cmdCounter} match=${match.match_id}`);
        break;
      }
      case 'exec_result': {
        // Dispatch-Feedback des Relays (Issue #89, AC aus #73): die rbbridge
        // hat auf ein exec_command geantwortet (ok/error) bzw. gar nicht
        // (status=timeout). Der Server haelt keinen eigenen State - er
        // protokolliert und broadcastet das Ergebnis per SSE an die Web-UI
        // (pushEvent unten), damit das Kommando-Feedback sichtbar wird.
        if (typeof ev.command !== 'string' || ev.command.length === 0 || ev.command.length > 512) {
          sendError(res, 400, 'invalid event payload: command (string 1..512) required', type);
          return;
        }
        if (typeof ev.ok !== 'boolean') {
          sendError(res, 400, 'invalid event payload: ok (boolean) required', type);
          return;
        }
        log(`[server] exec_result ${pid} command="${ev.command}" ok=${ev.ok}` +
            `${ev.status ? ' status=' + ev.status : ''} match=${match.match_id}`);
        break;
      }
      default:
        sendError(res, 400, 'invalid event type', type);
        return;
    }
    pushEvent('input', {
      match_id: match.match_id,
      player_id: pid,
      event: ev,
    });
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
  // GET /stream — SSE-Live-Feed (alle Events als data: <json>)
  if (req.method === 'GET' && parts.length === 1 && parts[0] === 'stream') {
    return handleStream(req, res);
  }
  // GET / — Web-UI (web/index.html), Fallback Health-JSON (legacy-Verhalten)
  if (req.method === 'GET' && parts.length === 0) {
    const f = resolveStatic('/');
    if (f) return serveStatic(res, f);
    return healthJson(res);
  }
  // GET /health
  if (req.method === 'GET' && parts.length === 1 && parts[0] === 'health') {
    return healthJson(res);
  }
  // Statische Dateien aus web/ (GET, nur .html/.js/.css, sonst 404)
  if (req.method === 'GET') {
    const f = resolveStatic(url.pathname);
    if (f) return serveStatic(res, f);
  }
  sendError(res, 404, 'not found');
}

function log(msg) {
  process.stdout.write(msg + '\n');
}

// ---------------------------------------------------------------------------
// SSE /stream (Prototyp-Strecke: Live-Feed fuer Web-UI / Beobachter)
// ---------------------------------------------------------------------------

function sseEnvelope(kind, fields) {
  streamSeq += 1;
  return Object.assign({ seq: streamSeq, t: now(), kind }, fields);
}

function sseSend(res, obj) {
  if (res.writableEnded) return false;
  try {
    res.write('data: ' + JSON.stringify(obj) + '\n\n');
    return true;
  } catch (e) {
    return false;
  }
}

/** Ein Event an alle SSE-Zuschauer verteilen (tote Clients rauswerfen). */
function sseBroadcast(obj) {
  for (const res of sseClients) {
    if (!sseSend(res, obj)) {
      sseClients.delete(res);
      try { res.destroy(); } catch (e) { /* egal */ }
    }
  }
}

/** Event als `data: <json>` an alle Zuschauer pushen. */
function pushEvent(kind, fields) {
  sseBroadcast(sseEnvelope(kind, fields));
}

function handleStream(req, res) {
  res.writeHead(200, {
    'Content-Type': 'text/event-stream; charset=utf-8',
    'Cache-Control': 'no-cache, no-transform',
    'Connection': 'keep-alive',
  });
  res.write(': connected\n\n');
  sseClients.add(res);
  res.on('close', () => sseClients.delete(res));
  res.on('error', () => sseClients.delete(res));
  // Initiales snapshot (Players/Matches), danach laufen Live-Events rein.
  sseSend(res, sseEnvelope('snapshot', {
    players: [...players.values()]
      .map((p) => ({ player_id: p.player_id, registered_at: p.registered_at })),
    matches: [...matches.values()].map(publicState),
  }));
  log(`[server] stream client connected (total=${sseClients.size})`);
}

// Heartbeat-Kommentar, damit Proxies die SSE-Verbindung nicht einschlafen lassen.
const sseHeartbeat = setInterval(() => {
  for (const res of sseClients) {
    if (res.writableEnded) {
      sseClients.delete(res);
      continue;
    }
    try {
      res.write(': hb\n\n');
    } catch (e) {
      sseClients.delete(res);
      try { res.destroy(); } catch (e2) { /* egal */ }
    }
  }
}, 15000);
if (sseHeartbeat.unref) sseHeartbeat.unref();

// ---------------------------------------------------------------------------
// Statische Dateien aus web/ (nur .html/.js/.css, sonst 404)
// ---------------------------------------------------------------------------

function resolveStatic(urlPath) {
  const rel = urlPath === '/' ? '/index.html' : urlPath;
  const ext = pathMod.extname(rel).toLowerCase();
  if (!STATIC_TYPES[ext]) return null;
  const abs = pathMod.normalize(pathMod.join(WEB_DIR, rel));
  // Path-Traversal abweisen (../ darf nicht aus web/ herausfuehren).
  if (abs !== WEB_DIR && !abs.startsWith(WEB_DIR + pathMod.sep)) return null;
  if (!fs.existsSync(abs) || !fs.statSync(abs).isFile()) return null;
  return { abs, type: STATIC_TYPES[ext] };
}

function serveStatic(res, file) {
  let body;
  try {
    body = fs.readFileSync(file.abs);
  } catch (e) {
    sendError(res, 404, 'not found');
    return;
  }
  res.writeHead(200, {
    'Content-Type': file.type,
    'Content-Length': body.length,
  });
  res.end(body);
}

function healthJson(res) {
  sendJson(res, 200, {
    ok: true,
    service: 'tournament-server',
    players: players.size,
    matches: matches.size,
  });
}

const server = http.createServer((req, res) => {
  try {
    route(req, res);
  } catch (e) {
    sendError(res, 400, e.message);
  }
});

server.listen(PORT, HOST, () => {
  log(`[server] tournament-server listening on http://${HOST}:${PORT} (settle_ms=${SETTLE_MS})`);
});
