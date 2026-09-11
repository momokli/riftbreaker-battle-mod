/*!
 * RIFT BATTLE — Ranglisten-Widget (Issue #132)
 * ============================================================
 * Zeigt die ELO-Rangliste (#128) als Tabelle: Spieler sortiert nach
 * Rating, inkl. Bilanz (wins/losses/draws). Aufbau wie live-status.js /
 * dev-log.js:
 *
 *   - deriveLeaderboard(payload)      → pure Funktion (ohne DOM/Netz),
 *                                       unit-testbar; sortiert + normalisiert.
 *   - createLeaderboardWidget(opts)   → Poll-Loop (fetch + render), ohne Reload.
 *
 * Datenquelle (Annahme, siehe PR #132): die geplante Profil-API aus #129
 * liefert unter `GET <base>/leaderboard` die Profile. `elo` kommt aus #131
 * (Start 1200), die Bilanz aus dem Profil-Datenmodell
 * (docs/PLAYER_PROFILE_MODEL.md: wins/losses/draws). Solange der Endpoint
 * auf `main` noch nicht existiert (kein Persistenz-Layer, API v1 ist
 * in-memory), degradiert das Widget defensiv zu "Rangliste nicht
 * verfügbar" statt zu brechen — genau wie das Live-Status-Widget bei
 * nicht erreichbarer API.
 *
 * Akzeptierte Antwortformen (defensiv, vorwärtskompatibel):
 *   [ {...}, ... ] · {"players": [...]} · {"leaderboard": [...]} ·
 *   {"entries": [...]} · {"ranking": [...]}
 * Profil-Felder je Eintrag (Aliase tolerant):
 *   display_name|name|player|player_id · elo · wins · losses · draws
 *
 * Konfiguration (keine Hardcodes im Markup):
 *   - API-Basis-URL: <meta name="rb-tournament-api" content="…">,
 *     sonst Default "/tournament" (relativ → gleicher Origin).
 *   - Poll-Intervall: opts.pollMs (Default 10000 ms).
 *
 * UMD: lädt als CommonJS-Modul (Node/Tests) oder als global
 * `RBLeaderboard` (Browser).
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory();
  } else {
    root.RBLeaderboard = factory();
  }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  var DEFAULT_API_BASE = "/tournament";
  var DEFAULT_POLL_MS = 10000;

  /* ---------------- pure Derivation ---------------- */

  /** Erstes Feld, das einen nicht-leeren String liefert (Alias-tolerant). */
  function firstString(obj, keys) {
    for (var i = 0; i < keys.length; i++) {
      var v = obj[keys[i]];
      if (typeof v === "string" && v.trim()) return v.trim();
    }
    return null;
  }

  function intOr(value, dflt) {
    if (typeof value === "number" && isFinite(value)) return Math.trunc(value);
    if (typeof value === "string" && value.trim() !== "" && isFinite(Number(value))) {
      return Math.trunc(Number(value));
    }
    return dflt;
  }

  /** Antwort-Payload → rohe Eintragsliste; null wenn keine erkennbar. */
  function extractEntries(payload) {
    if (Array.isArray(payload)) return payload;
    if (payload && typeof payload === "object") {
      var keys = ["players", "leaderboard", "entries", "ranking"];
      for (var i = 0; i < keys.length; i++) {
        if (Array.isArray(payload[keys[i]])) return payload[keys[i]];
      }
    }
    return null;
  }

  /** Ein roher Eintrag → normalisiertes Profil oder null (ohne Namen). */
  function normalizeEntry(e) {
    if (!e || typeof e !== "object") return null;
    var name = firstString(e, ["display_name", "name", "player", "player_id"]);
    if (!name) return null;
    var wins = intOr(e.wins, 0);
    var losses = intOr(e.losses, 0);
    var draws = intOr(e.draws, 0);
    return {
      name: name,
      elo: intOr(e.elo, 0),
      wins: wins,
      losses: losses,
      draws: draws,
      played: intOr(e.matches_played, wins + losses + draws),
    };
  }

  function unavailable(reason) {
    return {
      ok: false,
      rows: [],
      count: 0,
      line: "Rangliste nicht verfügbar",
      reason: reason || "Profil-/Ranglisten-API nicht erreichbar",
    };
  }

  /**
   * Ranglisten-Payload → { ok, rows, count, line }. Defensiv: unbekannte
   * Struktur/null ⇒ ok:false ("nicht verfügbar"); leere, aber gültige Liste
   * ⇒ ok:true mit count 0 ("noch keine Daten"). Sortierung: ELO absteigend,
   * Tie-Break gewonnene Spiele, dann Name.
   */
  function deriveLeaderboard(payload) {
    var raw = extractEntries(payload);
    if (!raw) return unavailable();

    var rows = [];
    for (var i = 0; i < raw.length; i++) {
      var n = normalizeEntry(raw[i]);
      if (n) rows.push(n);
    }

    rows.sort(function (a, b) {
      if (b.elo !== a.elo) return b.elo - a.elo;
      if (b.wins !== a.wins) return b.wins - a.wins;
      if (a.losses !== b.losses) return a.losses - b.losses;
      return a.name.localeCompare(b.name);
    });

    for (var r = 0; r < rows.length; r++) rows[r].rank = r + 1;

    return {
      ok: true,
      rows: rows,
      count: rows.length,
      line: rows.length === 0
        ? "Noch keine Ranglisten-Daten"
        : "Rangliste: " + rows.length + " Spieler",
    };
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

  function createLeaderboardWidget(opts) {
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
        render(deriveLeaderboard(null));
        return Promise.resolve();
      }
      return fetchImpl(base + "/leaderboard", { cache: "no-store" })
        .then(function (r) {
          if (!r.ok) throw new Error("HTTP " + r.status);
          return r.json();
        })
        .then(function (data) {
          render(deriveLeaderboard(data));
        })
        .catch(function () {
          render(deriveLeaderboard(null)); // defensiv: leere Liste statt Bruch
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
    deriveLeaderboard: deriveLeaderboard,
    resolveApiBase: resolveApiBase,
    createLeaderboardWidget: createLeaderboardWidget,
    DEFAULT_API_BASE: DEFAULT_API_BASE,
    DEFAULT_POLL_MS: DEFAULT_POLL_MS,
  };
});
