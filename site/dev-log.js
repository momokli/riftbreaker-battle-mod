/*!
 * RIFT BATTLE — DEV-Log Widget (Issue #98)
 * ============================================================
 * Live-Event-Konsole für solo.html: pollt den Tournament-API-Event-Feed
 * (GET /events?since=<seq>, siehe docs/TOURNAMENT_API.md, Cursor ohne
 * Event-Verlust) und liefert JEDES Event weiter, kontinuierlich ohne
 * Reload — auch unbekannte/künftige kind-Werte (generischer Fallback,
 * kein Event wird verschluckt).
 *
 * Bekannte kind-Werte (tournament/src/state.rs .log(...)-Aufrufe):
 *   register, ready, go, send, wave, reveal, hq, finish, match_end,
 *   score, rematch. Jeder andere Wert bekommt die neutrale dl-sys-Farbe,
 *   der Text bleibt trotzdem "[<kind>] <msg>" — nichts wird gefiltert.
 *
 * Transparenz-Hinweis: Die Tournament-API ist v1 bewusst ohne Persistenz
 * (in-memory, siehe docs/TOURNAMENT_API.md) — "was gespeichert wird" ist
 * hier kein eigener Event-Typ; die Spielstand-Persistenz läuft im Lua-Mod
 * (rb_points, Issue #65) und ist noch nicht Teil dieses Feeds.
 *
 * Aufbau wie live-status.js:
 *   - describeEvent(event)   -> pure Funktion (ohne DOM/Netz), unit-testbar
 *   - createLogWidget(opts)  -> Cursor-Poll-Loop (fetch + render)
 *
 * UMD: lädt als CommonJS-Modul (Node/Tests) oder als global `RBDevLog`
 * (Browser).
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory();
  } else {
    root.RBDevLog = factory();
  }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  var DEFAULT_API_BASE = "/tournament";
  var DEFAULT_POLL_MS = 3000;

  // kind -> CSS-Klasse (nur Einfärbung; der [<kind>]-Text kommt immer aus
  // dem Event selbst, damit unbekannte kinds nicht verschwinden).
  var KNOWN_KINDS = {
    register: "dl-join",
    ready: "dl-sys",
    go: "dl-sys",
    send: "dl-send",
    wave: "dl-wave",
    reveal: "dl-wave",
    hq: "dl-hq",
    finish: "dl-hl",
    match_end: "dl-hl",
    score: "dl-sys",
    rematch: "dl-sys",
  };

  /**
   * Ein rohes Feed-Event ({seq,t,kind,msg}, s. GET /events) -> Render-Spec.
   * Fällt NIE auf nichts zurück — unbekannte/fehlende Felder werden defensiv
   * generisch dargestellt statt das Event zu verschlucken.
   */
  function describeEvent(ev) {
    if (!ev || typeof ev !== "object") {
      return { seq: null, kind: "unknown", cls: "dl-sys", t: null, text: "[?] (ungültiges Event)" };
    }
    var kind = typeof ev.kind === "string" && ev.kind ? ev.kind : "unknown";
    var cls = KNOWN_KINDS[kind] || "dl-sys";
    var msg;
    if (typeof ev.msg === "string") {
      msg = ev.msg;
    } else if (ev.msg === undefined || ev.msg === null) {
      msg = "";
    } else {
      try {
        msg = JSON.stringify(ev.msg);
      } catch (e) {
        msg = String(ev.msg);
      }
    }
    return {
      seq: typeof ev.seq === "number" ? ev.seq : null,
      kind: kind,
      cls: cls,
      t: typeof ev.t === "number" ? ev.t : null,
      text: "[" + kind + "] " + msg,
    };
  }

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

  /**
   * Cursor-Poll-Loop gegen GET /events?since=<seq>. Jeder Refresh liefert
   * nur neue Events (seq > since) — kein Reload, keine Duplikate, kein
   * Event-Verlust (Server-Cursor-Vertrag, docs/TOURNAMENT_API.md).
   */
  function createLogWidget(opts) {
    opts = opts || {};
    var base = String(opts.apiBase !== undefined ? opts.apiBase : DEFAULT_API_BASE).replace(/\/+$/, "");
    var pollMs = opts.pollMs || DEFAULT_POLL_MS;
    var fetchImpl = opts.fetchImpl !== undefined
      ? opts.fetchImpl
      : (typeof fetch !== "undefined" ? fetch : null);
    var onEvents = opts.onEvents || function () {};
    var onError = opts.onError || function () {};
    var doc = opts.document || (typeof document !== "undefined" ? document : null);

    var since = typeof opts.since === "number" ? opts.since : 0;
    var timer = null;
    var initial = true;

    function refresh() {
      if (!fetchImpl) {
        onError(new Error("kein fetch verfügbar"));
        return Promise.resolve();
      }
      return fetchImpl(base + "/events?since=" + since, { cache: "no-store" })
        .then(function (r) {
          if (!r.ok) throw new Error("HTTP " + r.status);
          return r.json();
        })
        .then(function (data) {
          var raw = data && Array.isArray(data.events) ? data.events : [];
          if (raw.length > 0) {
            onEvents(raw.map(describeEvent), initial);
          }
          if (data && typeof data.last_seq === "number") {
            since = data.last_seq;
          }
          initial = false;
        })
        .catch(function (err) {
          onError(err);
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

    return {
      refresh: refresh,
      start: start,
      stop: stop,
      base: base,
      getSince: function () { return since; },
    };
  }

  return {
    describeEvent: describeEvent,
    resolveApiBase: resolveApiBase,
    createLogWidget: createLogWidget,
    KNOWN_KINDS: KNOWN_KINDS,
    DEFAULT_API_BASE: DEFAULT_API_BASE,
    DEFAULT_POLL_MS: DEFAULT_POLL_MS,
  };
});
