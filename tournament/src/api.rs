//! HTTP-API des Tournament-Servers (axum).
//!
//! Routen (v1):
//!   POST /lobby   Spieler registrieren        {"player": "<name>", "world": "A"|"B"}
//!   POST /ready   Welt ready                  {"world": "A"|"B"}
//!   POST /go      GO-Broadcast + Start        {} | {"retry": true}
//!   POST /send    Wave-Routing A→B            {"world": "A", "units": [...], "value": n}
//!   POST /report  Welt-Events (send_state)    {"world": "A", "event": "wave_start"|"hq_hp"|"score_update"|"hq_dead", ...}
//!   POST /referee/event  Spiel-Event an den Referee (Issue #268) {"world":"A","type":"ready"|"wave_done"|"hq_destroyed","level":n}
//!   GET  /referee/poll   Offene Referee-Commands einer Welt  ?world=A
//!   POST /rematch Reset in die Lobby          {}
//!   POST /wave    Operator-Wellen-Spawn       {"world":"A","n":3} → exec rb_wave 3
//!   GET  /state   Match-Zustand (Poll)        —
//!   GET  /health  Healthcheck                 —
//!   GET  /*       statische Web-UI            —
//!
//! Fehler: `{"error": "<meldung>", "type": "<invalid|not_found|conflict>"}`
//! mit 400/404/409. Unbekannte Felder in Bodies werden ignoriert
//! (vorwärtskompatibel, wie im Protokoll des Trainers üblich).

use crate::broadcast;
use crate::referee::{Command, GameEvent, GameEventKind, Referee, RefereeConfig};
use crate::state::{MatchState, Phase, ReadyEffect, StateError, World};
use axum::extract::{Query, State as AxumState};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Json, Router};
use serde::Deserialize;
use serde_json::{json, Value};
use std::collections::BTreeMap;
use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;
use tokio::sync::RwLock;

/// Konfiguration (aus Env im `main`, in Tests direkt konstruierbar).
#[derive(Debug, Clone)]
pub struct Config {
    pub host: String,
    pub port: u16,
    /// GO automatisch auslösen, sobald beide Welten ready sind.
    pub auto_go: bool,
    /// rbbridge-HTTP-Endpoints je Welt (für den GO-Broadcast); None = kein Push.
    pub bridge: [Option<String>; 2],
    /// Unpause-/Start-Kommandos, die die Bridge je Welt beim GO ausführt
    /// (Issue #22 Sync-Start): `exec_cmd_client "<cmd>"` als EIN gequotetes
    /// Argument (Issue #18). Reihenfolge = Ausführungsreihenfolge.
    pub go_commands: Vec<String>,
    /// Timeout je Broadcast-Endpoint.
    pub go_timeout: Duration,
    /// Start-HP jedes HQ.
    pub hq_hp_start: f64,
    /// Referee: Wellen-Deckel (0 = unbegrenzt) und Restart-Command (Issue #268).
    pub referee_max_wave: u32,
    pub referee_restart_cmd: String,
    /// Verzeichnis der statischen Web-UI.
    pub web_dir: PathBuf,
}

impl Config {
    pub fn bridge_for(&self, w: World) -> Option<&str> {
        self.bridge[Self::slot(w)].as_deref()
    }

    fn slot(w: World) -> usize {
        match w {
            World::A => 0,
            World::B => 1,
        }
    }
}

/// Geteilter App-State.
#[derive(Clone)]
pub struct AppState {
    pub state: Arc<RwLock<MatchState>>,
    /// Server-seitiger Referee (autoritative Event-/State-Quelle, Issue #268).
    pub referee: Arc<RwLock<Referee>>,
    pub cfg: Arc<Config>,
}

impl AppState {
    pub fn new(cfg: Config) -> Self {
        let referee = Referee::new(RefereeConfig {
            max_wave: cfg.referee_max_wave,
            restart_cmd: cfg.referee_restart_cmd.clone(),
        });
        AppState {
            state: Arc::new(RwLock::new(MatchState::new(cfg.hq_hp_start))),
            referee: Arc::new(RwLock::new(referee)),
            cfg: Arc::new(cfg),
        }
    }

    async fn with_state<F, T>(&self, f: F) -> Result<T, StateError>
    where
        F: FnOnce(&mut MatchState) -> Result<T, StateError>,
    {
        let mut guard = self.state.write().await;
        f(&mut guard)
    }
}

/// Antwort für einen Fehler (`{"error": ..., "type": ...}`).
pub struct ApiError(StateError);

impl From<StateError> for ApiError {
    fn from(e: StateError) -> Self {
        ApiError(e)
    }
}

impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        let body = json!({ "error": self.0.message, "type": self.0.code });
        (self.0.http_status(), Json(body)).into_response()
    }
}

pub type ApiResult<T> = Result<T, ApiError>;

fn parse_world(s: &str) -> Result<World, StateError> {
    s.parse::<World>()
        .map_err(|e| StateError::new("invalid", e))
}

/// Default-Wellennummer fuer `POST /wave` (`exec rb_wave <n>`).
const DEFAULT_WAVE_N: u32 = 3;
/// Obergrenze fuer `n` — schuetzt den Referee vor offensichtlichem Unfug.
const MAX_WAVE_N: u32 = 100;

// ---- Request-Bodies (unbekannte Felder werden ignoriert) ----

#[derive(Debug, Deserialize)]
struct LobbyReq {
    player: String,
    world: String,
}

#[derive(Debug, Deserialize)]
struct WorldReq {
    world: String,
}

#[derive(Debug, Deserialize, Default)]
struct GoReq {
    #[serde(default)]
    retry: bool,
}

#[derive(Debug, Deserialize)]
struct SendReq {
    world: String,
    units: Vec<UnitReq>,
    #[serde(default)]
    value: u64,
}

#[derive(Debug, Deserialize)]
struct UnitReq {
    unit: String,
    count: u32,
}

#[derive(Debug, Deserialize)]
struct ReportReq {
    world: String,
    event: String,
    /// Für event "hq_hp": aktueller HQ-HP der Welt (absolut).
    #[serde(default)]
    hp: Option<f64>,
    /// Für event "wave_start": gebauter Wert der Welt zum Lock.
    #[serde(default)]
    built_value: Option<u64>,
    /// Für event "score_update": Punktestand (send_state-Egress, Issue #13).
    #[serde(default)]
    score: Option<u64>,
    /// Für event "score_update": Ressourcen-Snapshot (z. B. {"iron":320}).
    #[serde(default)]
    resources: Option<BTreeMap<String, u64>>,
    /// Für event "score_update": aktuelle Wave.
    #[serde(default)]
    wave: Option<u32>,
}

#[derive(Debug, Deserialize)]
struct SpReq {
    player: String,
}

#[derive(Debug, Deserialize, Default)]
struct WaveReq {
    /// Ziel-Welt (Default "A": Solo/SP hat nur ein reales HQ in A).
    #[serde(default)]
    world: Option<String>,
    /// Wellennummer fuer `exec rb_wave <n>` (Default 3).
    #[serde(default)]
    n: Option<u32>,
}

#[derive(Debug, Deserialize, Default)]
struct EventsQuery {
    /// Cursor: nur Feed-Einträge mit `seq > since` liefern.
    #[serde(default)]
    since: Option<u64>,
}

#[derive(Debug, Deserialize)]
struct RefereeEventReq {
    world: String,
    /// `ready` | `wave_done` | `hq_destroyed` (snake_case).
    #[serde(rename = "type")]
    kind: GameEventKind,
    /// Nur für `wave_done`: abgeschlossenes Wellen-Level.
    #[serde(default)]
    level: Option<u32>,
}

#[derive(Debug, Deserialize)]
struct RefereePollQuery {
    world: String,
}

// ---- Router ----

pub fn router(app: AppState) -> Router {
    let web = app.cfg.web_dir.clone();
    Router::new()
        .route("/lobby", post(lobby))
        .route("/ready", post(ready))
        .route("/go", post(go))
        .route("/send", post(send))
        .route("/report", post(report))
        .route("/referee/event", post(referee_event))
        .route("/referee/poll", get(referee_poll))
        .route("/rematch", post(rematch))
        .route("/sp", post(sp))
        .route("/wave", post(wave))
        .route("/state", get(state_get))
        .route("/events", get(events))
        .route("/health", get(health))
        .fallback_service(tower_http::services::ServeDir::new(web))
        .with_state(app)
}

// ---- Handler ----

/// POST /lobby
async fn lobby(
    AxumState(app): AxumState<AppState>,
    Json(req): Json<LobbyReq>,
) -> ApiResult<Json<Value>> {
    let world = parse_world(&req.world)?;
    let effect = app
        .with_state(|s| s.lobby_register(world, &req.player))
        .await?;
    let view = app.state.read().await.view();
    let player = view
        .teams
        .get(world.as_str())
        .and_then(|t| t.player.clone());
    Ok(Json(json!({
        "world": world.as_str(),
        "player": player,
        "created": effect.created,
        "match_complete": effect.match_complete,
        "phase": view.phase,
    })))
}

