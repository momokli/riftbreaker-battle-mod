#!/usr/bin/env node
/**
 * Baustein 06 — Mock-Client (simulierte Spielinstanz)
 *
 * Simuliert eine Spielinstanz (Lua-Mod + Trainer-DLL) gegen den
 * Tournament-Server:
 *
 *   registrieren -> Match joinen (Creator legt Match an) ->
 *   Loop: poll -> Events verarbeiten (incoming_wave loggen) ->
 *         periodisch score_update -> wave_received quittieren
 *
 * Seedbare Aktionen: Der Creator (--creator) schickt pro Runde eine
 * `wave_sent` (Attacker), der Gegner empfängt sie als `incoming_wave` und
 * quittiert mit `wave_received`. Beide bestätigen Rundenbeginn/-ende gemäß
 * protocol.md und melden kumulierte Scores per `score_update`.
 *
 * NUR Node-Standardbibliothek (node:http, fs). Keine Dependencies.
 *
 * Aufruf:
 *   node mock_client.js --player <id> [Optionen]
 *
 * Optionen:
 *   --url http://host:port   Server-URL (Default http://127.0.0.1:8080)
 *   --creator                Legt das Match an, schreibt match.json, startet Runden
 *   --opponent <id>          Gegner-ID (Creator, Default: player_b)
 *   --rounds N               Rundenzahl des Matches (Default 2)
 *   --duration S             Rundendauer in Sekunden (Default 5)
 *   --no-attack              Creator schickt KEINE Wellen
 *   --tick MS                Poll-Intervall (Default 800)
 *   --state-dir DIR          Koordinationsdateien (match.json, <id>.ready, summary.<id>.json)
 *
 * Exit: 0 bei match_end empfangen, 1 bei Fehler, 2 bei Timeout.
 */
'use strict';

const http = require('http');
const fs = require('fs');
const path = require('path');

// ----------------------------- Argumente ----------------------------------

const args = process.argv.slice(2);
function opt(name, dflt) {
  const i = args.indexOf(name);
  return i >= 0 && i + 1 < args.length ? args[i + 1] : dflt;
}
function flag(name) {
  return args.includes(name);
}

const PLAYER = opt('--player', null);
const URL_BASE = opt('--url', 'http://127.0.0.1:8080');
const OPPONENT = opt('--opponent', 'player_b');
const ROUNDS = Number(opt('--rounds', '2'));
const DURATION = Number(opt('--duration', '5'));
const TICK = Number(opt('--tick', '800'));
const STATE_DIR = opt('--state-dir', '.');
const CREATOR = flag('--creator');
const ATTACK = CREATOR && !flag('--no-attack');

if (!PLAYER) {
  console.error('usage: node mock_client.js --player <id> [--creator] [--opponent <id>] [--url ...] [--rounds N] [--duration S] [--state-dir DIR] [--tick MS]');
  process.exit(2);
}

const urlObj = new URL(URL_BASE);
const HOST = urlObj.hostname;
const PORT = Number(urlObj.port || 80);

// ----------------------------- HTTP-Helfer ---------------------------------

