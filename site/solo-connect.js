/*!
 * RIFT BATTLE — Solo-Connect-Widget (Issue #97)
 * ============================================================
 * Server-Adresse eingeben → Verbinden (GET <base>/state) → Spiel
 * starten (POST <base>/sp). Der Spielstart nutzt den bestehenden
 * Tournament-/Referee-Flow: das SP-Match (P1 vs MIRROR, Issue #44)
 * wird serverseitig gestartet; die Bridge erkennt den Start per
 * Poll auf /state und dispatcht die GO-Kommandos über den Relay an
 * die rbbridge-Pipe (exec_result-Feedback, #73 / PR #79).
 *
 * Zweigeteilt wie live-status.js:
 *   - normalizeAddress(raw, fallback) → pure, unit-testbar
 *   - createSoloConnect(opts)         → Verbinden + Spielstart (fetch)
 *
 * Konfiguration (keine Hardcodes im Markup):
 *   - Server-Adresse: Eingabefeld; leer = Fallback (apiBase).
 *   - apiBase: Default "/tournament" (relativ → gleicher Origin),
 *     analog zum <meta name="rb-tournament-api"> der Live-Status.
 *
 * UMD: lädt als CommonJS-Modul (Node/Tests) oder als global
 * `RBSoloConnect` (Browser).
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory();
  } else {
    root.RBSoloConnect = factory();
  }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  var DEFAULT_API_BASE = "/tournament";

  /* ---------------- pure: Server-Adresse normalisieren ---------------- */

  /**
   * Normalisiert eine Server-Adresse: trimmt, entfernt einen (oder mehrere)
   * abschließende Slashes. Leer/whitespace → Fallback (ebenfalls normalisiert).
   * Liefert einen String ohne abschließenden Slash.
   */
  function normalizeAddress(raw, fallback) {
    var fb = String(fallback === undefined || fallback === null ? DEFAULT_API_BASE : fallback);
    var s = String(raw === undefined || raw === null ? "" : raw).trim();
    if (s === "") {
      s = fb;
    }
    return s.replace(/\/+$/, "");
  }

  /* ---------------- Widget (Verbinden + Spielstart) ---------------- */

  function createSoloConnect(opts) {
    opts = opts || {};
    var fallback = String(opts.apiBase !== undefined ? opts.apiBase : DEFAULT_API_BASE);
    var fetchImpl = opts.fetchImpl !== undefined
      ? opts.fetchImpl
      : (typeof fetch !== "undefined" ? fetch : null);
    var getAddress = typeof opts.getAddress === "function"
      ? opts.getAddress
      : function () { return ""; };
    var onState = typeof opts.onState === "function" ? opts.onState : function () {};

    var connected = false;
    var base = normalizeAddress("", fallback);

    function currentBase() {
      return normalizeAddress(getAddress(), fallback);
    }

    /**
     * Verbindet mit dem Referee: GET <base>/state. Erfolg = HTTP 200 und eine
     * gültige /state-Antwort (JSON mit string `phase`). Liefert einen Status-
     * objekt-Block { ok, connected, base, phase?, mode?, round? } bzw.
     * { ok:false, connected:false, base, error }.
     */
    function connect() {
      if (!fetchImpl) {
        connected = false;
        var noFetch = { ok: false, connected: false, base: base, error: "fetch nicht verfügbar" };
        onState(noFetch);
        return Promise.resolve(noFetch);
      }
      base = currentBase();
      return fetchImpl(base + "/state", { cache: "no-store" })
        .then(function (r) {
          if (!r.ok) throw new Error("HTTP " + r.status);
          return r.json();
        })
        .then(function (data) {
          var valid = data && typeof data === "object" && typeof data.phase === "string";
          if (!valid) throw new Error("ungültige /state-Antwort");
          connected = true;
          var st = {
            ok: true,
            connected: true,
            base: base,
            phase: data.phase,
            mode: data.mode,
            round: data.round,
          };
          onState(st);
          return st;
        })
        .catch(function (e) {
          connected = false;
          var st = {
            ok: false,
            connected: false,
            base: base,
            error: String((e && e.message) || e),
          };
          onState(st);
          return st;
        });
    }

    /**
     * Startet das Solo-Match über den bestehenden Flow: POST <base>/sp mit
     * { player: name }. Nur nach erfolgreichem connect().
     */
    function start(name) {
      var n = String(name === undefined || name === null ? "" : name).trim();
      if (!connected) {
        var errNotConnected = { ok: false, error: "nicht verbunden — erst verbinden" };
        onState(errNotConnected);
        return Promise.resolve(errNotConnected);
      }
      if (n === "") {
        var errNoName = { ok: false, error: "Name eingeben" };
        onState(errNoName);
        return Promise.resolve(errNoName);
      }
      if (!fetchImpl) {
        var errNoFetch = { ok: false, error: "fetch nicht verfügbar" };
        onState(errNoFetch);
        return Promise.resolve(errNoFetch);
      }
      return fetchImpl(base + "/sp", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ player: n }),
      })
        .then(function (r) {
          return r.json().catch(function () { return null; }).then(function (data) {
            if (!r.ok) {
              var e = { ok: false, error: (data && data.error) || ("HTTP " + r.status) };
              onState(e);
              return e;
            }
            var st = {
              ok: true,
              started: !!(data && data.started),
              phase: data && data.phase,
              round: data && data.round,
              mode: data && data.mode,
            };
            onState(st);
            return st;
          });
        })
        .catch(function (e) {
          var st = { ok: false, error: String((e && e.message) || e) };
          onState(st);
          return st;
        });
    }

    return {
      connect: connect,
      start: start,
      isConnected: function () { return connected; },
      base: function () { return base; },
    };
  }

  return {
    normalizeAddress: normalizeAddress,
    createSoloConnect: createSoloConnect,
    DEFAULT_API_BASE: DEFAULT_API_BASE,
  };
});