/// POST /ready — bei beiden ready: AUTO_GO startet das Match (Broadcast async).
async fn ready(
    AxumState(app): AxumState<AppState>,
    Json(req): Json<WorldReq>,
) -> ApiResult<Json<Value>> {
    let world = parse_world(&req.world)?;
    let mut started = false;
    {
        let mut guard = app.state.write().await;
        let effect = guard.ready(world).map_err(ApiError::from)?;
        if effect == ReadyEffect::BothReady {
            guard.arm_go().map_err(ApiError::from)?; // Lobby → Ready (Ready-Check-Log)
            if app.cfg.auto_go {
                guard.start_match().map_err(ApiError::from)?; // Ready → Running (GO-Log)
                started = true;
            }
        }
    }
    if started {
        spawn_go_broadcast(&app, 1);
    }
    let view = app.state.read().await.view();
    let teams = serde_json::to_value(&view.teams).unwrap_or(Value::Null);
    Ok(Json(json!({
        "world": world.as_str(),
        "phase": view.phase,
        "match_started": started,
        "round": view.round,
        "teams": teams,
    })))
}

/// POST /go — Start + GO-Broadcast an beide rbbridge-Endpoints; `retry` für
/// erneuten Broadcast bei laufendem Match (z. B. nach Endpoint-Fehler).
async fn go(AxumState(app): AxumState<AppState>, Json(req): Json<GoReq>) -> ApiResult<Json<Value>> {
    let (started, round) = {
        let mut guard = app.state.write().await;
        match guard.phase {
            Phase::Running | Phase::Finished => {
                if guard.phase == Phase::Running && req.retry {
                    (false, guard.round)
                } else {
                    return Err(StateError::new(
                        "conflict",
                        format!(
                            "Match läuft bereits (Phase: {}) — retry nur mit {{\"retry\": true}}",
                            guard.phase.as_str()
                        ),
                    )
                    .into());
                }
            }
            _ => {
                let effect = guard.start_match()?;
                (effect.started, guard.round)
            }
        }
    };
    let broadcast = broadcast_go(&app, round).await;
    let view = app.state.read().await.view();
    Ok(Json(json!({
        "started": started,
        "phase": view.phase,
        "round": view.round,
        "broadcast": broadcast,
    })))
}

/// POST /send — Wave-Routing: Send von Welt X landet in der Queue der Gegner-Welt.
async fn send(
    AxumState(app): AxumState<AppState>,
    Json(req): Json<SendReq>,
) -> ApiResult<Json<Value>> {
    let world = parse_world(&req.world)?;
    let units: Vec<crate::state::UnitSpec> = req
        .units
        .into_iter()
        .map(|u| crate::state::UnitSpec {
            unit: u.unit,
            count: u.count,
        })
        .collect();
    let batch = app
        .with_state(|s| s.route_send(world, units, req.value))
        .await?;
    let to = world.opponent();
    let view = app.state.read().await.view();
    let pending_len = view
        .teams
        .get(to.as_str())
        .map(|t| t.pending_sends.len())
        .unwrap_or(0);
    Ok(Json(json!({
        "queued_for": to.as_str(),
        "round": batch.round,
        "batch": batch,
        "pending_sends": pending_len,
    })))
}

/// POST /report — Welt-Events (send_state-Egress, Issue #13 konzeptionell):
///   {"world":"A","event":"wave_start","built_value":1234}
///   {"world":"A","event":"hq_hp","hp":70.0}
///   {"world":"A","event":"hq_dead"}   (Mod-Log; #267)
async fn report(
    AxumState(app): AxumState<AppState>,
    Json(req): Json<ReportReq>,
) -> ApiResult<Json<Value>> {
    let world = parse_world(&req.world)?;
    match req.event.as_str() {
        "wave_start" => {
            let effect = app
                .with_state(|s| s.wave_start(world, req.built_value))
                .await?;
            let view = app.state.read().await.view();
            Ok(Json(json!({
                "world": world.as_str(),
                "event": "wave_start",
                "effect": match effect {
                    crate::state::WaveEffect::Locked => "locked",
                    crate::state::WaveEffect::Duplicate => "duplicate",
                },
                "round": view.round,
                "rounds_done": view.rounds_done,
                "phase": view.phase,
            })))
        }
        "hq_hp" => {
            let hp = req
                .hp
                .ok_or_else(|| StateError::new("invalid", "event 'hq_hp' benötigt Feld 'hp'"))?;
            let effect = app.with_state(|s| s.report_hq(world, hp)).await?;
            let view = app.state.read().await.view();
            let hq_hp = view.teams.get(world.as_str()).map(|t| t.hq_hp);
            Ok(Json(json!({
                "world": world.as_str(),
                "event": "hq_hp",
                "match_over": effect.match_over,
                "phase": view.phase,
                "winner": view.winner,
                "hq_hp": hq_hp,
            })))
        }
        "score_update" => {
            let score = req.score.unwrap_or(0);
            let resources = req.resources.unwrap_or_default();
            let wave = req.wave.unwrap_or(0);
            let effect = app
                .with_state(|s| s.score_update(world, score, resources, wave))
                .await?;
            let view = app.state.read().await.view();
            Ok(Json(json!({
                "world": world.as_str(),
                "event": "score_update",
                "score": score,
                "wave": wave,
                "changed": effect.changed,
                "phase": view.phase,
            })))
        }
        // HQ-Tod aus dem echten Spielverlauf (Mod-Log `event=hq_dead`, #267).
        // Wird als `HqDestroyed` in den Referee gespeist; der Referee liefert
        // daraus genau EIN `restart` (Duplikate/ohne laufendes Match leer =
        // ignoriert). Die Commands werden an die Bridge der Welt gepusht
        // (IO-Kanal, analog GO); erfolgreich gepushte Commands werden aus der
        // Referee-Outbox genommen, damit der Poll-Pfad sie nicht doppelt
        // zustellt (B1, „Push **oder** Poll“).
        //
        // KEIN `match_over`: #267 startet nur die Runde neu (Referee), es gibt
        // hier bewusst keinen MatchState-`FINISHED`-Übergang — Match-Ende läuft
        // weiterhin über `event=hq_hp` mit `hp <= 0`. Die Antwort spiegelt den
        // realen Referee-Zustand (`restart`/`rounds`/`running`).
        "hq_dead" | "hq_destroy" | "hq_destroyed" => {
            let ev = GameEvent {
                world,
                kind: GameEventKind::HqDestroyed,
                level: None,
            };
            let commands: Vec<Command> = app.referee.write().await.on_event(ev);
            // R2: „restart“ heißt konkret das konfigurierte Restart-Command,
            // nicht „irgendein emittiertes Command".
            let restart = commands
                .iter()
                .any(|c| c.command == app.cfg.referee_restart_cmd);
            let outcome = push_referee_commands(&app, world, &commands).await;
            // B1: erfolgreich gepushte Commands acken (Outbox-rest-los).
            let acked = app.referee.write().await.ack(world, &outcome.delivered);
            let referee_view = app.referee.read().await.world_view(world);
            let view = app.state.read().await.view();
            Ok(Json(json!({
                "world": world.as_str(),
                "event": "hq_dead",
                "phase": view.phase,
                "rounds": referee_view.rounds,
                "restart": restart,
                "ignored": commands.is_empty(),
                "referee_running": referee_view.running,
                "restart_pending": referee_view.restart_pending,
                "acked": acked,
                "commands": commands,
                "broadcast": outcome.results,
            })))
        }
        other => Err(StateError::new(
            "invalid",
            format!(
                "unbekanntes event '{other}' (erwartet: wave_start, hq_hp, score_update, hq_dead)"
            ),
        )
        .into()),
    }
}

/// Ergebnis eines Referee-Command-Pushes: die JSON-Results (Antwortfeld) und
/// die `cmd_id`s, die **erfolgreich** zugestellt wurden (Outbox-Ack, B1).
struct PushOutcome {
    results: Vec<Value>,
    delivered: Vec<u64>,
}

/// Pusht die vom Referee entschiedenen Commands an die Bridge der Welt
/// (IO-Kanal, analog GO-Broadcast, Issue #267).
///
/// Je Command: `POST <bridge_for(world)> {"command": …, "cmd_id": …,
/// "world": …, "reason": …}` via [`broadcast::post_json`]. Der `cmd_id` ist der
/// Dedup-Schlüssel des Relay-/Pipe-Vertrags (`docs/relay-pipe-contract.md`); der
/// HTTP-Adapter (`pipe_bridge`) liest nur `command`, kennt aber keine Pflicht-
/// felder mehr. Ohne konfigurierten Endpoint (`RBBRIDGE_<W>_URL`) wird `ok: null`
/// gemeldet — kein Panic, kein `unwrap`; die Zustellung übernimmt dann der Poll.
/// Mehrere Commands werden in ihrer Reihenfolge gepusht.
async fn push_referee_commands(app: &AppState, world: World, commands: &[Command]) -> PushOutcome {
    let mut results = Vec::with_capacity(commands.len());
    let mut delivered = Vec::new();
    for cmd in commands {
        match app.cfg.bridge_for(world) {
            Some(url) => {
                let payload = json!({
                    "command": cmd.command,
                    "cmd_id": cmd.cmd_id,
                    "world": world.as_str(),
                    "reason": cmd.reason,
                });
                let res = broadcast::post_json(url, &payload, app.cfg.go_timeout).await;
                let ok = res.ok();
                if ok {
                    delivered.push(cmd.cmd_id);
                }
                results.push(json!({
                    "command": cmd.command,
                    "cmd_id": cmd.cmd_id,
                    "ok": ok,
                    "status": res.status,
                    "error": res.error,
                    "endpoint": url,
                }));
            }
            None => results.push(json!({
                "command": cmd.command,
                "cmd_id": cmd.cmd_id,
                "ok": Value::Null,
                "note": "kein Endpoint konfiguriert (RBBRIDGE_<W>_URL) — Zustellung via GET /referee/poll",
            })),
        }
    }
    PushOutcome { results, delivered }
}

