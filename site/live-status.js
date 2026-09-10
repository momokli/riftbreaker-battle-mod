/*!
 * RIFT BATTLE — Landing Live-Status-Widget (Issue #30)
 * ============================================================
 * Leitet aus einem Tournament-API-Zustand (GET /state, siehe
 * docs/TOURNAMENT_API.md) eine kompakte Status-Zeile für die Landing ab:
 *
 *   "Lobby leer" · "N Spieler in Lobby" · "Match läuft: A vs B (Runde X)"
 *   · "Solo-Match läuft" · "Status unbekannt" (API nicht erreichbar)
 *
 * Der Code ist bewusst zweigeteilt:
 *   - deriveStatus(state)  → pure Funktion (ohne DOM/Netz), unit-testbar
 *   - createWidget(opts)   → Poll-Loop (fetch + render), ohne Reload
 *
 * Konfiguration (keine Hardcodes im Markup):
 *   - API-Basis-URL: <meta name="rb-tournament-api" content="…">,
 *     sonst Default "/tournament" (relativ → gleicher Origin).
 *   - Poll-Intervall: opts.pollMs (Default 5000 ms).
 *
 * UMD: lädt als CommonJS-Modul (Node/Tests) oder als global
 * `RBTournamentStatus` (Browser).
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory();
  } else {
    root.RBTournamentStatus = factory();
  }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  var DEFAULT_API_BASE = "/tournament";
  var DEFAULT_POLL_MS = 5000;

  /* ---------------- pure Derivation ---------------- */

  function nameOrWorld(teams, world) {
    var t = teams && teams[world];
    return t && t.player ? t.player : "Welt " + world;
  }

  function playersIn(teams) {
    var out = [];
    ["A", "B"].forEach(function (w) {
      var t = teams && teams[w];
      if (t && t.player) out.push({ world: w, name: t.player, ready: !!t.ready });
    });
    return out;
  }

  function make(kind, line, sub, phase, mode, round, players, winner) {
    return {
      ok: true,
      kind: kind,
      line: line,
      sub: sub,
      phase: phase,
      mode: mode,
      round: round,
      players: players,
      count: players.length,
      winner: winner,
    };
  }

  function unknown(sub) {
    return {
      ok: false,
      kind: "unknown",
      line: "Status unbekannt",
      sub: sub || "Tournament-API nicht erreichbar",
      phase: null,
      mode: null,
      round: 0,
      players: [],
      count: 0,
      winner: null,
    };
  }

  /**
   * GET /state → Status-Zeile. Defensiv: null/fehlende phase/fehlende teams
   * führen zu "Status unbekannt" statt zu einem Fehler.
   */
  function deriveStatus(state) {
    if (!state || typeof state !== "object" || typeof state.phase !== "string") {
      return unknown();
    }
    var phase = state.phase;
    var mode = state.mode || "duel";
    var teams = state.teams && typeof state.teams === "object" ? state.teams : {};
    var round = state.round || 0;
    var players = playersIn(teams);
    var count = players.length;
    var winner = state.winner || null;

    if (phase === "lobby" || phase === "ready") {
      if (count === 0) {
        return make("empty", "Lobby leer", "warten auf Spieler", phase, mode, round, players, winner);
      }
      var readyCount = 0;
      var names = players
        .map(function (p) {
          if (p.ready) readyCount++;
          return p.name;
        })
        .join(" · ");
      var sub = names + (readyCount > 0 ? " — " + readyCount + "/" + count + " ready" : "");
      var n = count === 1 ? "1 Spieler" : count + " Spieler";
      return make("lobby", n + " in Lobby", sub, phase, mode, round, players, winner);
    }

    if (phase === "running") {
      if (mode === "sp") {
        var spName = nameOrWorld(teams, "A");
        return make("sp", "Solo-Match läuft", spName + " · Runde " + round, phase, mode, round, players, winner);
      }
      var a = nameOrWorld(teams, "A");
      var b = nameOrWorld(teams, "B");
      return make(
        "running",
        "Match läuft: " + a + " vs " + b + " (Runde " + round + ")",
        "duel · runde " + round,
        phase, mode, round, players, winner
      );
    }

    if (phase === "finished") {
      var wname = winner ? nameOrWorld(teams, winner) : "—";
      if (mode === "sp") {
        return make("finished", "Solo-Match beendet", "Sieger: " + wname, phase, mode, round, players, winner);
      }
      return make("finished", "Match beendet — Sieger: " + wname, "rematch möglich", phase, mode, round, players, winner);
    }

    return unknown("Phase: " + phase);
  }

  /* ---------------- API-Basis-Konfiguration ---------------- */

  function resolveApiBase(doc) {
    var d = doc || (typeof document !== "undefined" ? document : null);
    if (d) {
      var m = d.querySelector('meta[name="rb-tournament-api"]');
      if (m && m.content && m.content.trim()) {
        return m.content.trim().replace(/\/+$/, "");
      }
    }
    return DEFAULT_API_BASE;
  }

  /* ---------------- Widget (Poll-Loop) ---------------- */

  function createWidget(opts) {
    opts = opts || {};
    var base = String(opts.apiBase !== undefined ? opts.apiBase : DEFAULT_API_BASE).replace(/\/+$/, "");
    var pollMs = opts.pollMs || DEFAULT_POLL_MS;
    var fetchImpl = opts.fetchImpl !== undefined
      ? opts.fetchImpl
      : (typeof fetch !== "undefined" ? fetch : null);
    var render = opts.render || function () {};
    var doc = opts.document || (typeof document !== "undefined" ? document : null);

    var timer = null;

    function refresh() {
      if (!fetchImpl) {
        render(deriveStatus(null));
        return Promise.resolve();
      }
      return fetchImpl(base + "/state", { cache: "no-store" })
        .then(function (r) {
          if (!r.ok) throw new Error("HTTP " + r.status);
          return r.json();
        })
        .then(function (data) {
          render(deriveStatus(data));
        })
        .catch(function () {
          render(deriveStatus(null)); // defensiv: "Status unbekannt"
        });
    }

    function start() {
      if (timer) return;
      refresh();
      timer = setInterval(function () {
        if (doc && doc.hidden) return; // kein Poll im Hintergrund-Tab
        refresh();
      }, pollMs);
    }

    function stop() {
      if (timer) {
        clearInterval(timer);
        timer = null;
      }
    }

    return { refresh: refresh, start: start, stop: stop, base: base };
  }

  return {
    deriveStatus: deriveStatus,
    resolveApiBase: resolveApiBase,
    createWidget: createWidget,
    DEFAULT_API_BASE: DEFAULT_API_BASE,
    DEFAULT_POLL_MS: DEFAULT_POLL_MS,
  };
});
