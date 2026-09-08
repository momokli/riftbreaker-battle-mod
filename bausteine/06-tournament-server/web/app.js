/* RBBATTLE CONTROL — Web-UI fuer den Tournament-Server (Baustein 06/08).
 *
 * Vanilla JS, keine externen Libraries.
 * - PLAYERS: aus dem SSE-snapshot + player/match-Events des Servers
 * - EVENT FEED: live via GET /stream (SSE), neueste Zeile oben
 * - CONTROLS: POST /event {type:"exec_command", command} an den Ziel-Spieler
 * - match_id/player_id landen in localStorage; Klick auf einen Spieler
 *   uebernimmt dessen id.
 */
'use strict';

const LS_MATCH = 'rbbattle.match_id';
const LS_PLAYER = 'rbbattle.player_id';

const $ = (id) => document.getElementById(id);
const feed = $('feed');
const playerRows = $('playerRows');
const playerEmpty = $('playerEmpty');
const connDot = $('connDot');
const connText = $('connText');
const matchIdInput = $('matchId');
const playerIdInput = $('playerId');
const feedMeta = $('feedMeta');

const players = new Map(); // player_id -> { registered_at }
const matches = new Map(); // match_id  -> match-state (aus snapshot + match-events)

const pad = (n) => String(n).padStart(2, '0');
const timeStr = (ts) => {
  const d = new Date(ts || Date.now());
  return pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds());
};
const clockStr = (ts) => timeStr(ts);

// ---------------------------------------------------------------------------
// Event-Feed
// ---------------------------------------------------------------------------

function feedLine(tag, msgHtml) {
  const row = document.createElement('div');
  row.className = 'row';
  const t = document.createElement('span');
  t.className = 't';
  t.textContent = '[' + timeStr() + ']';
  const tagEl = document.createElement('span');
  tagEl.className = 'tag ' + tag;
  const label = { in: 'IN', out: 'OUT', pl: 'PLAYER', m: 'MATCH', sys: 'SYS' }[tag] || tag;
  tagEl.textContent = label;
  const msg = document.createElement('span');
  msg.className = 'msg';
  if (typeof msgHtml === 'string') {
    msg.textContent = msgHtml;
  } else {
    msg.appendChild(msgHtml);
  }
  row.appendChild(t);
  row.appendChild(tagEl);
  row.appendChild(msg);
  feed.insertBefore(row, feed.firstChild);
  while (feed.children.length > 300) feed.removeChild(feed.lastChild);
  feedMeta.textContent = '(' + feed.children.length + ' zeilen)';
}

function strong(text) {
  const b = document.createElement('b');
  b.textContent = text;
  return b;
}

function fmtPayload(ev) {
  // Aus einem Event-Objekt {type|event, ...payload} eine kompakte
  // "key=value"-Beschreibung bauen (fuer die Feed-Zeile).
  const frag = document.createDocumentFragment();
  let first = true;
  for (const [k, v] of Object.entries(ev || {})) {
    if (k === 'type' || k === 'event') continue;
    if (k === 'command' || k === 'cmd_id') continue;
    const sp = document.createTextNode(first ? '' : ' ');
    frag.appendChild(sp);
    const keyEl = strong(k + '=');
    frag.appendChild(keyEl);
    frag.appendChild(document.createTextNode(String(v)));
    first = false;
  }
  return frag;
}

// ---------------------------------------------------------------------------
// SSE /stream
// ---------------------------------------------------------------------------

function setConn(state) {
  connDot.className = 'dot' + (state === 'live' ? ' live' : state === 'retry' ? ' retry' : '');
  connText.textContent = state === 'live' ? 'stream live' : state === 'retry' ? 'reconnecting …' : 'offline';
}

function handleStreamEvent(data) {
  const ev = typeof data === 'string' ? JSON.parse(data) : data;
  const kind = ev.kind;
  const e = ev.event || {};

  switch (kind) {
    case 'snapshot':
      players.clear();
      matches.clear();
      for (const p of ev.players || []) players.set(p.player_id, p);
      for (const m of ev.matches || []) matches.set(m.match_id, m);
      renderPlayers();
      feedLine('sys', 'snapshot empfangen: ' + players.size + ' spieler, ' + matches.size + ' matches');
      break;

    case 'player':
      if (ev.player_id) {
        players.set(ev.player_id, { player_id: ev.player_id, registered_at: ev.registered_at });
      }
      renderPlayers();
      feedLine('pl', 'player ' + strong(ev.player_id) + ' registriert' + (ev.existed ? ' (existed)' : ''));
      break;

    case 'match':
      if (ev.match_id) {
        matches.set(ev.match_id, { match_id: ev.match_id, players: ev.players,
                                   status: ev.status, rounds_total: ev.rounds_total });
        renderPlayers();
      }
      feedLine('m', 'match ' + strong(ev.match_id) + ' erstellt: ' +
               (ev.players || []).join(' vs ') + ' (rounds=' + (ev.rounds_total != null ? ev.rounds_total : '?') + ')');
      break;

    case 'input': {
      // game -> server (bzw. UI -> server): {type, ...payload}
      const t = e.type || '?';
      const frag = document.createDocumentFragment();
      frag.appendChild(document.createTextNode('POST /event '));
      frag.appendChild(strong(ev.player_id));
      frag.appendChild(document.createTextNode(' type=' + t + ' '));
      if (t === 'exec_command') {
        const c = document.createElement('span');
        c.className = 'cmd';
        c.textContent = '"' + (e.command || '') + '"';
        frag.appendChild(c);
      } else {
        frag.appendChild(fmtPayload(e));
      }
      feedLine('in', frag);
      break;
    }

    case 'delivery': {
      // server -> game (Outbox): {event, ...} — exec_command = control-channel
      const t = e.event || '?';
      const frag = document.createDocumentFragment();
      frag.appendChild(document.createTextNode('→ '));
      frag.appendChild(strong(ev.player_id));
      frag.appendChild(document.createTextNode(' event=' + t + ' '));
      if (t === 'exec_command') {
        const c = document.createElement('span');
        c.className = 'cmd';
        c.textContent = '"' + (e.command || '') + '"';
        frag.appendChild(c);
      } else {
        frag.appendChild(fmtPayload(e));
      }
      feedLine('out', frag);
      break;
    }

    default:
      feedLine('sys', 'stream: kind=' + String(kind));
  }
}

