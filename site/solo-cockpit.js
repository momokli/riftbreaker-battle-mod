/*!
 * RIFT BATTLE — Solo „cockpit console" Match-Page (Issue #173, ref #159)
 * =====================================================================
 * Macht aus dem Tournament-API-Zustand (GET /state, docs/TOURNAMENT_API.md)
 * die Cockpit-Anzeige der /solo-Match-Page gemäß Stitch-Design
 * (site/designs/stitch-solo-cockpit-console.zip):
 *   - State-Strip: Phase (SETUP/WAVES/GAME OVER), Wave, HQ-Integrität, P1/P2
 *   - Announce-Banner: COMMENCE (running) / GAME OVER (finished) / STANDBY
 *   - God-Commands-Panel: Wave-Start/Pause, Ressourcen, HQ zerstören, Restart
 *   - optionales Operator-Gate
 *
 * Zweigeteilt wie live-status.js / dev-log.js:
 *   - deriveCockpit(state, opts)  -> pure (ohne DOM/Netz), unit-testbar
 *   - createCockpit(opts)         -> Poll-Loop GET /state -> render(view)
 *   - createGodPanel(opts)        -> Command-Transport (nur erreichbare Endpoints)
 *   - createAccessGate(opts)      -> optionales Soft-Gate (kein Secret im Repo)
 *
 * Kein `/command`-Backend im Tournament-Server v1 (#159): der Transport
 * nutzt ausschließlich EXISTIERENDE Referee-Endpoints (`POST /report`,
 * `POST /rematch`) für Kommandos, die damit nachweislich machbar sind.
 * Kommandos ohne erreichbaren Transport bleiben GEPARKT (reachable:false,
 * kein POST, `todo`-Begründung) statt gegen einen nicht existierenden
 * Endpoint zu laufen.
 *
 * UMD: CommonJS (Node/Tests) oder global `RBSoloCockpit` (Browser).
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory();
  } else {
    root.RBSoloCockpit = factory();
  }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  var DEFAULT_API_BASE = "/tournament";
  var DEFAULT_POLL_MS = 3000;
  var GATE_STORAGE_KEY = "rb-operator-unlocked";

  /* kind -> Phasenlabel des State-Strips */
  var PHASE_LABELS = {
    lobby: "SETUP",
    ready: "SETUP",
    running: "WAVES",
    finished: "GAME OVER",
  };

  /* God-Commands (Issue #159).
   *
   * v1 verdrahtet NUR Kommandos, die im aktuellen Deployment nachweislich
   * erreichbar sind — d. h. über einen EXISTIERENDEN Referee-Endpoint
   * (docs/TOURNAMENT_API.md). Ein Kommando ohne erreichbaren Transport
   * bleibt sichtbar, wird aber GEPARKT (`reachable:false`) und trägt die
   * Follow-up-Begründung in `todo`.
   *
   * Hintergrund: der Tournament-Server v1 hat KEINEN `/command`-Endpoint
   * und keinen In-Game-Kommandokanal (RCON/Relay ist nicht deployt, #159).
   * Deshalb sind Wellen-Steuerung und Ressourcen-Gabe geparkt. */
  var COMMANDS = {
    destroy_hq: {
      cmd: "destroy_hq",
      destructive: true,
      reachable: true,
      label: "HQ zerstören (Test)",
      // POST /report {event:"hq_hp", hp:0} — der dokumentierte Weg des
      // Referees, ein HQ auf 0 zu setzen (→ Match-Ende, Sieger = Gegner).
      request: function () {
        return { path: "/report", method: "POST", body: { world: "A", event: "hq_hp", hp: 0 } };
      },
    },
    restart: {
      cmd: "restart",
      destructive: true,
      reachable: true,
      label: "Spiel neu starten",
      // POST /rematch — Reset in die Lobby (nur außerhalb `running`, sonst
      // 409). Spieler bleiben registriert.
      request: function () {
        return { path: "/rematch", method: "POST", body: {} };
      },
    },
    // --- GEPARKT: kein erreichbarer Transport im aktuellen Deployment -----
    wave_toggle: {
      cmd: "wave_toggle",
      destructive: false,
      reachable: false,
      label: "Welle Start/Pause",
      todo: "Follow-up #159: kein Referee-Endpoint für Wellenstart/-pause; braucht rb_* über RCON/Relay (nicht deployt).",
    },
    give_resources: {
      cmd: "give_resources",
      destructive: false,
      reachable: false,
      label: "Ressourcen injizieren",
      todo: "Follow-up #159: kein Ressourcen-Endpoint; braucht rb_give_* über RCON/Relay (nicht deployt).",
    },
  };

  function clampPct(n) {
    if (typeof n !== "number" || !isFinite(n)) return 0;
    if (n < 0) return 0;
    if (n > 100) return 100;
    return Math.round(n);
  }

  function phaseLabel(phase) {
    return PHASE_LABELS[phase] || "SETUP";
  }

  function numOrNull(v) {
    return typeof v === "number" && isFinite(v) ? v : null;
  }

  function teamView(teams, world, total) {
    var t = (teams && teams[world]) || {};
    var hp = numOrNull(t.hq_hp);
    return {
      world: world,
      player: typeof t.player === "string" && t.player ? t.player : null,
      hp: hp,
      hpPct: hp === null ? null : clampPct((hp / total) * 100),
      score: numOrNull(t.score),
      wave: numOrNull(t.wave),
      ready: !!t.ready,
    };
  }

  /**
   * Roher /state -> View-Spec für den Cockpit-Strip (pure, ohne DOM).
   * Defensiv: fehlende/ungültige Felder werden neutral dargestellt.
   */
  function deriveCockpit(state, opts) {
    opts = opts || {};
    state = state && typeof state === "object" ? state : {};
    var teams = state.teams && typeof state.teams === "object" ? state.teams : {};

    var start = numOrNull(state.hq_hp_start);
    var total = start !== null && start > 0 ? start : (opts.hqMax || 100);

    var phase = typeof state.phase === "string" ? state.phase : "unknown";
    var winner = state.winner === "A" || state.winner === "B" ? state.winner : null;

    var a = teamView(teams, "A", total);
    var b = teamView(teams, "B", total);

    /* HQ-Integrität: in SP/duel gibt es nur ein reales HQ (Welt A); die
       MIRROR-Seite wird im Server gespiegelt. Fallback: max beider Seiten. */
    var hqPct = a.hpPct;
    if (hqPct === null) hqPct = b.hpPct;
    if (hqPct === null) hqPct = 100;

    var round = numOrNull(state.round);
    var wave = numOrNull(state.wave);
    if (wave === null) wave = a.wave;
    if (wave === null) wave = b.wave;

    var announce;
    if (phase === "running") {
      var left = a.player || "P1";
      var right = b.player || "P2";
      announce = { kind: "commence", text: "COMMENCE — " + left + " vs " + right };
    } else if (phase === "finished") {
      var winName = winner === "A" ? (a.player || "A") : winner === "B" ? (b.player || "B") : null;
      announce = { kind: "gameover", text: "GAME OVER" + (winName ? " — SIEGER: " + winName : "") };
    } else {
      announce = { kind: "idle", text: "STANDBY — WARTE AUF START" };
    }

    return {
      phase: phase,
      phaseLabel: phaseLabel(phase),
      mode: typeof state.mode === "string" ? state.mode : null,
      round: round,
      wave: wave,
      waveText: wave === null ? "WELLE --" : "WELLE " + String(wave).padStart(2, "0"),
      hq: { pct: hqPct, total: total, hp: a.hp, critical: hqPct < 20 },
      players: { p1: a, p2: b },
      announce: announce,
    };
  }

  function noFetch(onError) {
    var err = new Error("fetch nicht verfügbar");
    onError(err);
    return Promise.resolve(null);
  }

  /* ---------------- Widget: /state-Poll -> render(view) ---------------- */

  function createCockpit(opts) {
    opts = opts || {};
    var base = String(opts.apiBase !== undefined ? opts.apiBase : DEFAULT_API_BASE).replace(/\/+$/, "");
    var pollMs = opts.pollMs || DEFAULT_POLL_MS;
    var fetchImpl = opts.fetchImpl !== undefined
      ? opts.fetchImpl
      : (typeof fetch !== "undefined" ? fetch : null);
    var render = typeof opts.render === "function" ? opts.render : function () {};
    var onError = typeof opts.onError === "function" ? opts.onError : function () {};
    var doc = opts.document || (typeof document !== "undefined" ? document : null);

    var timer = null;

    function refresh() {
      if (!fetchImpl) return noFetch(onError);
      return fetchImpl(base + "/state", { cache: "no-store" })
        .then(function (r) {
          if (!r.ok) throw new Error("HTTP " + r.status);
          return r.json();
        })
        .then(function (data) {
          var view = deriveCockpit(data, opts);
          render(view, data);
          return view;
        })
        .catch(function (err) {
          onError(err);
          return null;
        });
    }

    function start() {
      if (timer) return;
      refresh();
      timer = setInterval(function () {
        if (doc && doc.hidden) return;
        refresh();
      }, pollMs);
    }

    function stop() {
      if (timer) { clearInterval(timer); timer = null; }
    }

    return { refresh: refresh, start: start, stop: stop, base: base };
  }

  /* ---------------- God-Commands-Transport ---------------- */

  function createGodPanel(opts) {
    opts = opts || {};
    var base = String(opts.apiBase !== undefined ? opts.apiBase : DEFAULT_API_BASE).replace(/\/+$/, "");
    var fetchImpl = opts.fetchImpl !== undefined
      ? opts.fetchImpl
      : (typeof fetch !== "undefined" ? fetch : null);
    var confirmImpl = opts.confirm !== undefined
      ? opts.confirm
      : function (msg) { return typeof window !== "undefined" && window.confirm ? window.confirm(msg) : false; };
    var onResult = typeof opts.onResult === "function" ? opts.onResult : function () {};

    function send(action) {
      var spec = COMMANDS[action];
      if (!spec) {
        var bad = { ok: false, action: action, error: "unbekanntes Kommando" };
        onResult(bad);
        return Promise.resolve(bad);
      }
      if (!spec.reachable) {
        // Geparkt: kein erreichbarer Transport — kein POST, klar begründet.
        var parked = { ok: false, action: action, parked: true, error: spec.todo || "nicht verdrahtet (Follow-up)" };
        onResult(parked);
        return Promise.resolve(parked);
      }
      if (spec.destructive && !confirmImpl("SICHERHEITSABFRAGE: '" + spec.label + "' ist destruktiv. Ausführen?")) {
        var abort = { ok: false, action: action, aborted: true };
        onResult(abort);
        return Promise.resolve(abort);
      }
      if (!fetchImpl) {
        var nf = { ok: false, action: action, error: "fetch nicht verfügbar" };
        onResult(nf);
        return Promise.resolve(nf);
      }
      var req = spec.request();
      return fetchImpl(base + req.path, {
        method: req.method || "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(req.body || {}),
      })
        .then(function (r) {
          return r.json().catch(function () { return null; }).then(function (data) {
            if (!r.ok) {
              var err = { ok: false, action: action, status: r.status, error: (data && data.error) || ("HTTP " + r.status) };
              onResult(err);
              return err;
            }
            var ok = { ok: true, action: action, data: data };
            onResult(ok);
            return ok;
          });
        })
        .catch(function (e) {
          var err2 = { ok: false, action: action, error: String((e && e.message) || e) };
          onResult(err2);
          return err2;
        });
    }

    return { send: send, base: base, commands: COMMANDS, isReachable: isReachable };
  }

  /** true, wenn `action` einen erreichbaren Transport hat (nicht geparkt). */
  function isReachable(action) {
    return !!(COMMANDS[action] && COMMANDS[action].reachable);
  }

  /* ---------------- Optionales Operator-Gate ----------------
   * Reines UI-Soft-Gate (kein Secret im Repo): wenn per Meta
   * `rb-operator-gate` = "on" aktiviert, entsperrt der Operator die
   * God-Commands für die Session. Die echte Absicherung erfolgt serverseitig
   * (Caddy basic_auth / App-Auth, siehe #159). */

  function createAccessGate(opts) {
    opts = opts || {};
    var enabled = !!opts.enabled;
    var storage = opts.storage !== undefined
      ? opts.storage
      : (typeof sessionStorage !== "undefined" ? sessionStorage : null);

    function isUnlocked() {
      if (!enabled) return true;
      if (!storage) return false;
      try { return storage.getItem(GATE_STORAGE_KEY) === "1"; } catch (e) { return false; }
    }

    function unlock(code) {
      var ok = String(code === undefined || code === null ? "" : code).trim() !== "";
      if (ok && storage) {
        try { storage.setItem(GATE_STORAGE_KEY, "1"); } catch (e) { /* ignore */ }
      }
      return ok;
    }

    function lock() {
      if (storage) { try { storage.removeItem(GATE_STORAGE_KEY); } catch (e) { /* ignore */ } }
    }

    return { enabled: enabled, isUnlocked: isUnlocked, unlock: unlock, lock: lock, key: GATE_STORAGE_KEY };
  }

  return {
    deriveCockpit: deriveCockpit,
    createCockpit: createCockpit,
    createGodPanel: createGodPanel,
    createAccessGate: createAccessGate,
    isReachable: isReachable,
    phaseLabel: phaseLabel,
    clampPct: clampPct,
    COMMANDS: COMMANDS,
    PHASE_LABELS: PHASE_LABELS,
    DEFAULT_API_BASE: DEFAULT_API_BASE,
    DEFAULT_POLL_MS: DEFAULT_POLL_MS,
  };
});