function req(method, apiPath, body) {
  return new Promise((resolve, reject) => {
    const data = body !== undefined ? JSON.stringify(body) : null;
    const r = http.request({
      host: HOST,
      port: PORT,
      method,
      path: apiPath,
      headers: {
        'Content-Type': 'application/json',
        'Content-Length': data !== null ? Buffer.byteLength(data) : 0,
      },
    }, (res) => {
      let buf = '';
      res.on('data', (c) => { buf += c; });
      res.on('end', () => {
        let json = null;
        try { json = JSON.parse(buf); } catch (e) { /* leer */ }
        if (res.statusCode >= 400) {
          const msg = json && json.error ? json.error : `HTTP ${res.statusCode} ${buf.slice(0, 160)}`;
          const err = new Error(msg);
          err.status = res.statusCode;
          err.body = json;
          reject(err);
          return;
        }
        resolve(json);
      });
    });
    r.on('error', reject);
    if (data !== null) r.write(data);
    r.end();
  });
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const log = (msg) => console.log(`[${PLAYER}] ${msg}`);

// ----------------------------- Zustand -------------------------------------

const state = {
  role: CREATOR ? 'creator' : 'player',
  matchId: null,
  roundsTotal: 0,
  duration: DURATION,
  totalScore: 0,            // kumulierte Punktzahl (monoton steigend)
  currentRound: 0,
  wavesByRound: {},         // round -> [level, ...] (erhaltene incoming_wave)
  rounds: [],               // [{round, score}]
  received: { round_start: 0, incoming_wave: 0, round_end: 0, match_end: 0 },
  winner: null,
  done: false,
};

const matchFile = path.join(STATE_DIR, 'match.json');
const readyFile = path.join(STATE_DIR, `${PLAYER}.ready`);
const summaryFile = path.join(STATE_DIR, `summary.${PLAYER}.json`);

function fileExists(p) {
  try { fs.accessSync(p); return true; } catch (e) { return false; }
}

async function waitForFile(p, what, timeoutMs) {
  const t0 = Date.now();
  while (!fileExists(p)) {
    if (Date.now() - t0 > timeoutMs) {
      log(`FEHLER: Timeout beim Warten auf ${what} (${p})`);
      process.exit(2);
    }
    await sleep(500);
  }
}

async function readMatchFile() {
  const t0 = Date.now();
  while (true) {
    try {
      const j = JSON.parse(fs.readFileSync(matchFile, 'utf8'));
      if (j && j.match_id) return j;
    } catch (e) { /* noch nicht komplett geschrieben */ }
    if (Date.now() - t0 > 20000) {
      log('FEHLER: Timeout beim Lesen von match.json');
      process.exit(2);
    }
    await sleep(300);
  }
}

async function register() {
  const t0 = Date.now();
  while (true) {
    try {
      const j = await req('POST', '/register', { player_id: PLAYER });
      log(`registriert (${j.existed ? 'existierte bereits' : 'neu'})`);
      return;
    } catch (e) {
      if (Date.now() - t0 > 30000) {
        log(`FEHLER: Register-Timeouts — Server erreichbar? (${e.message})`);
        process.exit(1);
      }
      await sleep(1000);
    }
  }
}

async function postEvent(ev) {
  const j = await req('POST', '/event', {
    match_id: state.matchId,
    player_id: PLAYER,
    event: ev,
  });
  return j;
}

// ----------------------------- Aktionen ------------------------------------

async function startRound(n) {
  const j = await req('POST', '/round/start', {
    match_id: state.matchId,
    duration_s: state.duration,
  });
  log(`ROUND_STARTED server ok: round=${j.round} duration_s=${j.duration_s}`);
}

async function sendScoreUpdate() {
  const sc = Math.max(0, state.totalScore);
  await postEvent({
    type: 'score_update',
    score: sc,
    resources: { iron: sc * 2, carbon: Math.floor(sc / 5) },
    wave: state.currentRound,
  });
}

async function handleEvent(ev) {
  switch (ev.event) {
    case 'round_start': {
      state.received.round_start += 1;
      state.currentRound = ev.round;
      if (!state.wavesByRound[ev.round]) state.wavesByRound[ev.round] = [];
      log(`EVENT round_start round=${ev.round} duration_s=${ev.duration_s} phase=${ev.phase}`);
      // Spielseitige Bestätigung (game -> server round_start)
      await postEvent({ type: 'round_start', round: ev.round, phase: ev.phase || 'planning' });
      // Seed: Creator schickt pro Runde genau eine Welle (level = Runde)
      if (ATTACK) {
        await sleep(200);
        const level = ev.round;
        await postEvent({
          type: 'wave_sent',
          level,
          cost: 100 * level,
          score_left: state.totalScore,
        });
        log(`WAVE_SENT level=${level} cost=${100 * level}`);
      }
      break;
    }

    case 'incoming_wave': {
      state.received.incoming_wave += 1;
      const r = state.currentRound > 0 ? state.currentRound : 1;
      if (!state.wavesByRound[r]) state.wavesByRound[r] = [];
      state.wavesByRound[r].push(ev.level);
      log(`INCOMING_WAVE level=${ev.level} from=${ev.from} delay_s=${ev.delay_s} (Runde ${r})`);
      // Quittieren (game -> server wave_received)
      await postEvent({ type: 'wave_received', level: ev.level, from: ev.from });
      log(`ACK wave_received level=${ev.level} from=${ev.from}`);
      break;
    }

    case 'round_end': {
      state.received.round_end += 1;
      const r = ev.round;
      const bonus = (state.wavesByRound[r] || []).reduce((a, l) => a + 50 * l, 0);
      const roundScore = 1000 + bonus; // Basis + 50 pro empfangenem Wellen-Level
      state.totalScore += roundScore;
      state.rounds.push({ round: r, score: roundScore });
      log(`EVENT round_end round=${r} summary=${JSON.stringify(ev.summary)} my_round_score=${roundScore} total=${state.totalScore}`);
      // Spielseitige Auswertung + Scoreboard aktualisieren, bevor der Server
      // nach der letzten Runde den Sieger bestimmt (Settle-Fenster).
      await postEvent({ type: 'round_end', round: r, score: roundScore, survived: true });
      await sendScoreUpdate();
      if (CREATOR && r < state.roundsTotal) {
        await sleep(400);
        await startRound(r + 1);
      }
      break;
    }

    case 'match_end': {
      state.received.match_end += 1;
      state.winner = ev.winner;
      state.done = true;
      log(`MATCH_END winner=${ev.winner} reason=${ev.reason}`);
      break;
    }

    default:
      log(`WARN unbekanntes Server-Event: ${JSON.stringify(ev)}`);
  }
}

async function writeSummary() {
  const summary = {
    player_id: PLAYER,
    role: state.role,
    match_id: state.matchId,
    winner: state.winner,
    total_score: state.totalScore,
    rounds: state.rounds,
    received: state.received,
    exit: state.done ? 0 : 1,
  };
  fs.writeFileSync(summaryFile, JSON.stringify(summary, null, 2));
  return summary;
}

// ----------------------------- Main ----------------------------------------

async function main() {
  log(`start role=${state.role} url=${URL_BASE} rounds=${ROUNDS} duration_s=${DURATION}`);

  if (CREATOR) {
    // 1) Warten, bis der Gegner registriert ist (ready-File), dann selbst registrieren.
    await waitForFile(path.join(STATE_DIR, `${OPPONENT}.ready`), 'Gegner-Registrierung', 40000);
    await register();
    fs.writeFileSync(readyFile, 'ok');

    // 2) Match anlegen (Retry: Gegner muss registriert sein).
    let created = null;
    const t0 = Date.now();
    while (!created) {
      try {
        created = await req('POST', '/match/create', {
          players: [PLAYER, OPPONENT],
          rounds: ROUNDS,
        });
      } catch (e) {
        if (Date.now() - t0 > 20000) {
          log(`FEHLER: Match-Create schlägt fehl (${e.message})`);
          process.exit(1);
        }
        log(`warn: match create retry (${e.message})`);
        await sleep(700);
      }
    }
    state.matchId = created.match_id;
    state.roundsTotal = created.rounds_total;
    fs.writeFileSync(matchFile, JSON.stringify({
      match_id: state.matchId,
      players: created.players,
      rounds: created.rounds_total,
      duration_s: DURATION,
    }));
    log(`match erstellt id=${state.matchId} rounds=${state.roundsTotal}`);

    // 3) Runde 1 starten, dann Poll-Loop.
    await startRound(1);
  } else {
    // Nicht-Creator: registrieren, ready melden, auf Match warten.
    await register();
    fs.writeFileSync(readyFile, 'ok');
    const mf = await readMatchFile();
    state.matchId = mf.match_id;
    state.roundsTotal = mf.rounds;
    log(`match beigetreten id=${state.matchId} rounds=${state.roundsTotal}`);
  }

  // Poll-Loop: poll -> Events verarbeiten -> periodisch score_update.
  const pollT0 = Date.now();
  let lastScoreUpdate = 0;
  while (!state.done) {
    if (Date.now() - pollT0 > 120000) {
      log('FEHLER: Timeout im Poll-Loop (kein match_end nach 120s)');
      await writeSummary().catch(() => {});
      process.exit(2);
    }
    try {
      const res = await req('GET', `/poll/${encodeURIComponent(PLAYER)}`);
      for (const ev of res.events || []) {
        try { await handleEvent(ev); } catch (e) { log(`FEHLER bei Event ${ev.event}: ${e.message}`); process.exit(1); }
      }
    } catch (e) {
      log(`warn: poll fehlgeschlagen (${e.message}) — retry`);
      await sleep(500);
    }
    // Periodischer score_update (unabhängig vom Event-Eingang).
    if (Date.now() - lastScoreUpdate > TICK) {
      lastScoreUpdate = Date.now();
      try { await sendScoreUpdate(); } catch (e) { log(`warn: score_update fehlgeschlagen (${e.message})`); }
    }
    await sleep(TICK);
  }

  const summary = await writeSummary();
  log(`fertig: winner=${summary.winner} rounds=${JSON.stringify(state.rounds)} received=${JSON.stringify(state.received)}`);
  process.exit(0);
}

main().catch((e) => {
  log(`FATAL: ${e.message}`);
  process.exit(1);
});