/// POST /referee/event — Spiel-Event an den Referee (Issue #268).
///
/// Die in-game Lua ist reiner **Executor**: sie meldet Ereignisse (Welle
/// fertig, HQ zerstört, ready) über den Rückkanal (Relay/Pipe, #265) und
/// bekommt hier die daraus entschiedenen Commands zurück. Die Antwort enthält
/// zusätzlich den Referee-Zustand der Welt; dieselben Commands liegen in der
/// Outbox (`GET /referee/poll?world=A`), falls der Aufrufer nur meldet.
///
/// Duplikate/veraltete Events sind idempotent (keine Doppel-Welle, kein
/// Doppel-Restart).
async fn referee_event(
    AxumState(app): AxumState<AppState>,
    Json(req): Json<RefereeEventReq>,
) -> ApiResult<Json<Value>> {
    let world = parse_world(&req.world)?;
    let kind = req.kind;
    if kind == GameEventKind::WaveDone && req.level.is_none() {
        return Err(StateError::new("invalid", "type 'wave_done' benötigt Feld 'level'").into());
    }
    let ev = GameEvent {
        world,
        kind,
        level: req.level,
    };
    let commands: Vec<Command> = app.referee.write().await.on_event(ev);
    let view = app.referee.read().await.world_view(world);
    Ok(Json(json!({
        "world": world.as_str(),
        "type": kind,
        "accepted": true,
        "commands": commands,
        "state": view,
    })))
}

/// GET /referee/poll — offene Referee-Commands einer Welt abholen (Outbox drain).
async fn referee_poll(
    AxumState(app): AxumState<AppState>,
    Query(q): Query<RefereePollQuery>,
) -> ApiResult<Json<Value>> {
    let world = parse_world(&q.world)?;
    let commands = app.referee.write().await.poll(world);
    let view = app.referee.read().await.world_view(world);
    Ok(Json(json!({
        "world": world.as_str(),
        "commands": commands,
        "state": view,
    })))
}

/// POST /rematch — Reset in die Lobby (Spieler bleiben registriert).
async fn rematch(AxumState(app): AxumState<AppState>) -> ApiResult<Json<Value>> {
    app.with_state(|s| s.rematch()).await?;
    let view = app.state.read().await.view();
    Ok(Json(json!({
        "phase": view.phase,
        "rematches": view.rematches,
    })))
}

/// POST /sp — SP-Mode starten (Issue #44): P1 vs MIRROR (Server-Spiegel).
/// Kein zweiter Client nötig; der Server erzeugt die Gegner-Seite.
async fn sp(AxumState(app): AxumState<AppState>, Json(req): Json<SpReq>) -> ApiResult<Json<Value>> {
    let effect = app.with_state(|s| s.start_sp(&req.player)).await?;
    let view = app.state.read().await.view();
    Ok(Json(json!({
        "started": effect.started,
        "phase": view.phase,
        "round": view.round,
        "mode": view.mode,
        "teams": view.teams,
    })))
}

/// `exec_result`-Erfolg einer Bridge-Antwort — gleiche Ableitung wie
/// `waveResult()` in `site/solo-cockpit.js`: `ok:true` schlaegt durch, sonst
/// zaehlt `results[]` (nicht-leer und alle `ok:true`). `None` ohne JSON-Body.
fn exec_result_ok(body: Option<&Value>) -> Option<bool> {
    let body = body?;
    if body.get("ok").and_then(Value::as_bool) == Some(true) {
        return Some(true);
    }
    if let Some(results) = body.get("results").and_then(Value::as_array) {
        if !results.is_empty() {
            return Some(
                results
                    .iter()
                    .all(|r| r.get("ok").and_then(Value::as_bool) == Some(true)),
            );
        }
    }
    Some(false)
}

/// POST /wave — Operator-Wellen-Spawn (Issue #266).
///
/// Leitet `exec rb_wave <n>` an den Bridge-/Relay-HTTP-Endpoint der Welt
/// weiter (`RBBRIDGE_*_URL`, `POST /exec {"command":"rb_wave <n>"}`) und gibt
/// dessen `exec_result` an die Web-UI zurueck. Kein Endpoint konfiguriert →
/// 409 (kein Transport).
///
/// `ok` ist der **Zustell-Erfolg** (HTTP 2xx, kein Transportfehler), `exec_ok`
/// der **Ausfuehr-Erfolg** aus dem durchgereichten `exec_result`. Eine Bridge,
/// die mit 200 + `exec_result: {ok:false}` antwortet (z. B. `timeout`),
/// liefert daher HTTP 200 mit `ok:true` und `exec_ok:false` plus `error`/
/// `exec_result`, damit die UI den Grund anzeigen kann.
async fn wave(
    AxumState(app): AxumState<AppState>,
    Json(req): Json<WaveReq>,
) -> ApiResult<Json<Value>> {
    let world = match req.world.as_deref() {
        None | Some("") => World::A,
        Some(s) => parse_world(s)?,
    };
    let n = req.n.unwrap_or(DEFAULT_WAVE_N);
    if n == 0 || n > MAX_WAVE_N {
        return Err(StateError::new(
            "invalid",
            format!("n muss zwischen 1 und {MAX_WAVE_N} liegen (war {n})"),
        )
        .into());
    }
    let command = format!("rb_wave {n}");
    let Some(endpoint) = app.cfg.bridge_for(world).map(str::to_string) else {
        app.state.write().await.log_wave(
            world,
            &command,
            false,
            None,
            Some("kein Bridge-Endpoint konfiguriert"),
        );
        return Err(StateError::new(
            "conflict",
            format!(
                "kein Bridge-Endpoint fuer Welt {world} konfiguriert (RBBRIDGE_{}_URL) — kein Transport fuer {command}",
                world.as_str()
            ),
        )
        .into());
    };

    let payload = json!({ "command": command });
    let res = broadcast::post_json(&endpoint, &payload, app.cfg.go_timeout).await;
    let delivered = res.ok();
    let exec_ok = exec_result_ok(res.body.as_ref());
    app.state
        .write()
        .await
        .log_wave(world, &command, delivered, res.status, res.error.as_deref());

    Ok(Json(json!({
        "ok": delivered,
        "exec_ok": exec_ok,
        "world": world.as_str(),
        "command": command,
        "endpoint": endpoint,
        "status": res.status,
        "error": res.error,
        "exec_result": res.body,
    })))
}

/// GET /events — Feed-Cursor für Poll-Bridges (Telegram-Feed u. a.).
/// `?since=<seq>` liefert nur Einträge mit `seq > since`; `last_seq` ist die
/// höchste vergebene Sequenz (Cursor-Stand).
async fn events(
    AxumState(app): AxumState<AppState>,
    Query(q): Query<EventsQuery>,
) -> ApiResult<Json<Value>> {
    let since = q.since.unwrap_or(0);
    let (feed_events, last_seq) = {
        let guard = app.state.read().await;
        (guard.feed_since(since), guard.last_seq())
    };
    Ok(Json(json!({ "events": feed_events, "last_seq": last_seq })))
}

/// GET /state — kompakter Match-Zustand (wird von UI + Bridges gepollt).
async fn state_get(AxumState(app): AxumState<AppState>) -> ApiResult<Json<Value>> {
    let view = app.state.read().await.view();
    Ok(Json(serde_json::to_value(view).unwrap_or(Value::Null)))
}

/// GET /health
async fn health(AxumState(app): AxumState<AppState>) -> ApiResult<Json<Value>> {
    let phase = app.state.read().await.phase.as_str().to_string();
    Ok(Json(json!({ "ok": true, "phase": phase })))
}

// ---- GO-Broadcast ----

/// GO-Payload an beide rbbridge-Endpoints (Push; Poll auf /state ist Fallback).
///
/// `commands` = die Unpause-/Start-Kommandos, die die Bridge je Welt in dieser
/// Reihenfolge ausführt (Issue #22 Sync-Start): `exec_cmd_client "<cmd>"` als
/// EIN gequotetes Argument (Issue #18). Default (verifiziert, SYNC_START.md):
/// `debug_dom_resume` (DOM-Ebene). Die native Server-Pause (`resume_game`) ist
/// unverifiziert und wird per Env ergänzt; ihr Fallback ist das automatische
/// `ResumeGame` beim Client-Join (`server_pause_game_when_empty`).
fn go_payload(match_id: &str, round: u32, commands: &[String]) -> Value {
    json!({
        "cmd": "go",
        "match_id": match_id,
        "round": round,
        "commands": commands,
    })
}

/// Broadcast an beide Endpoints, Ergebnisse im State festhalten.
async fn broadcast_go(app: &AppState, round: u32) -> Value {
    let match_id = {
        let guard = app.state.read().await;
        guard.match_id.clone()
    };
    let commands = app.cfg.go_commands.clone();
    let payload = go_payload(&match_id, round, &commands);
    let timeout = app.cfg.go_timeout;

    let mut results = serde_json::Map::new();
    for w in World::ALL {
        match app.cfg.bridge_for(w) {
            Some(url) => {
                let res = broadcast::post_json(url, &payload, timeout).await;
                app.state.write().await.record_broadcast(
                    w,
                    res.ok(),
                    res.error.clone(),
                    Some(url.to_string()),
                );
                results.insert(
                    w.as_str().to_string(),
                    json!({
                        "ok": res.ok(),
                        "status": res.status,
                        "error": res.error,
                        "endpoint": url,
                    }),
                );
            }
            None => {
                results.insert(w.as_str().to_string(), json!({"ok": null, "note": "kein Endpoint konfiguriert — Bridges pollten /state"}));
            }
        }
    }
    Value::Object(results)
}