function connectStream() {
  const es = new EventSource('/stream');
  es.onopen = () => setConn('live');
  es.onerror = () => {
    // EventSource verbindet sich selbst neu; wir zeigen nur den Zustand.
    setConn('retry');
  };
  es.onmessage = (msg) => {
    try {
      handleStreamEvent(msg.data);
    } catch (err) {
      feedLine('sys', 'stream parse-fehler: ' + err.message + ' :: ' + String(msg.data).slice(0, 120));
    }
  };
}

// ---------------------------------------------------------------------------
// PLAYERS-Liste
// ---------------------------------------------------------------------------

function playerStatus(pid) {
  const mine = [];
  for (const m of matches.values()) {
    if ((m.players || []).includes(pid)) mine.push(m);
  }
  if (mine.length === 0) return { cls: '', text: 'registered' };
  const m = mine[0];
  return { cls: 'match', text: 'match ' + m.match_id + ' [' + (m.status || '?') + ']'
           + (mine.length > 1 ? ' (+' + (mine.length - 1) + ')' : '') };
}

function renderPlayers() {
  playerRows.textContent = '';
  const list = [...players.values()].sort((a, b) => (a.registered_at || 0) - (b.registered_at || 0));
  playerEmpty.style.display = list.length ? 'none' : '';
  for (const p of list) {
    const tr = document.createElement('tr');
    const tdId = document.createElement('td');
    tdId.className = 'id';
    tdId.textContent = p.player_id;
    tdId.title = 'player_id uebernehmen';
    tdId.addEventListener('click', () => { playerIdInput.value = p.player_id; saveLocal(); });
    const tdSt = document.createElement('td');
    const st = playerStatus(p.player_id);
    tdSt.className = 'st' + (st.cls ? ' ' + st.cls : '');
    tdSt.textContent = st.text;
    const tdReg = document.createElement('td');
    tdReg.textContent = clockStr(p.registered_at);
    tr.appendChild(tdId);
    tr.appendChild(tdSt);
    tr.appendChild(tdReg);
    playerRows.appendChild(tr);
  }
}

// ---------------------------------------------------------------------------
// CONTROLS: exec_command
// ---------------------------------------------------------------------------

function saveLocal() {
  try {
    localStorage.setItem(LS_MATCH, matchIdInput.value.trim());
    localStorage.setItem(LS_PLAYER, playerIdInput.value.trim());
  } catch (e) { /* private mode */ }
}

function loadLocal() {
  try {
    matchIdInput.value = localStorage.getItem(LS_MATCH) || '';
    playerIdInput.value = localStorage.getItem(LS_PLAYER) || '';
  } catch (e) { /* private mode */ }
}

async function sendCommand(command, btn) {
  const matchId = matchIdInput.value.trim();
  const playerId = playerIdInput.value.trim();
  if (!matchId || !playerId) {
    feedLine('sys', 'command ' + command + ' abgebrochen: match_id und player_id fehlen (oben eintragen)');
    return;
  }
  const body = { match_id: matchId, player_id: playerId,
                 event: { type: 'exec_command', command: command } };
  btn.disabled = true;
  try {
    const res = await fetch('/event', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    let data = null;
    try { data = await res.json(); } catch (e) { /* leer */ }
    const frag = document.createDocumentFragment();
    const c = document.createElement('span');
    c.className = 'cmd';
    c.textContent = '"' + command + '"';
    frag.appendChild(document.createTextNode('POST /event '));
    frag.appendChild(c);
    frag.appendChild(document.createTextNode(' → ' + res.status + ' '));
    if (res.ok) {
      const ok = document.createElement('span');
      ok.className = 'sc';
      ok.textContent = 'ok';
      frag.appendChild(ok);
    } else {
      frag.appendChild(document.createTextNode(data && data.error ? data.error : 'fehler'));
    }
    feedLine('sys', frag);
  } catch (err) {
    feedLine('sys', 'POST /event fehlgeschlagen: ' + err.message);
  } finally {
    btn.disabled = false;
  }
}

// ---------------------------------------------------------------------------
// init
// ---------------------------------------------------------------------------

document.querySelectorAll('button.cmd[data-cmd]').forEach((btn) => {
  btn.addEventListener('click', () => sendCommand(btn.dataset.cmd, btn));
});
matchIdInput.addEventListener('input', saveLocal);
playerIdInput.addEventListener('input', saveLocal);
window.addEventListener('beforeunload', saveLocal);

loadLocal();
connectStream();
