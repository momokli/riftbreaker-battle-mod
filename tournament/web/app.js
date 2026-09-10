/* RIFT BATTLE — Lobby-UI + Spectator-Dashboard
   Pollt GET /state (1 s) und steuert POST /lobby, /ready, /go, /rematch. */

"use strict";

const POLL_MS = 1200;
let me = null; // { world: "A"|"B", player: String } — lokale Identität

const $ = (id) => document.getElementById(id);

async function api(method, path, body) {
  const opt = { method, headers: {} };
  if (body !== undefined) {
    opt.headers["content-type"] = "application/json";
    opt.body = JSON.stringify(body);
  }
  const res = await fetch(path, opt);
  let data = null;
  try { data = await res.json(); } catch (_) { /* leer */ }
  return { status: res.status, data };
}

function ts(t) {
  if (!t) return "------";
  const d = new Date(t);
  const p = (n) => String(n).padStart(2, "0");
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

function fmtUnits(batches) {
  const parts = [];
  for (const b of batches || []) {
    for (const u of b.units || []) {
      parts.push(`${u.count}× ${u.unit}`);
    }
  }
  return parts.length ? parts.join(" · ") : "—";
}

function fmtPending(sends) {
  if (!sends || !sends.length) return "keine eingehenden Sends";
  const n = sends.reduce((a, b) => a + (b.units || []).reduce((x, u) => x + u.count, 0), 0);
  const v = sends.reduce((a, b) => a + (b.value || 0), 0);
  return `⏳ ${n} Einheiten / ${v} Wert in der Queue (nächste Welle)`;
}

function barClass(hp, max) {
  if (max <= 0) return "low";
  const r = hp / max;
  return r <= 0.35 ? "low" : r <= 0.6 ? "mid" : "";
}

function render(state) {
  // Verbindung
  $("conn").textContent = `— live · phase ${state.phase} · runde ${state.round}`;
  $("conn").className = "conn ok";
  $("matchId").textContent = state.match_id;
  $("rematchNo").textContent = state.rematches;
  if ($("modeTag")) $("modeTag").textContent = (state.mode || "duel").toUpperCase();
  $("roundNo").textContent = state.round;
  $("roundsDone").textContent = state.rounds_done ? `(${state.rounds_done} erledigt)` : "";
  $("phaseTag").textContent = state.phase.toUpperCase();
  $("phaseTag").className = state.phase;

  const A = state.teams["A"];
  const B = state.teams["B"];

  // --- Sichtbarkeit: Lobby vs. Dashboard ---
  const lobbyVisible = state.phase === "lobby" || state.phase === "ready";
  $("lobby").classList.toggle("hidden", !lobbyVisible);
  $("dashboard").classList.toggle("hidden", lobbyVisible);

  if (lobbyVisible) {
    // Slots
    const setSlot = (world, slotId) => {
      const t = state.teams[world];
      const el = $(slotId);
      if (t && t.player) {
        el.textContent = `● ${t.player} ${t.ready ? "— READY" : "(nicht ready)"}`;
        el.style.color = t.ready ? "var(--good)" : "var(--accent)";
      } else {
        el.textContent = "— offen —";
        el.style.color = "";
      }
    };
    setSlot("A", "slotA");
    setSlot("B", "slotB");

    // Buttons an lokale Identität koppeln
    for (const w of ["A", "B"]) {
      const mine = me && me.world === w;
      const regBtn = document.querySelector(`button[data-act="register${w}"]`);
      const readyBtn = document.querySelector(`button[data-act="ready${w}"]`);
      const registered = state.teams[w] && state.teams[w].player;
      if (regBtn) regBtn.disabled = !!(registered && mine && state.teams[w].player === me.player);
      if (readyBtn) {
        readyBtn.disabled = !mine || !registered || state.teams[w].ready;
        readyBtn.classList.toggle("primary", mine && registered && !state.teams[w].ready);
      }
    }
    // GO-Bar (wenn beide ready / Phase ready)
    const bothReady = state.phase === "ready" || (A && B && A.ready && B.ready && A.player && B.player);
    $("goBar").classList.toggle("hidden", !bothReady);
    if (bothReady) $("goMsg").textContent = state.phase === "ready" ? "Ready-Check komplett — GO ausstehend." : "Beide Welten ready — GO feuert beim zweiten Ready (AUTO_GO).";
  }

  // --- Dashboard ---
  if (!lobbyVisible) {
    const renderTeam = (w, prefix) => {
      const t = state.teams[w];
      const max = state.hq_hp_start || 100;
      $(`p${w}`).textContent = `WELT ${w}`;
      $(`p${w}2`).textContent = t && t.player ? t.player : "";
      const hp = t ? t.hq_hp : 0;
      const pct = Math.max(0, Math.min(100, (hp / max) * 100));
      $(`hqFill${w}`).style.width = pct + "%";
      $(`hqFill${w}`).className = "hq-fill " + barClass(hp, max);
      $(`hqLabel${w}`).textContent = `HQ ${Math.round(hp)} / ${max}`;
      $(`pending${w}`).textContent = t ? fmtPending(t.pending_sends) : "";
    };
    renderTeam("A", "hqFillA");
    renderTeam("B", "hqFillB");

    // GO-Zustell-Status
    const gs = [];
    for (const w of ["A", "B"]) {
      const g = state.teams[w] && state.teams[w].go_broadcast;
      if (g && g.at) {
        gs.push(`GO→${w}: ${g.ok ? "OK" : "FEHLER " + (g.error || "")}${g.endpoint ? " @" + g.endpoint : ""}`);
      }
    }
    const goEl = $("goStatus");
    if (gs.length) {
      goEl.classList.remove("hidden");
      goEl.classList.toggle("ok-line", gs.every((s) => s.includes("OK")));
      goEl.classList.toggle("err-line", !gs.every((s) => s.includes("OK")));
      goEl.textContent = "▸ " + gs.join("   ");
    } else {
      goEl.classList.add("hidden");
    }

    // Reveal
    const rev = state.reveal;
    const showReveal = rev && rev.incoming && Object.keys(rev.incoming).length > 0;
    $("revealBox").classList.toggle("hidden", !showReveal);
    if (showReveal) {
      $("revealRound").textContent = `— RUNDE ${rev.round}`;
      $("builtA").textContent = rev.built && rev.built["A"] !== undefined ? rev.built["A"].toLocaleString("de-DE") : "?";
      $("builtB").textContent = rev.built && rev.built["B"] !== undefined ? rev.built["B"].toLocaleString("de-DE") : "?";
      $("incomingA").innerHTML = fmtUnits(rev.incoming["A"]).replace(/—/, "") || "nichts";
      $("incomingB").innerHTML = fmtUnits(rev.incoming["B"]).replace(/—/, "") || "nichts";
      for (const w of ["A", "B"]) {
        const html = fmtUnits(rev.incoming[w]);
        $(`incoming${w}`).innerHTML = html === "—" ? "— (leer)" : html.replace(/(\d+×\s+\S+)/g, '<span class="unit">$1</span>');
      }
    }

    // Winner/Rematch
    const over = state.phase === "finished";
    $("winnerBox").classList.toggle("hidden", !over);
    if (over) {
      const w = state.winner;
      const name = w ? state.teams[w].player || ("Welt " + w) : "DRAW?";
      $("winnerName").textContent = `${name} (Welt ${w})`;
    }
  }

  // --- Feed ---
  if (state.feed && state.feed.length) {
    $("feed").innerHTML = state.feed
      .map((e) => `<span class="t">[${ts(e.t)}]</span> <span class="${e.kind}">${esc(e.msg)}</span>`)
      .join("\n");
  }
}

function esc(s) {
  const d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}

async function poll() {
  try {
    const { status, data } = await api("GET", "/state");
    if (status === 200) render(data);
    else throw new Error("HTTP " + status);
  } catch (e) {
    $("conn").textContent = "— offline: " + e.message;
    $("conn").className = "conn err";
  }
}

async function doAction(act) {
  try {
    if (act === "registerA" || act === "registerB") {
      const world = act === "registerA" ? "A" : "B";
      const name = $(world === "A" ? "nameA" : "nameB").value.trim();
      if (!name) return flash("Name eingeben!");
      const { status, data } = await api("POST", "/lobby", { player: name, world });
      if (status === 200) {
        me = { world, player: name };
        localStorage.setItem("rb-me", JSON.stringify(me));
        flash(`Spieler ${name} (Welt ${world}) registriert`);
      } else {
        flash(data.error || "Registrierung fehlgeschlagen", true);
      }
    } else if (act === "readyA" || act === "readyB") {
      const world = act === "readyA" ? "A" : "B";
      const { status, data } = await api("POST", "/ready", { world });
      if (status === 200) {
        flash(data.match_started ? "GO! Match gestartet (Runde 1)" : `Welt ${world} ready`);
      } else {
        flash(data.error || "Ready fehlgeschlagen", true);
      }
    } else if (act === "sp") {
      const name = $("nameSp").value.trim();
      if (!name) return flash("Name eingeben!");
      const { status, data } = await api("POST", "/sp", { player: name });
      if (status === 200) {
        me = { world: "A", player: name };
        localStorage.setItem("rb-me", JSON.stringify(me));
        flash(`SP-Mode gestartet — ${name} (P1) vs MIRROR`);
      } else {
        flash(data.error || "SP-Start fehlgeschlagen", true);
      }
    } else if (act === "go") {
      const { status, data } = await api("POST", "/go", {});
      if (status === 200) flash("GO-Broadcast gesendet");
      else flash(data.error || "GO fehlgeschlagen", true);
    } else if (act === "rematch") {
      const { status, data } = await api("POST", "/rematch", {});
      if (status === 200) flash("Rematch — zurück in die Lobby");
      else flash(data.error || "Rematch fehlgeschlagen", true);
    }
  } catch (e) {
    flash("Netzwerkfehler: " + e.message, true);
  }
}

function flash(msg, isErr) {
  const bar = $("goBar");
  bar.classList.remove("hidden");
  $("goMsg").textContent = (isErr ? "✗ " : "✓ ") + msg;
  $("goMsg").style.color = isErr ? "var(--bad)" : "var(--good)";
  setTimeout(() => { $("goMsg").style.color = ""; }, 4000);
}

function bindActions() {
  document.addEventListener("click", (ev) => {
    const btn = ev.target.closest("button[data-act]");
    if (btn && !btn.disabled) doAction(btn.dataset.act);
  });
}

function init() {
  try {
    const saved = localStorage.getItem("rb-me");
    if (saved) me = JSON.parse(saved);
  } catch (_) {}
  bindActions();
  poll();
  setInterval(poll, POLL_MS);
}

init();