/// Fire-and-forget-Broadcast (AUTO_GO-Pfad): läuft im Hintergrund, Ergebnis
/// landet in `go_broadcast` von /state (UI zeigt Zustell-Status an).
fn spawn_go_broadcast(app: &AppState, round: u32) {
    let app = app.clone();
    tokio::spawn(async move {
        let _ = broadcast_go(&app, round).await;
    });
}

// ---- Tests (HTTP-Level gegen echte Router + Mock-Endpoints) ----

#[cfg(test)]
mod tests {
    use super::*;
    use axum::body::to_bytes;
    use axum::http::Request;
    use axum::http::StatusCode;
    use serde_json::Value;
    use std::net::SocketAddr;
    use tower::ServiceExt;

    fn test_cfg() -> Config {
        Config {
            host: "127.0.0.1".into(),
            port: 0,
            auto_go: false,
            bridge: [None, None],
            go_commands: vec!["debug_dom_resume".to_string()],
            go_timeout: Duration::from_millis(800),
            hq_hp_start: 100.0,
            referee_max_wave: 0,
            referee_restart_cmd: "restart".to_string(),
            web_dir: PathBuf::from("web"), // wird in Tests nicht gebraucht
        }
    }

    async fn make_app(cfg: Config) -> Router {
        router(AppState::new(cfg))
    }

    async fn call(
        app: &Router,
        method: &str,
        uri: &str,
        body: Option<Value>,
    ) -> (StatusCode, Value) {
        let builder = Request::builder().method(method).uri(uri);
        let req = match body {
            Some(b) => builder
                .header("content-type", "application/json")
                .body(axum::body::Body::from(b.to_string()))
                .unwrap(),
            None => builder.body(axum::body::Body::empty()).unwrap(),
        };
        let resp = app.clone().oneshot(req).await.unwrap();
        let status = resp.status();
        let bytes = to_bytes(resp.into_body(), 1 << 20).await.unwrap();
        let value: Value = serde_json::from_slice(&bytes).unwrap_or(Value::Null);
        (status, value)
    }

    async fn register(app: &Router, world: &str, player: &str) -> (StatusCode, Value) {
        call(
            app,
            "POST",
            "/lobby",
            Some(json!({"player": player, "world": world})),
        )
        .await
    }

    fn err_type(v: &Value) -> String {
        v["type"].as_str().unwrap_or("?").to_string()
    }

    /// Setup: beide Spieler registriert; AUTO_GO aus; beide ready; Phase Ready.
    async fn ready_state(app: &Router) {
        assert_eq!(register(app, "A", "momo").await.0, StatusCode::OK);
        assert_eq!(register(app, "B", "matheo").await.0, StatusCode::OK);
        let (s, v) = call(app, "POST", "/ready", Some(json!({"world": "A"}))).await;
        assert_eq!(s, StatusCode::OK);
        assert_eq!(v["match_started"], false);
        let (s, v) = call(app, "POST", "/ready", Some(json!({"world": "B"}))).await;
        assert_eq!(s, StatusCode::OK);
        assert_eq!(v["phase"], "ready"); // AUTO_GO aus → wartet auf /go
        assert_eq!(v["match_started"], false);
    }

    #[tokio::test]
    async fn full_flow_via_http() {
        let app = make_app(test_cfg()).await;
        // Lobby-Validierung
        let (s, v) = register(&app, "A", "").await;
        assert_eq!(s, StatusCode::BAD_REQUEST);
        assert_eq!(err_type(&v), "invalid");
        let (s, _) = register(&app, "A", "momo").await;
        assert_eq!(s, StatusCode::OK);
        let (s, v) = register(&app, "C", "x").await;
        assert_eq!(s, StatusCode::BAD_REQUEST);
        assert_eq!(err_type(&v), "invalid");
        let (s, _) = register(&app, "B", "matheo").await;
        assert_eq!(s, StatusCode::OK);

        // Ready ohne beide → Waiting, keine Phase Ready
        let (s, v) = call(&app, "POST", "/ready", Some(json!({"world": "A"}))).await;
        assert_eq!(s, StatusCode::OK);
        assert_eq!(v["phase"], "lobby");
        let (s, _) = call(&app, "POST", "/ready", Some(json!({"world": "B"}))).await;
        assert_eq!(s, StatusCode::OK);

        // Ready erst nach Registrierung → 404
        let app2 = make_app(test_cfg()).await;
        register(&app2, "A", "momo").await;
        let (s, v) = call(&app2, "POST", "/ready", Some(json!({"world": "B"}))).await;
        assert_eq!(s, StatusCode::NOT_FOUND);
        assert_eq!(err_type(&v), "not_found");
        drop(app2);

        // /go startet aus Phase Ready
        let (s, v) = call(&app, "POST", "/go", Some(json!({}))).await;
        assert_eq!(s, StatusCode::OK);
        assert_eq!(v["started"], true);
        assert_eq!(v["phase"], "running");
        assert_eq!(v["round"], 1);
        // ohne Endpoints: kein Push konfiguriert → null-ok
        assert_eq!(v["broadcast"]["A"]["ok"], Value::Null);

        // /go erneut ohne retry → 409
        let (s, v) = call(&app, "POST", "/go", Some(json!({}))).await;
        assert_eq!(s, StatusCode::CONFLICT);
        assert_eq!(err_type(&v), "conflict");

        // Send-Routing
        let (s, v) = call(
            &app,
            "POST",
            "/send",
            Some(json!({"world": "A", "units": [{"unit": "creeper", "count": 4}], "value": 400})),
        )
        .await;
        assert_eq!(s, StatusCode::OK);
        assert_eq!(v["queued_for"], "B");
        assert_eq!(v["round"], 1);
        let (s, v) = call(
            &app,
            "POST",
            "/send",
            Some(json!({"world": "B", "units": [{"unit": "brute", "count": 1}]})),
        )
        .await;
        assert_eq!(s, StatusCode::OK);
        assert_eq!(v["queued_for"], "A");

        // Wellenstart A → reveal Teil 1
        let (s, v) = call(
            &app,
            "POST",
            "/report",
            Some(json!({"world": "A", "event": "wave_start", "built_value": 8000})),
        )
        .await;
        assert_eq!(s, StatusCode::OK);
        assert_eq!(v["effect"], "locked");
        assert_eq!(v["round"], 1);
        // Wellenstart B → Runde 2
        let (s, v) = call(
            &app,
            "POST",
            "/report",
            Some(json!({"world": "B", "event": "wave_start", "built_value": 6400})),
        )
        .await;
        assert_eq!(s, StatusCode::OK);
        assert_eq!(v["round"], 2);
        assert_eq!(v["rounds_done"], 1);

        // /state zeigt Reveal + Pending
        let (s, v) = call(&app, "GET", "/state", None).await;
        assert_eq!(s, StatusCode::OK);
        assert_eq!(v["phase"], "running");
        assert_eq!(v["round"], 2);
        assert_eq!(v["teams"]["A"]["hq_hp"], 100.0);
        assert_eq!(v["teams"]["B"]["player"], "matheo");
        assert_eq!(v["reveal"]["round"], 1);
        assert_eq!(v["reveal"]["built"]["A"], 8000);
        assert_eq!(v["reveal"]["incoming"]["A"][0]["value"], 0); // B-Send ohne value
        assert_eq!(v["reveal"]["incoming"]["B"][0]["value"], 400);
        assert_eq!(
            v["teams"]["A"]["pending_sends"].as_array().unwrap().len(),
            0
        );
        assert_eq!(
            v["teams"]["B"]["pending_sends"].as_array().unwrap().len(),
            0
        );

        // HQ-Verlust → Finished
        let (s, v) = call(
            &app,
            "POST",
            "/report",
            Some(json!({"world": "A", "event": "hq_hp", "hp": 0})),
        )
        .await;
        assert_eq!(s, StatusCode::OK);
        assert_eq!(v["match_over"], true);
        assert_eq!(v["winner"], "B");
        let (s, v) = call(&app, "GET", "/state", None).await;
        assert_eq!(s, StatusCode::OK);
        assert_eq!(v["phase"], "finished");
        assert_eq!(v["winner"], "B");
        // feed enthält finish
        let feed = v["feed"].as_array().unwrap();
        assert!(feed.iter().any(|e| e["kind"] == "finish"));

        // Rematch → Lobby, Spieler bleiben
        let (s, v) = call(&app, "POST", "/rematch", None).await;
        assert_eq!(s, StatusCode::OK);
        assert_eq!(v["phase"], "lobby");
        assert_eq!(v["rematches"], 1);
        let (s, v) = call(&app, "GET", "/state", None).await;
        assert_eq!(s, StatusCode::OK);
        assert_eq!(v["teams"]["A"]["player"], "momo");
        assert_eq!(v["teams"]["A"]["hq_hp"], 100.0);
        assert_eq!(v["round"], 0);
    }

    #[tokio::test]
    async fn go_requires_registered_worlds() {
        let app = make_app(test_cfg()).await;
        let (s, v) = call(&app, "POST", "/go", Some(json!({}))).await;
        assert_eq!(s, StatusCode::CONFLICT);
        assert_eq!(err_type(&v), "conflict");
        register(&app, "A", "momo").await;
        let (s, _) = call(&app, "POST", "/go", Some(json!({}))).await;
        assert_eq!(s, StatusCode::CONFLICT);
        register(&app, "B", "matheo").await;
        let (s, v) = call(&app, "POST", "/go", Some(json!({}))).await;
        assert_eq!(s, StatusCode::OK);
        assert_eq!(v["started"], true);
    }

