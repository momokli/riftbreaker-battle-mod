/*!
 * RIFT BATTLE — Kalzium-Dashboard-Widget (Issue #383, Baby-Step zu #363/#377)
 * ============================================================
 * Zeigt NUR den aktuellen Kalzium-Stand (= "carbonium" im Spiel, #40) als
 * Zahl. Quelle: POST /get_state auf der pipe_bridge (Trainer-only-Kanal,
 * #363/#365) — bewusst KEIN Log-Tailing (siehe #363: "Log bleibt nur
 * Telemetry"). Der Bridge-Wert ist ein int64-Fixed-Point ×10^6
 * (b93061f/57208db auf feature/363-dedicated-io-interface), Anzeige-Wert
 * ist also `carbonium / 1_000_000`.
 *
 * Zweigeteilt wie live-status.js (#30):
 *   - deriveCalcium(response)  → pure Funktion (ohne DOM/Netz), unit-testbar
 *   - createWidget(opts)       → Poll-Loop (fetch + render), ohne Reload
 *
 * Konfiguration (keine Hardcodes im Markup):
 *   - API-Basis-URL: <meta name="rb-bridge-api" content="…">,
 *     sonst Default "/bridge" (relativ → gleicher Origin, Caddy-Proxy).
 *   - Poll-Intervall: opts.pollMs (Default 3000 ms).
 *
 * UMD: laedt als CommonJS-Modul (Node/Tests) oder als global
 * `RBCalciumWidget` (Browser).
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory();
  } else {
    root.RBCalciumWidget = factory();
  }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  var DEFAULT_API_BASE = "/bridge";
  var DEFAULT_POLL_MS = 3000;
  var FIXED_POINT_SCALE = 1000000; // #365: int64-Fixed-Point ×10^6

  /* ---------------- pure Derivation ---------------- */

  function unavailable(reason) {
    return {
      ok: false,
      value: null,
      line: "Kalzium: —",
      sub: reason || "Bridge nicht erreichbar",
    };
  }

  /**
   * POST /get_state → Kalzium-Anzeige. Defensiv: fehlendes/kaputtes
   * carbonium-Feld oder ok:false fuehren zu "—" statt zu einem Fehler
   * (die Bridge antwortet ok:false z.B. bei "pipe_unavailable"/"no_account",
   * solange kein Spieler online/verbunden ist — kein Fake-Wert).
   */
  function deriveCalcium(response) {
    if (!response || typeof response !== "object") return unavailable();
    if (response.ok !== true) return unavailable(response.reason || null);

    var raw = Number(response.carbonium);
    if (!isFinite(raw)) return unavailable("carbonium fehlt/ungueltig");

    var value = Math.round(raw / FIXED_POINT_SCALE);
    return {
      ok: true,
      value: value,
      line: "Kalzium: " + value,
      sub: null,
    };
  }

  /* ---------------- API-Basis-Konfiguration ---------------- */

  function resolveApiBase(doc) {
    var d = doc || (typeof document !== "undefined" ? document : null);
    if (d) {
      var m = d.querySelector('meta[name="rb-bridge-api"]');
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
        render(unavailable("kein fetch verfuegbar"));
        return Promise.resolve();
      }
      return fetchImpl(base + "/get_state", { method: "POST", cache: "no-store" })
        .then(function (r) {
          if (!r.ok) throw new Error("HTTP " + r.status);
          return r.json();
        })
        .then(function (data) {
          render(deriveCalcium(data));
        })
        .catch(function () {
          render(unavailable()); // defensiv: "—" statt Fehlerseite
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
    deriveCalcium: deriveCalcium,
    resolveApiBase: resolveApiBase,
    createWidget: createWidget,
    DEFAULT_API_BASE: DEFAULT_API_BASE,
    DEFAULT_POLL_MS: DEFAULT_POLL_MS,
    FIXED_POINT_SCALE: FIXED_POINT_SCALE,
  };
});