    #[tokio::test]
    async fn auto_go_starts_on_second_ready_and_broadcasts() {
        let mut cfg = test_cfg();
        cfg.auto_go = true;
        let app = make_app(cfg).await;
        register(&app, "A", "momo").await;
        register(&app, "B", "matheo").await;
        let (_, v) = call(&app, "POST", "/ready", Some(json!({"world": "A"}))).await;
        assert_eq!(v["match_started"], false);
        let (_, v) = call(&app, "POST", "/ready", Some(json!({"world": "B"}))).await;
        assert_eq!(v["match_started"], true);
        assert_eq!(v["phase"], "running");
        let (_, v) = call(&app, "GET", "/state", None).await;
        assert_eq!(v["round"], 1);
    }

    #[tokio::test]
    async fn send_report_validations() {
        let app = make_app(test_cfg()).await;
        // Send in Lobby → 409
        register(&app, "A", "momo").await;
        register(&app, "B", "matheo").await;
        let (s, v) = call(
            &app,
            "POST",
            "/send",
            Some(json!({"world": "A", "units": [{"unit": "x", "count": 1}]})),
        )
        .await;
        assert_eq!(s, StatusCode::CONFLICT);
        assert_eq!(err_type(&v), "conflict");
        ready_state(&app).await;
        call(&app, "POST", "/go", Some(json!({}))).await;

        let (s, _) = call(
            &app,
            "POST",
            "/send",
            Some(json!({"world": "A", "units": []})),
        )
        .await;
        assert_eq!(s, StatusCode::BAD_REQUEST);
        let (s, _) = call(
            &app,
            "POST",
            "/send",
            Some(json!({"world": "A", "units": [{"unit": "x", "count": 0}]})),
        )
        .await;
        assert_eq!(s, StatusCode::BAD_REQUEST);
        let (s, _) = call(
            &app,
            "POST",
            "/send",
            Some(json!({"world": "Z", "units": [{"unit": "x", "count": 1}]})),
        )
        .await;
        assert_eq!(s, StatusCode::BAD_REQUEST);

        // Report-Validierung
        let (s, v) = call(
            &app,
            "POST",
            "/report",
            Some(json!({"world": "A", "event": "unsinn"})),
        )
        .await;
        assert_eq!(s, StatusCode::BAD_REQUEST);
        assert_eq!(err_type(&v), "invalid");
        let (s, _) = call(
            &app,
            "POST",
            "/report",
            Some(json!({"world": "A", "event": "hq_hp"})),
        )
        .await;
        assert_eq!(s, StatusCode::BAD_REQUEST); // hp fehlt
        let (s, _) = call(
            &app,
            "POST",
            "/report",
            Some(json!({"world": "A", "event": "hq_hp", "hp": -1})),
        )
        .await;
        assert_eq!(s, StatusCode::BAD_REQUEST);
        let (s, v) = call(
            &app,
            "POST",
            "/report",
            Some(json!({"world": "A", "event": "hq_hp", "hp": 42.5})),
        )
        .await;
        assert_eq!(s, StatusCode::OK);
        assert_eq!(v["hq_hp"], 42.5);
        let (_, v) = call(&app, "GET", "/state", None).await;
        assert_eq!(v["teams"]["A"]["hq_hp"], 42.5);
        // hq_hp nach Finish → 409
        call(
            &app,
            "POST",
            "/report",
            Some(json!({"world": "A", "event": "hq_hp", "hp": 0})),
        )
        .await;
        let (s, _) = call(
            &app,
            "POST",
            "/report",
            Some(json!({"world": "B", "event": "hq_hp", "hp": 30})),
        )
        .await;
        assert_eq!(s, StatusCode::CONFLICT);
        // wave_start nach Finish → 409
        let (s, _) = call(
            &app,
            "POST",
            "/report",
            Some(json!({"world": "B", "event": "wave_start"})),
        )
        .await;
        assert_eq!(s, StatusCode::CONFLICT);
    }

    #[tokio::test]
    async fn rematch_blocked_while_running() {
        let app = make_app(test_cfg()).await;
        register(&app, "A", "momo").await;
        register(&app, "B", "matheo").await;
        call(&app, "POST", "/go", Some(json!({}))).await;
        let (s, v) = call(&app, "POST", "/rematch", None).await;
        assert_eq!(s, StatusCode::CONFLICT);
        assert_eq!(err_type(&v), "conflict");
    }

    #[tokio::test]
    async fn score_update_snapshot_appears_in_state() {
        let app = make_app(test_cfg()).await;
        // Vor Registrierung → 404.
        let (s, _) = call(
            &app,
            "POST",
            "/report",
            Some(json!({"world": "A", "event": "score_update", "score": 10})),
        )
        .await;
        assert_eq!(s, StatusCode::NOT_FOUND);

        ready_state(&app).await;
        call(&app, "POST", "/go", Some(json!({}))).await;

        // Periodischer Snapshot mit Ressourcen.
        let (s, v) = call(
            &app,
            "POST",
            "/report",
            Some(json!({
                "world": "A", "event": "score_update",
                "score": 1240, "resources": {"iron": 320, "carbon": 80}, "wave": 4
            })),
        )
        .await;
        assert_eq!(s, StatusCode::OK);
        assert_eq!(v["event"], "score_update");
        assert_eq!(v["changed"], true);

        // /state zeigt den Snapshot in der Team-View.
        let (_, st) = call(&app, "GET", "/state", None).await;
        assert_eq!(st["teams"]["A"]["score"], 1240);
        assert_eq!(st["teams"]["A"]["wave"], 4);
        assert_eq!(st["teams"]["A"]["resources"]["iron"], 320);
        assert_eq!(st["teams"]["A"]["resources"]["carbon"], 80);
        assert_eq!(st["teams"]["B"]["score"], 0); // unberührt

        // Unveränderter Snapshot → changed=false, kein weiterer Feed-Eintrag.
        let (_, v2) = call(
            &app,
            "POST",
            "/report",
            Some(json!({
                "world": "A", "event": "score_update",
                "score": 1240, "resources": {"iron": 320, "carbon": 80}, "wave": 4
            })),
        )
        .await;
        assert_eq!(v2["changed"], false);

        // Feed dokumentiert die Score-Änderung.
        let (_, ev) = call(&app, "GET", "/events", None).await;
        assert!(ev["events"]
            .as_array()
            .unwrap()
            .iter()
            .any(|e| e["kind"] == "score"));
    }

    /// Mock-HTTP-Endpoint: akzeptiert eine Verbindung, liefert Request-Text.
    async fn mock_endpoint() -> (SocketAddr, tokio::sync::oneshot::Receiver<String>) {
        use tokio::io::AsyncReadExt;
        use tokio::io::AsyncWriteExt;
        use tokio::net::TcpListener;
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        let (tx, rx) = tokio::sync::oneshot::channel();
        tokio::spawn(async move {
            let (mut sock, _) = listener.accept().await.unwrap();
            let mut buf = [0u8; 8192];
            let n = sock.read(&mut buf).await.unwrap();
            let _ = sock
                .write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")
                .await;
            let _ = tx.send(String::from_utf8_lossy(&buf[..n]).to_string());
        });
        (addr, rx)
    }

    #[tokio::test]
    async fn go_broadcasts_to_both_bridge_endpoints() {
        let (addr_a, rx_a) = mock_endpoint().await;
        let (addr_b, rx_b) = mock_endpoint().await;
        let mut cfg = test_cfg();
        cfg.bridge = [
            Some(format!("http://{addr_a}/exec")),
            Some(format!("http://{addr_b}/exec")),
        ];
        let app = make_app(cfg).await;
        ready_state(&app).await;

        let (s, v) = call(&app, "POST", "/go", Some(json!({}))).await;
        assert_eq!(s, StatusCode::OK);
        assert_eq!(v["broadcast"]["A"]["ok"], true);
        assert_eq!(v["broadcast"]["B"]["ok"], true);

        // beide Endpoints haben das GO gesehen
        let req_a = tokio::time::timeout(Duration::from_secs(2), rx_a)
            .await
            .unwrap()
            .unwrap();
        let req_b = tokio::time::timeout(Duration::from_secs(2), rx_b)
            .await
            .unwrap()
            .unwrap();
        for req in [&req_a, &req_b] {
            assert!(req.starts_with("POST /exec HTTP/1.1"), "req: {req}");
            assert!(req.contains("\"cmd\":\"go\""), "req: {req}");
            assert!(req.contains("\"round\":1"), "req: {req}");
            assert!(req.contains("rift-1"), "req: {req}");
            assert!(
                req.contains("\"commands\":[\"debug_dom_resume\"]"),
                "req: {req}"
            );
        }

        // /state zeigt Zustell-Status
        let (_, v) = call(&app, "GET", "/state", None).await;
        assert_eq!(v["teams"]["A"]["go_broadcast"]["ok"], true);
        assert_eq!(
            v["teams"]["A"]["go_broadcast"]["endpoint"],
            format!("http://{addr_a}/exec")
        );

        // Retry-Broadcast bei laufendem Match (Endpoint down simulieren → Fehler sichtbar)
        let (s, v) = call(&app, "POST", "/go", Some(json!({"retry": true}))).await;
        assert_eq!(s, StatusCode::OK);
        assert_eq!(v["started"], false);
        // zweiter Retry: Endpoints sind weg (nur 1 Verbindung je Listener) → Fehler
        assert_eq!(v["broadcast"]["A"]["ok"], false);
        assert!(v["broadcast"]["A"]["error"].is_string());
        let (_, v) = call(&app, "GET", "/state", None).await;
        assert_eq!(v["teams"]["A"]["go_broadcast"]["ok"], false);
    }

    /// Mock-HTTP-Endpoint mit konfigurierbarem Status + JSON-Body (exec_result).
    async fn mock_json_response(
        status: &'static str,
        payload: &'static str,
    ) -> (SocketAddr, tokio::sync::oneshot::Receiver<String>) {
        use tokio::io::{AsyncReadExt, AsyncWriteExt};
        use tokio::net::TcpListener;
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        let (tx, rx) = tokio::sync::oneshot::channel();
        tokio::spawn(async move {
            let (mut sock, _) = listener.accept().await.unwrap();
            let mut buf = [0u8; 8192];
            let n = sock.read(&mut buf).await.unwrap();
            let resp = format!(
                "HTTP/1.1 {status}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{payload}",
                payload.len()
            );
            let _ = sock.write_all(resp.as_bytes()).await;
            let _ = tx.send(String::from_utf8_lossy(&buf[..n]).to_string());
        });
        (addr, rx)
    }

    /// OHNE Player (#266): Klick-Pfad Referee → Bridge → `exec_result ok:true`.
    #[tokio::test]
    async fn wave_posts_rb_wave_and_returns_exec_result() {
        let (addr, rx) = mock_json_response(
            "200 OK",
            r#"{"ok":true,"results":[{"command":"rb_wave 3","ok":true}]}"#,
        )
        .await;
        let mut cfg = test_cfg();
        cfg.bridge = [Some(format!("http://{addr}/exec")), None];
        let app = make_app(cfg).await;

        let (s, v) = call(&app, "POST", "/wave", Some(json!({"world": "A", "n": 3}))).await;
        assert_eq!(s, StatusCode::OK);
        assert_eq!(v["ok"], true);
        assert_eq!(v["exec_ok"], true);
        assert_eq!(v["world"], "A");
        assert_eq!(v["command"], "rb_wave 3");
        assert_eq!(v["endpoint"], format!("http://{addr}/exec"));
        assert_eq!(v["exec_result"]["ok"], true);
        assert_eq!(v["exec_result"]["results"][0]["command"], "rb_wave 3");
        assert_eq!(v["exec_result"]["results"][0]["ok"], true);

        // Die Bridge hat exakt das exec-Kommando gesehen.
        let req = tokio::time::timeout(Duration::from_secs(2), rx)
            .await
            .unwrap()
            .unwrap();
        assert!(req.starts_with("POST /exec HTTP/1.1"), "req: {req}");
        assert!(req.contains("\"command\":\"rb_wave 3\""), "req: {req}");

        // Feed dokumentiert den Spawn als kind=wave.
        let (_, st) = call(&app, "GET", "/state", None).await;
        assert!(st["feed"]
            .as_array()
            .unwrap()
            .iter()
            .any(|e| e["kind"] == "wave" && e["msg"].as_str().unwrap_or("").contains("rb_wave 3")));
    }

    #[tokio::test]
    async fn wave_defaults_to_world_a_and_n3() {
        let (addr, rx) = mock_json_response(
            "200 OK",
            r#"{"ok":true,"results":[{"command":"rb_wave 3","ok":true}]}"#,
        )
        .await;
        let mut cfg = test_cfg();
        cfg.bridge = [Some(format!("http://{addr}/exec")), None];
        let app = make_app(cfg).await;
        let (s, v) = call(&app, "POST", "/wave", Some(json!({}))).await;
        assert_eq!(s, StatusCode::OK);
        assert_eq!(v["world"], "A");
        assert_eq!(v["command"], "rb_wave 3");
        let req = tokio::time::timeout(Duration::from_secs(2), rx)
            .await
            .unwrap()
            .unwrap();
        assert!(req.contains("\"command\":\"rb_wave 3\""), "req: {req}");
    }

    #[tokio::test]
    async fn wave_without_bridge_endpoint_is_conflict() {
        let app = make_app(test_cfg()).await; // bridge = [None, None]
        let (s, v) = call(&app, "POST", "/wave", Some(json!({"world": "A", "n": 3}))).await;
        assert_eq!(s, StatusCode::CONFLICT);
        assert_eq!(err_type(&v), "conflict");
        // Auch ohne Transport wird der Versuch im Feed vermerkt.
        let (_, st) = call(&app, "GET", "/state", None).await;
        assert!(st["feed"]
            .as_array()
            .unwrap()
            .iter()
            .any(|e| e["kind"] == "wave"));
    }

    #[tokio::test]
    async fn wave_rejects_invalid_n_and_world() {
        let app = make_app(test_cfg()).await;
        for n in [0u32, 101u32] {
            let (s, v) = call(&app, "POST", "/wave", Some(json!({"world": "A", "n": n}))).await;
            assert_eq!(s, StatusCode::BAD_REQUEST, "n={n}");
            assert_eq!(err_type(&v), "invalid");
        }
        let (s, v) = call(&app, "POST", "/wave", Some(json!({"world": "C", "n": 3}))).await;
        assert_eq!(s, StatusCode::BAD_REQUEST);
        assert_eq!(err_type(&v), "invalid");
    }

    /// Bridge meldet `pipe_unavailable` (503): Referee bleibt 200, `ok:false`
    /// + durchgereichter Grund — die UI kann den Fehler anzeigen.
    #[tokio::test]
    async fn wave_bridge_unavailable_reports_ok_false() {
        let (addr, _rx) = mock_json_response(
            "503 Service Unavailable",
            r#"{"ok":false,"reason":"pipe_unavailable"}"#,
        )
        .await;
        let mut cfg = test_cfg();
        cfg.bridge = [Some(format!("http://{addr}/exec")), None];
        let app = make_app(cfg).await;
        let (s, v) = call(&app, "POST", "/wave", Some(json!({"world": "A", "n": 3}))).await;
        assert_eq!(s, StatusCode::OK);
        assert_eq!(v["ok"], false);
        assert_eq!(v["status"], 503);
        assert!(v["error"].as_str().unwrap().contains("503"));
        assert_eq!(v["exec_result"]["ok"], false);
        assert_eq!(v["exec_result"]["reason"], "pipe_unavailable");
    }

    /// Bridge antwortet HTTP 200, aber `exec_result.ok=false` (z. B. Mod-Timeout
    /// `no_response`): `ok` bleibt Zustell-Erfolg (`true`), `exec_ok` ist
    /// `false` — genau der Fall, in dem Doku/`ok`-Semantik auseinanderliefen
    /// (Review #271, Finding 2/3).
    #[tokio::test]
    async fn wave_delivery_ok_with_exec_result_failure() {
        let (addr, _rx) =
            mock_json_response("200 OK", r#"{"ok":false,"reason":"no_response"}"#).await;
        let mut cfg = test_cfg();
        cfg.bridge = [Some(format!("http://{addr}/exec")), None];
        let app = make_app(cfg).await;
        let (s, v) = call(&app, "POST", "/wave", Some(json!({"world": "A", "n": 3}))).await;
        assert_eq!(s, StatusCode::OK);
        // Zustell-Erfolg (HTTP 200) — nicht der Ausfuehr-Erfolg.
        assert_eq!(v["ok"], true);
        assert_eq!(v["exec_ok"], false);
        assert_eq!(v["status"], 200);
        assert_eq!(v["exec_result"]["ok"], false);
        assert_eq!(v["exec_result"]["reason"], "no_response");
    }

    /// `results[]` ohne `ok`-Flag bzw. gemischte Ergebnisse — gleiche Ableitung
    /// wie die UI (`alle results[].ok`).
    #[tokio::test]
    async fn wave_exec_ok_from_results_array() {
        let (addr, _rx) = mock_json_response(
            "200 OK",
            r#"{"results":[{"command":"rb_wave 3","ok":true},{"command":"rb_wave 3","ok":false}]}"#,
        )
        .await;
        let mut cfg = test_cfg();
        cfg.bridge = [Some(format!("http://{addr}/exec")), None];
        let app = make_app(cfg).await;
        let (s, v) = call(&app, "POST", "/wave", Some(json!({"n": 3}))).await;
        assert_eq!(s, StatusCode::OK);
        assert_eq!(v["ok"], true);
        assert_eq!(v["exec_ok"], false);
    }

    /// Capture-Mock-HTTP-Endpoint (#267): nimmt jede Verbindung an und sammelt
    /// die Request-Bytes in `Arc<Mutex<Vec<String>>>` — so lässt sich prüfen,
    /// dass genau EIN Restart-Push rausgeht (und ein Duplikat keinen zweiten).
    async fn capture_endpoint() -> (SocketAddr, Arc<tokio::sync::Mutex<Vec<String>>>) {
        use tokio::io::AsyncReadExt;
        use tokio::io::AsyncWriteExt;
        use tokio::net::TcpListener;
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        let captures: Arc<tokio::sync::Mutex<Vec<String>>> =
            Arc::new(tokio::sync::Mutex::new(Vec::new()));
        let sink = captures.clone();
        tokio::spawn(async move {
            loop {
                let (mut sock, _) = match listener.accept().await {
                    Ok(p) => p,
                    Err(_) => break,
                };
                let mut buf = [0u8; 8192];
                let n = sock.read(&mut buf).await.unwrap_or(0);
                let _ = sock
                    .write_all(
                        b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok",
                    )
                    .await;
                sink.lock()
                    .await
                    .push(String::from_utf8_lossy(&buf[..n]).to_string());
            }
        });
        (addr, captures)
    }

    /// #267: `hq_dead` → Referee `restart` → genau EIN Push an die Bridge;
    /// das gepushte Command wird geackt (B1: **kein** zweiter Zustellweg über
    /// den Poll), ein zweites `hq_dead` ist idempotent (kein zweiter Push).
    #[tokio::test]
    async fn report_hq_dead_pushes_restart_once() {
        let (addr, captures) = capture_endpoint().await;
        let mut cfg = test_cfg();
        cfg.bridge = [Some(format!("http://{addr}/cmd")), None];
        let app = make_app(cfg).await;

        // Referee-Guard: ein HQ kann nur in einem LAUFENDEN Match sterben.
        // Erst `ready` (echte Event-Reihenfolge ready → Wellen → hq_dead);
        // /referee/event pusht nichts, nur /report hq_dead pusht den Restart.
        let (s, _) = call(
            &app,
            "POST",
            "/referee/event",
            Some(json!({"world": "A", "type": "ready"})),
        )
        .await;
        assert_eq!(s, StatusCode::OK);

        let (s, v) = call(
            &app,
            "POST",
            "/report",
            Some(json!({"world": "A", "event": "hq_dead"})),
        )
        .await;
        assert_eq!(s, StatusCode::OK);
        assert_eq!(v["event"], "hq_dead");
        // B2: kein `match_over` (nur Referee-Restart, kein MatchState-FINISHED),
        // stattdessen der reale Referee-Zustand + die Match-Phase.
        assert!(
            v.get("match_over").is_none(),
            "match_over ist irreführend: {v}"
        );
        assert!(v["phase"].is_string());
        assert_eq!(v["restart"], true);
        assert_eq!(v["ignored"], false);
        assert_eq!(v["referee_running"], false);
        assert_eq!(v["restart_pending"], true);
        assert_eq!(v["rounds"], 1);
        assert_eq!(v["acked"], 1);
        assert_eq!(v["broadcast"][0]["ok"], true);
        assert_eq!(v["broadcast"][0]["command"], "restart");
        assert_eq!(v["broadcast"][0]["cmd_id"], 2);

        // Genau EIN Capture mit `command:restart` **inkl. `cmd_id`** (Dedup-Schlüssel).
        tokio::time::sleep(Duration::from_millis(100)).await;
        {
            let caps = captures.lock().await;
            assert_eq!(caps.len(), 1, "caps: {caps:?}");
            assert!(
                caps[0].contains("\"command\":\"restart\""),
                "req: {}",
                caps[0]
            );
            assert!(caps[0].contains("\"cmd_id\":2"), "req: {}", caps[0]);
        }

        // B1-Kern: das gepushte `restart` darf **nicht** erneut über den Poll
        // auftauchen (Push und Poll sind genau EINE Zustellung, nicht zwei).
        let (s, poll) = call(&app, "GET", "/referee/poll?world=A", None).await;
        assert_eq!(s, StatusCode::OK);
        let polled = poll["commands"].as_array().unwrap();
        assert!(
            !polled.iter().any(|c| c["command"] == "restart"),
            "gepushtes restart darf nicht doppelt im Poll liegen: {polled:?}"
        );

        // Duplikat → keine Commands → restart:false, ignored:true, kein Push.
        let (s, v) = call(
            &app,
            "POST",
            "/report",
            Some(json!({"world": "A", "event": "hq_dead"})),
        )
        .await;
        assert_eq!(s, StatusCode::OK);
        assert_eq!(v["restart"], false);
        assert_eq!(v["ignored"], true);
        assert_eq!(v["rounds"], 1);
        // Der Referee hat das Repeat (nach Restart) verworfen.
        assert_eq!(v["restart_pending"], true);
        tokio::time::sleep(Duration::from_millis(150)).await;
        assert_eq!(captures.lock().await.len(), 1, "kein zweiter Push erwartet");
    }

    /// #267/R1: `hq_dead` **ohne** laufendes Match (kein vorheriges `ready`)
    /// wird vom Referee-Guard verworfen — `ignored:true`, aber **kein**
    /// `restart_pending` (unterscheidbar vom Repeat nach einem Restart).
    #[tokio::test]
    async fn report_hq_dead_is_ignored_without_running_match() {
        let app = make_app(test_cfg()).await;
        let (s, v) = call(
            &app,
            "POST",
            "/report",
            Some(json!({"world": "A", "event": "hq_dead"})),
        )
        .await;
        assert_eq!(s, StatusCode::OK);
        assert_eq!(v["restart"], false);
        assert_eq!(v["ignored"], true);
        assert_eq!(v["rounds"], 0);
        assert_eq!(v["referee_running"], false);
        assert_eq!(v["restart_pending"], false);
    }

    /// #267/B1: ohne konfigurierte Bridge → 200 + `ok:null`, kein Panic; das
    /// Command bleibt in der Outbox und ist über den Poll abholbar (Fallback).
    #[tokio::test]
    async fn report_hq_dead_without_bridge_is_ok() {
        let app = make_app(test_cfg()).await; // bridge = [None, None]
        let (s, _) = call(
            &app,
            "POST",
            "/referee/event",
            Some(json!({"world": "A", "type": "ready"})),
        )
        .await;
        assert_eq!(s, StatusCode::OK);

        let (s, v) = call(
            &app,
            "POST",
            "/report",
            Some(json!({"world": "A", "event": "hq_dead"})),
        )
        .await;
        assert_eq!(s, StatusCode::OK);
        assert_eq!(v["restart"], true);
        assert_eq!(v["acked"], 0);
        assert_eq!(v["broadcast"][0]["ok"], Value::Null);
        assert!(v["broadcast"][0]["note"].is_string());

        // Kein Push → Command muss im Poll liegen (sonst ginge der Restart verloren).
        let (_, poll) = call(&app, "GET", "/referee/poll?world=A", None).await;
        let polled = poll["commands"].as_array().unwrap();
        assert!(
            polled.iter().any(|c| c["command"] == "restart"),
            "Restart muss ohne Bridge per Poll zustellbar sein: {polled:?}"
        );
    }

    /// #267/B1: schlägt der Push fehl (Endpoint down), bleibt das Command in der
    /// Outbox → der Poll stellt es zu. Kein stiller Verlust, kein Doppel.
    #[tokio::test]
    async fn report_hq_dead_push_failure_keeps_outbox_for_poll() {
        let mut cfg = test_cfg();
        // Port 1 ist praktisch immer zu (Connection refused) → Push-Fehler.
        cfg.bridge = [Some("http://127.0.0.1:1/exec".to_string()), None];
        let app = make_app(cfg).await;

        call(
            &app,
            "POST",
            "/referee/event",
            Some(json!({"world": "A", "type": "ready"})),
        )
        .await;
        let (s, v) = call(
            &app,
            "POST",
            "/report",
            Some(json!({"world": "A", "event": "hq_dead"})),
        )
        .await;
        assert_eq!(s, StatusCode::OK);
        assert_eq!(v["restart"], true);
        assert_eq!(v["acked"], 0, "fehlgeschlagener Push darf nicht acken");
        assert_ne!(v["broadcast"][0]["ok"], true);

        let (_, poll) = call(&app, "GET", "/referee/poll?world=A", None).await;
        let polled = poll["commands"].as_array().unwrap();
        assert!(
            polled.iter().any(|c| c["command"] == "restart"),
            "nach Push-Fehler muss der Poll den Restart liefern: {polled:?}"
        );
    }

    /// #267: die Aliase `hq_destroy`/`hq_destroyed` verhalten sich exakt wie
    /// `hq_dead` — der erste Treffer pusht genau EIN `restart`, der zweite
    /// Alias ist idempotent (kein zweiter Push).
    #[tokio::test]
    async fn report_hq_dead_aliases_are_accepted_and_idempotent() {
        let (addr, captures) = capture_endpoint().await;
        let mut cfg = test_cfg();
        cfg.bridge = [Some(format!("http://{addr}/cmd")), None];
        let app = make_app(cfg).await;

        let (s, _) = call(
            &app,
            "POST",
            "/referee/event",
            Some(json!({"world": "A", "type": "ready"})),
        )
        .await;
        assert_eq!(s, StatusCode::OK);

        // Alias 1: `hq_destroy` → restart, genau EIN Push.
        let (s, v) = call(
            &app,
            "POST",
            "/report",
            Some(json!({"world": "A", "event": "hq_destroy"})),
        )
        .await;
        assert_eq!(s, StatusCode::OK);
        assert_eq!(v["event"], "hq_dead");
        assert_eq!(v["restart"], true);
        assert_eq!(v["ignored"], false);
        assert_eq!(v["broadcast"][0]["ok"], true);

        // Alias 2: `hq_destroyed` → kein zweiter Restart/Push.
        let (s, v) = call(
            &app,
            "POST",
            "/report",
            Some(json!({"world": "A", "event": "hq_destroyed"})),
        )
        .await;
        assert_eq!(s, StatusCode::OK);
        assert_eq!(v["restart"], false);
        assert_eq!(v["ignored"], true);

        tokio::time::sleep(Duration::from_millis(100)).await;
        assert_eq!(
            captures.lock().await.len(),
            1,
            "genau ein Push über die Aliase"
        );
    }

    #[tokio::test]
    async fn health_endpoint() {
        let app = make_app(test_cfg()).await;
        let (s, v) = call(&app, "GET", "/health", None).await;
        assert_eq!(s, StatusCode::OK);
        assert_eq!(v["ok"], true);
        assert_eq!(v["phase"], "lobby");
    }

    #[tokio::test]
    async fn sp_endpoint_starts_sp_match_and_mirrors() {
        let app = make_app(test_cfg()).await;
        let (s, v) = call(&app, "POST", "/sp", Some(json!({"player": "momo"}))).await;
        assert_eq!(s, StatusCode::OK);
        assert_eq!(v["started"], true);
        assert_eq!(v["phase"], "running");
        assert_eq!(v["round"], 1);
        assert_eq!(v["mode"], "sp");
        assert_eq!(v["teams"]["A"]["player"], "momo");
        assert_eq!(v["teams"]["B"]["player"], "MIRROR");

        // Send von P1 (A) → wird zu MIRROR (B) geroutet UND zurückgespiegelt zu A.
        let (s, v) = call(
            &app,
            "POST",
            "/send",
            Some(json!({"world": "A", "units": [{"unit": "creeper", "count": 4}], "value": 400})),
        )
        .await;
        assert_eq!(s, StatusCode::OK);
        assert_eq!(v["queued_for"], "B");
        let (_, state) = call(&app, "GET", "/state", None).await;
        assert_eq!(
            state["teams"]["A"]["pending_sends"]
                .as_array()
                .unwrap()
                .len(),
            1
        );
        assert_eq!(
            state["teams"]["B"]["pending_sends"]
                .as_array()
                .unwrap()
                .len(),
            1
        );
        assert_eq!(state["teams"]["A"]["pending_sends"][0]["from"], "B"); // Spiegel

        // Wellenstart von P1 (A) lockt beide Seiten + spiegelt Built-Value.
        let (s, v) = call(
            &app,
            "POST",
            "/report",
            Some(json!({"world": "A", "event": "wave_start", "built_value": 8000})),
        )
        .await;
        assert_eq!(s, StatusCode::OK);
        assert_eq!(v["round"], 2);
        assert_eq!(v["rounds_done"], 1);
        let (_, state) = call(&app, "GET", "/state", None).await;
        assert_eq!(
            state["reveal"]["incoming"]["A"].as_array().unwrap().len(),
            1
        );
        assert_eq!(
            state["reveal"]["incoming"]["B"].as_array().unwrap().len(),
            1
        );
        assert_eq!(state["reveal"]["built"]["A"], 8000);
        assert_eq!(state["reveal"]["built"]["B"], 8000);

        // Match-Ende via HQ-HP 0 → match_end im Feed, HQ beider Seiten 0.
        let (s, v) = call(
            &app,
            "POST",
            "/report",
            Some(json!({"world": "A", "event": "hq_hp", "hp": 0})),
        )
        .await;
        assert_eq!(s, StatusCode::OK);
        assert_eq!(v["match_over"], true);
        let (_, state) = call(&app, "GET", "/state", None).await;
        let feed = state["feed"].as_array().unwrap();
        assert!(feed.iter().any(|e| e["kind"] == "match_end"));
        assert_eq!(state["teams"]["A"]["hq_hp"], 0.0);
        assert_eq!(state["teams"]["B"]["hq_hp"], 0.0);
    }

    #[tokio::test]
    async fn events_endpoint_supports_cursor() {
        let app = make_app(test_cfg()).await;
        register(&app, "A", "momo").await;
        register(&app, "B", "matheo").await;
        call(&app, "POST", "/go", Some(json!({}))).await;

        // Ohne Cursor: alle bislang vergebenen Events.
        let (s, v) = call(&app, "GET", "/events", None).await;
        assert_eq!(s, StatusCode::OK);
        let all = v["events"].as_array().unwrap().clone();
        assert!(!all.is_empty());
        let last_seq = v["last_seq"].as_u64().unwrap();
        assert_eq!(all.last().unwrap()["seq"].as_u64().unwrap(), last_seq);

        // Cursor = höchste Sequenz → keine neuen Events.
        let (_, v2) = call(&app, "GET", &format!("/events?since={last_seq}"), None).await;
        assert_eq!(v2["events"].as_array().unwrap().len(), 0);
        assert_eq!(v2["last_seq"].as_u64().unwrap(), last_seq);

        // Cursor mitten drin → nur spätere Events.
        let first_seq = all[0]["seq"].as_u64().unwrap();
        let (_, v3) = call(&app, "GET", &format!("/events?since={first_seq}"), None).await;
        assert_eq!(v3["events"].as_array().unwrap().len(), all.len() - 1);
        assert_eq!(v3["last_seq"].as_u64().unwrap(), last_seq);
    }

    #[test]
    fn go_payload_carries_ordered_unpause_commands() {
        let p = go_payload("rift-1", 1, &["debug_dom_resume".to_string()]);
        assert_eq!(p["cmd"], "go");
        assert_eq!(p["match_id"], "rift-1");
        assert_eq!(p["round"], 1);
        assert_eq!(p["commands"], json!(["debug_dom_resume"]));

        // Mehrere Kommandos bleiben in Reihenfolge (DOM zuerst, native Server-Pause danach).
        let p2 = go_payload(
            "rift-1",
            1,
            &["debug_dom_resume".to_string(), "resume_game".to_string()],
        );
        assert_eq!(p2["commands"], json!(["debug_dom_resume", "resume_game"]));
    }

    #[tokio::test]
    async fn referee_event_in_command_out_over_http() {
        let app = make_app(test_cfg()).await;

        // ready → rb_wave 1 für Welt A (Event-In → Command-Out).
        let (s, v) = call(
            &app,
            "POST",
            "/referee/event",
            Some(json!({"world": "A", "type": "ready"})),
        )
        .await;
        assert_eq!(s, StatusCode::OK);
        assert_eq!(v["accepted"], true);
        assert_eq!(v["commands"][0]["command"], "rb_wave 1");
        assert_eq!(v["commands"][0]["world"], "A");
        assert_eq!(v["state"]["running"], true);
        assert_eq!(v["state"]["waves_in_flight"], 1);

        // wave_done level=1 → rb_wave 2.
        let (s, v) = call(
            &app,
            "POST",
            "/referee/event",
            Some(json!({"world": "A", "type": "wave_done", "level": 1})),
        )
        .await;
        assert_eq!(s, StatusCode::OK);
        assert_eq!(v["commands"][0]["command"], "rb_wave 2");

        // Doppeltes wave_done level=1 → keine neue Welle (idempotent).
        let (s, v) = call(
            &app,
            "POST",
            "/referee/event",
            Some(json!({"world": "A", "type": "wave_done", "level": 1})),
        )
        .await;
        assert_eq!(s, StatusCode::OK);
        assert_eq!(v["commands"].as_array().unwrap().len(), 0);

        // hq_destroyed → restart, Runde 1.
        let (s, v) = call(
            &app,
            "POST",
            "/referee/event",
            Some(json!({"world": "A", "type": "hq_destroyed"})),
        )
        .await;
        assert_eq!(s, StatusCode::OK);
        assert_eq!(v["commands"][0]["command"], "restart");
        assert_eq!(v["state"]["restart_pending"], true);
        assert_eq!(v["state"]["rounds"], 1);

        // Nach dem Neustart: ready → wieder rb_wave 1.
        let (s, v) = call(
            &app,
            "POST",
            "/referee/event",
            Some(json!({"world": "A", "type": "ready"})),
        )
        .await;
        assert_eq!(s, StatusCode::OK);
        assert_eq!(v["commands"][0]["command"], "rb_wave 1");
    }

    #[tokio::test]
    async fn referee_event_validates_and_polls_outbox() {
        let app = make_app(test_cfg()).await;

        // Unbekannte Welt → 400.
        let (s, v) = call(
            &app,
            "POST",
            "/referee/event",
            Some(json!({"world": "C", "type": "ready"})),
        )
        .await;
        assert_eq!(s, StatusCode::BAD_REQUEST);
        assert_eq!(err_type(&v), "invalid");

        // Unbekannter Typ → 400 (serde lehnt den Enum-Wert ab).
        let (s, _) = call(
            &app,
            "POST",
            "/referee/event",
            Some(json!({"world": "A", "type": "bogus"})),
        )
        .await;
        assert_eq!(s, StatusCode::UNPROCESSABLE_ENTITY);

        // wave_done ohne level → 400.
        let (s, v) = call(
            &app,
            "POST",
            "/referee/event",
            Some(json!({"world": "A", "type": "wave_done"})),
        )
        .await;
        assert_eq!(s, StatusCode::BAD_REQUEST);
        assert_eq!(err_type(&v), "invalid");

        // ready → Command in der Outbox; Poll liefert und leert sie.
        call(
            &app,
            "POST",
            "/referee/event",
            Some(json!({"world": "B", "type": "ready"})),
        )
        .await;
        let (s, v) = call(&app, "GET", "/referee/poll?world=B", None).await;
        assert_eq!(s, StatusCode::OK);
        assert_eq!(v["commands"].as_array().unwrap().len(), 1);
        assert_eq!(v["commands"][0]["command"], "rb_wave 1");
        let (_, v) = call(&app, "GET", "/referee/poll?world=B", None).await;
        assert_eq!(v["commands"].as_array().unwrap().len(), 0);
    }
}
