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
//! (vorwärtskompatibel, wie im Protokoll des Servers üblich).

use crate::broadcast;
use crate::records::RecordStore;
use crate::referee::{Command, GameEvent, GameEventKind, Referee, RefereeConfig};
use crate::state::{
    MatchState, PauseEffect, Phase, ReadyEffect, ResumeEffect, SendBatch, StateError, World,
};
use axum::extract::{Path, Query, Request, State as AxumState};
use axum::http::{header, HeaderValue, StatusCode};
use axum::middleware::{self, Next};
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
    /// Timeout je Broadcast-Endpoint.
    pub go_timeout: Duration,
    /// Verzögerung (`delay_s`) für Ingress-Pushes an die Ziel-Bridge beim
    /// Wellenstart (US4, #996); Default aus `TOURNAMENT_INCOMING_DELAY_S`.
    pub incoming_delay_s: f64,
    /// Start-HP jedes HQ.
    pub hq_hp_start: f64,
    /// Referee: Wellen-Deckel (0 = unbegrenzt) und Restart-Command (Issue #268).
    pub referee_max_wave: u32,
    pub referee_restart_cmd: String,
    /// Verzeichnis der statischen Web-UI.
    pub web_dir: PathBuf,
    /// Deploy-Identitaet (Issue #483, US4): Umgebung (dev|prod|test|staging) + Ref
    /// (Checkout-SHA bzw. Tag), sichtbar in `GET /health`.
    pub env: String,
    pub deploy_ref: String,
    /// Pfad der SQLite-Datenbank fuer Match-Records (#999); Default
    /// `./data/rbbattle.db` (`TOURNAMENT_DB_PATH`).
    pub db_path: PathBuf,
    /// Bearer-Token fuer MUTIERENDE Routen (Issue #298). Quelle ist der Vault
    /// (`vault_tournament_token` → `TOURNAMENT_TOKEN`), read-once aus Env im
    /// `main`. Leer = **fail-closed**: mutierende Routen antworten 401.
    pub token: String,
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
    /// Persistenter Match-Record-Store (#999).
    pub store: Arc<RecordStore>,
    pub cfg: Arc<Config>,
}

impl AppState {
    pub fn new(cfg: Config, store: Arc<RecordStore>) -> Self {
        let referee = Referee::new(RefereeConfig {
            max_wave: cfg.referee_max_wave,
            restart_cmd: cfg.referee_restart_cmd.clone(),
        });
        AppState {
            state: Arc::new(RwLock::new(MatchState::new(cfg.hq_hp_start))),
            referee: Arc::new(RwLock::new(referee)),
            store,
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
    /// Optionale Queue-`match_id` (Issue #1028). Fehlt sie, wird nichts gesetzt;
    /// ein falscher Typ wird von axum als `422` abgewiesen.
    #[serde(default)]
    match_id: Option<i64>,
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

/// `POST /pause` + `POST /resume` (Match-weiter DOM-Freeze-Fan-out, #997).
#[derive(Debug, Deserialize, Default)]
struct PauseReq {
    /// Laufendes Match, bereits pausiert/frei: Fan-out trotzdem erneut senden.
    #[serde(default)]
    retry: bool,
}

#[derive(Debug, Deserialize)]
struct SendReq {
    world: String,
    /// Unit-basierter Send (SP/direkt). Leer/missing für wellen-basierte Sends.
    #[serde(default)]
    units: Vec<UnitReq>,
    #[serde(default)]
    value: u64,
    /// Wellen-basierter Cross-World-Send (US2, #996): Difficulty-Level ≥ 1.
    /// Gesetzt → Level-Send (`route_wave_send`); sonst unit-basiert.
    #[serde(default)]
    level: Option<u32>,
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

/// Mutierende Routen (Issue #298): verlangen `Authorization: Bearer <TOKEN>`.
///
/// Die Liste ist der Vertrag mit Caddy (`rift-caddy.Caddyfile.j2`, `@write`) —
/// beide Stellen muessen dieselben Pfade fuehren (siehe `docs/TOURNAMENT_API.md`).
#[allow(dead_code)]
pub const WRITE_ROUTES: &[&str] = &[
    "/lobby",
    "/ready",
    "/go",
    "/pause",
    "/resume",
    "/send",
    "/report",
    "/rematch",
    "/sp",
    "/wave",
    "/referee/event",
];

pub fn router(app: AppState) -> Router {
    let web = app.cfg.web_dir.clone();
    // Mutierende Routen in EINEM Sub-Router mit Bearer-Layer; Leserouten +
    // ServeDir-Web-UI bleiben frei. `route_layer` greift nur auf gematchte
    // Routen dieses Sub-Routers (nicht auf den Fallback des Public-Routers).
    let protected = Router::new()
        .route("/lobby", post(lobby))
        .route("/ready", post(ready))
        .route("/go", post(go))
        .route("/pause", post(pause))
        .route("/resume", post(resume))
        .route("/send", post(send))
        .route("/report", post(report))
        .route("/rematch", post(rematch))
        .route("/sp", post(sp))
        .route("/wave", post(wave))
        .route("/referee/event", post(referee_event))
        .route_layer(middleware::from_fn_with_state(app.clone(), require_bearer));

    let public = Router::new()
        .route("/referee/poll", get(referee_poll))
        .route("/state", get(state_get))
        .route("/matches/{id}", get(match_get))
        .route("/events", get(events))
        .route("/health", get(health))
        .fallback_service(tower_http::services::ServeDir::new(web));

    protected.merge(public).with_state(app)
}

/// Bearer-Middleware fuer mutierende Routen (Issue #298).
///
/// Fail-closed: ist `config.token` leer, wird JEDER mutierende Request mit 401
/// abgewiesen (nie „offen"). Sonst muss `Authorization: Bearer <token>` exakt
/// passen. Die Antwort traegt `WWW-Authenticate: Bearer`.
async fn require_bearer(AxumState(app): AxumState<AppState>, req: Request, next: Next) -> Response {
    let token = app.cfg.token.as_str();
    let expected = format!("Bearer {token}");
    let provided = req
        .headers()
        .get(header::AUTHORIZATION)
        .and_then(|v| v.to_str().ok());
    let authorized = !token.is_empty() && provided == Some(expected.as_str());
    if !authorized {
        let mut resp = Json(json!({
            "error": "unauthorized",
            "type": "unauthorized",
        }))
        .into_response();
        *resp.status_mut() = StatusCode::UNAUTHORIZED;
        resp.headers_mut()
            .insert(header::WWW_AUTHENTICATE, HeaderValue::from_static("Bearer"));
        return resp;
    }
    next.run(req).await
}

// ---- Handler ----

/// POST /lobby
async fn lobby(
    AxumState(app): AxumState<AppState>,
    Json(req): Json<LobbyReq>,
) -> ApiResult<Json<Value>> {
    let world = parse_world(&req.world)?;
    let effect = app
        .with_state(|s| match req.match_id {
            Some(id) => s.lobby_register_with_match_id(world, &req.player, Some(id)),
            None => s.lobby_register(world, &req.player),
        })
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
        spawn_go_broadcast(&app);
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
    let (started, _round) = {
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
    let broadcast = broadcast_go(&app).await;
    let view = app.state.read().await.view();
    Ok(Json(json!({
        "started": started,
        "phase": view.phase,
        "round": view.round,
        "broadcast": broadcast,
    })))
}

/// POST /pause — Match-weiter Pause-Fan-out (DOM-Freeze, #997).
///
/// Guard: nur `Phase::Running` (sonst 409). Der State setzt `paused=true`;
/// danach fächert [`broadcast_dom`] `POST /pause_dom` an **beide** Bridges
/// (`cfg.bridge_for(w)`, analog `broadcast_go`). Partial-Fehler einer Welt
/// liefern HTTP **200** mit `broadcast.<W>.ok:false` — kein 5xx.
///
/// `already:true` bei bereits pausiertem Match (kein zweiter Fan-out); mit
/// `{"retry":true}` wird trotzdem erneut gefächert.
async fn pause(
    AxumState(app): AxumState<AppState>,
    Json(req): Json<PauseReq>,
) -> ApiResult<Json<Value>> {
    let already = {
        let mut guard = app.state.write().await;
        guard.pause().map_err(ApiError::from)? == PauseEffect::AlreadyPaused
    };
    let broadcast = if !already || req.retry {
        broadcast_dom(&app, "pause_dom").await
    } else {
        stored_pause_broadcast(&app).await
    };
    let view = app.state.read().await.view();
    Ok(Json(json!({
        "paused": view.paused,
        "phase": view.phase,
        "already": already,
        "broadcast": broadcast,
    })))
}

/// POST /resume — Match-weiter Resume-Fan-out (DOM-Freeze aufheben, #997).
///
/// Guard: nur `Phase::Running` (sonst 409). `already:true`, wenn das Match gar
/// nicht pausiert war (kein Doppel-Feed); mit `{"retry":true}` wird trotzdem
/// erneut `POST /resume_dom` an beide Bridges gefächert.
async fn resume(
    AxumState(app): AxumState<AppState>,
    Json(req): Json<PauseReq>,
) -> ApiResult<Json<Value>> {
    let already = {
        let mut guard = app.state.write().await;
        guard.resume().map_err(ApiError::from)? == ResumeEffect::AlreadyRunning
    };
    let broadcast = if !already || req.retry {
        broadcast_dom(&app, "resume_dom").await
    } else {
        stored_pause_broadcast(&app).await
    };
    let view = app.state.read().await.view();
    Ok(Json(json!({
        "paused": view.paused,
        "phase": view.phase,
        "already": already,
        "broadcast": broadcast,
    })))
}

/// POST /send — Wave-Routing: Send von Welt X landet in der Queue der Gegner-Welt.
///
/// Zwei Formen (additiv, #996):
///   * unit-basiert (`{"world":"A","units":[…],"value":…}`) — SP/direkt;
///   * wellen-basiert (`{"world":"A","level":3,"value":…}`) — Cross-World
///     aus dem Attack-Cycle (US2/US5). Der Level-Send landet ebenfalls in der
///     Queue der Gegner-Welt und wird bei deren nächstem `wave_start` in den
///     Reveal übernommen und als Ingress gepusht (US4).
async fn send(
    AxumState(app): AxumState<AppState>,
    Json(req): Json<SendReq>,
) -> ApiResult<Json<Value>> {
    let world = parse_world(&req.world)?;
    let batch = match req.level {
        Some(level) => {
            app.with_state(|s| s.route_wave_send(world, level, req.value))
                .await?
        }
        None => {
            let units: Vec<crate::state::UnitSpec> = req
                .units
                .into_iter()
                .map(|u| crate::state::UnitSpec {
                    unit: u.unit,
                    count: u.count,
                })
                .collect();
            app.with_state(|s| s.route_send(world, units, req.value))
                .await?
        }
    };
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
        "level": batch.level,
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
            // Ingress-Push (US4): nur beim echten Lock dieser Welt — ein
            // Duplikat/Retry (Reveal bereits gesetzt) pusht NICHT erneut.
            let ingress = match effect {
                crate::state::WaveEffect::Locked => {
                    let batches: Vec<SendBatch> = {
                        let guard = app.state.read().await;
                        guard
                            .reveal
                            .as_ref()
                            .and_then(|r| r.incoming.get(&world))
                            .cloned()
                            .unwrap_or_default()
                    };
                    push_incoming_sends(&app, world, &batches).await
                }
                crate::state::WaveEffect::Duplicate => Vec::new(),
            };
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
                "ingress": ingress,
            })))
        }
        "hq_hp" => {
            let hp = req
                .hp
                .ok_or_else(|| StateError::new("invalid", "event 'hq_hp' benötigt Feld 'hp'"))?;
            let (effect, record) = app
                .with_state(|s| {
                    let effect = s.report_hq(world, hp)?;
                    // #999: Match-Ende → pure Record-Ableitung (idempotent).
                    let record = if effect.match_over {
                        s.to_record(crate::records::now_ms())
                    } else {
                        None
                    };
                    Ok((effect, record))
                })
                .await?;
            // Fehler beim Persistieren NUR loggen — die HTTP-Antwort bleibt gleich.
            if let Some(rec) = &record {
                if let Err(e) = app.store.record(rec) {
                    tracing::error!(
                        "Match-Record {}/{} konnte nicht geschrieben werden: {e}",
                        rec.match_id,
                        rec.rematch
                    );
                }
            }
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

/// Basis eines konfigurierten Bridge-HTTP-Adapters: ein abschließendes `/exec`
/// wird entfernt (Prod/local setzen `RBBRIDGE_*_URL` historisch auf `.../exec`),
/// ebenso ein abschließender Slash. EINE Quelle für alle abgeleiteten
/// Bridge-Routen (#997/#996/#1027).
fn bridge_base_url(bridge: &str) -> String {
    let trimmed = bridge.trim_end_matches('/');
    let base = trimmed.strip_suffix("/exec").unwrap_or(trimmed);
    base.trim_end_matches('/').to_string()
}

/// Leitet die Aktions-Route eines konfigurierten Bridge-Endpoints ab:
/// `bridge_base_url` + `/{action}`.
fn bridge_action_url(bridge: &str, action: &str) -> String {
    format!("{}/{action}", bridge_base_url(bridge))
}

/// Leitet die Ingress-URL (`POST /incoming_send`) eines konfigurierten
/// Bridge-Endpoints ab (US4, #996): ein abschließendes `/exec` wird entfernt
/// und `/incoming_send` angehängt.
fn ingress_url(bridge: &str) -> String {
    bridge_action_url(bridge, "incoming_send")
}

/// Leitet die Pause-/Resume-Route eines konfigurierten Bridge-Endpoints ab
/// (#997): ein abschließendes `/exec` wird entfernt und die Aktion
/// (`pause_dom`/`resume_dom`) angehängt.
fn dom_action_url(bridge: &str, action: &str) -> String {
    bridge_action_url(bridge, action)
}

/// Pusht die beim Wellenstart einer Welt gedrainten (level-basierten) Sends als
/// Ingress an die Bridge der Zielwelt (US4, #996 — der G5-Vertrag).
///
/// Je Batch mit `level` genau EIN `POST <bridge_for(world)>/incoming_send` mit
/// Body `{level, from, delay_s}`. Batchs **ohne** `level` (Alt-unit-Sends)
/// werden defensiv übersprungen (nur im Reveal geführt). Ohne konfigurierten
/// Endpoint wird je Batch `ok: null` vermerkt — kein Panic, kein `unwrap`
/// (Muster `push_referee_commands`). Da der Drain den Batch aus `pending`
/// entfernt und `Duplicate` nicht erneut pusht, wird jeder Batch genau EINMAL
/// zugestellt.
///
/// Die Delay-Zeit stammt aus `cfg.incoming_delay_s` (`delay_s` im
/// `incoming_wave`-Event).
///
/// Rückgabe: ein `ingress`-Block (je gepushtem Batch
/// `{level, from, ok, http_status, error, endpoint}` bzw. `ok:null` + `note`).
async fn push_incoming_sends(app: &AppState, world: World, batches: &[SendBatch]) -> Vec<Value> {
    let mut results = Vec::new();
    for batch in batches {
        let Some(level) = batch.level else {
            continue; // Alt-unit-Send: kein Ingress, nur Reveal.
        };
        let from = batch.from.as_str();
        match app.cfg.bridge_for(world) {
            Some(url) => {
                let endpoint = ingress_url(url);
                let payload = json!({
                    "level": level,
                    "from": from,
                    "delay_s": app.cfg.incoming_delay_s,
                });
                let res = broadcast::post_json(&endpoint, &payload, app.cfg.go_timeout).await;
                results.push(json!({
                    "level": level,
                    "from": from,
                    "ok": res.ok(),
                    "http_status": res.status,
                    "error": res.error,
                    "endpoint": endpoint,
                }));
            }
            None => results.push(json!({
                "level": level,
                "from": from,
                "ok": Value::Null,
                "note": "kein Endpoint konfiguriert (RBBRIDGE_<W>_URL) — kein Ingress-Push",
            })),
        }
    }
    results
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
    if !(1..=MAX_WAVE_N).contains(&n) {
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

#[derive(Debug, Deserialize, Default)]
struct RematchQuery {
    /// Match-Instanz (Rematch-Zähler); Default 0 = das erste Match.
    #[serde(default)]
    rematch: Option<u32>,
}

/// GET /matches/{id} — persistierter Match-Record (#999, US3, additativ).
///
/// `?rematch=<n>` wählt die Match-Instanz (Default 0). Unbekannt → 404
/// `not_found`.
async fn match_get(
    AxumState(app): AxumState<AppState>,
    Path(id): Path<String>,
    Query(q): Query<RematchQuery>,
) -> ApiResult<Json<Value>> {
    let rematch = q.rematch.unwrap_or(0);
    match app.store.get(&id, rematch) {
        Ok(Some(rec)) => Ok(Json(serde_json::to_value(rec).unwrap_or(Value::Null))),
        Ok(None) => Err(StateError::new(
            "not_found",
            format!("kein Match-Record für match_id '{id}' (rematch {rematch})"),
        )
        .into()),
        Err(e) => {
            tracing::error!("Match-Record {id}/{rematch} lesen fehlgeschlagen: {e}");
            Err(StateError::new("not_found", "Match-Record nicht lesbar").into())
        }
    }
}

/// GET /health
async fn health(AxumState(app): AxumState<AppState>) -> ApiResult<Json<Value>> {
    let phase = app.state.read().await.phase.as_str().to_string();
    Ok(Json(json!({
        "ok": true,
        "phase": phase,
        // Deploy-Identitaet (Issue #483, US4): dieselbe <env> · <ref> wie
        // Landing, Server-Control und die Container-Labels.
        "env": app.cfg.env,
        "ref": app.cfg.deploy_ref,
    })))
}

// ---- GO-Broadcast ----

/// Verifizierte Bridge-Routen des GO-Fan-outs (#1027), in Ausführungs-
/// reihenfolge: erst die native Server-Pause einer kalt gebooteten Welt
/// aufheben (`/resume_game`, Issue #880), dann den Wellen-Zyklus armieren
/// (`/start` setzt `start_epoch`; der attack_cycle vollzieht PAUSED→WARMUP,
/// s. `deploy/attack-cycle/attack_cycle.py`). Beide Routen nehmen keinen Body.
const GO_ROUTES: [&str; 2] = ["resume_game", "start"];

/// GO-Fan-out an beide rbbridge-Endpoints (Push; Poll auf /state ist Fallback).
///
/// Je Welt wird **für jede** Route aus [`GO_ROUTES`] `POST {base}/{route}` mit
/// leerem JSON-Body `{}` gesendet (Muster [`broadcast_dom`]; die Bridge-Routen
/// nehmen keinen Body). `base` = `bridge_for(w)` ohne abschließendes `/exec`
/// ([`bridge_action_url`]). Erfasst wird je Route `{route, ok, status, error,
/// endpoint}`; der Welt-Block aggregiert `ok` (alle Routen ok), `endpoint`
/// (Basis) und `routes`. Der `/state`-Status (`go_broadcast`) wird je Welt
/// aggregiert fortgeschrieben. Partial-Fehler sind **kein** Handler-Fehler
/// (die andere Welt/Route wird trotzdem gepusht, kein 5xx/kein Panic).
async fn broadcast_go(app: &AppState) -> Value {
    let timeout = app.cfg.go_timeout;
    let payload = json!({});

    let mut results = serde_json::Map::new();
    for w in World::ALL {
        match app.cfg.bridge_for(w) {
            Some(url) => {
                let base = bridge_base_url(url);
                let mut routes = Vec::with_capacity(GO_ROUTES.len());
                let mut all_ok = true;
                let mut first_error: Option<String> = None;
                for route in GO_ROUTES {
                    let endpoint = bridge_action_url(url, route);
                    let res = broadcast::post_json(&endpoint, &payload, timeout).await;
                    let ok = res.ok();
                    if !ok && first_error.is_none() {
                        first_error = res.error.clone();
                    }
                    all_ok &= ok;
                    routes.push(json!({
                        "route": route,
                        "ok": ok,
                        "status": res.status,
                        "error": res.error,
                        "endpoint": endpoint,
                    }));
                }
                app.state.write().await.record_broadcast(
                    w,
                    all_ok,
                    first_error,
                    Some(base.clone()),
                );
                results.insert(
                    w.as_str().to_string(),
                    json!({
                        "ok": all_ok,
                        "endpoint": base,
                        "routes": routes,
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
fn spawn_go_broadcast(app: &AppState) {
    let app = app.clone();
    tokio::spawn(async move {
        let _ = broadcast_go(&app).await;
    });
}

/// Fan-out eines DOM-Aktions-Kommandos (`pause_dom`/`resume_dom`) an **beide**
/// Bridges (#997) — analog [`broadcast_go`], aber ohne Payload (Body `{}`; die
/// Bridge-Route nimmt keinen Body). Je Welt wird der Zustell-Status in
/// `teams.<W>.pause_broadcast` festgehalten (für /state + UI).
///
/// Partial-Fehler einer Welt (Endpoint down / kein Endpoint) sind **kein**
/// Fehler des Handlers: die andere Welt wird trotzdem gepusht, das Ergebnis
/// steht je Welt im `broadcast`-Block (`ok:false` bzw. `ok:null` + `note`).
async fn broadcast_dom(app: &AppState, action: &str) -> Value {
    let timeout = app.cfg.go_timeout;
    let payload = json!({});

    let mut results = serde_json::Map::new();
    for w in World::ALL {
        match app.cfg.bridge_for(w) {
            Some(url) => {
                let endpoint = dom_action_url(url, action);
                let res = broadcast::post_json(&endpoint, &payload, timeout).await;
                app.state.write().await.record_pause_broadcast(
                    w,
                    res.ok(),
                    res.error.clone(),
                    Some(endpoint.clone()),
                );
                results.insert(
                    w.as_str().to_string(),
                    json!({
                        "ok": res.ok(),
                        "status": res.status,
                        "error": res.error,
                        "endpoint": endpoint,
                    }),
                );
            }
            None => {
                results.insert(
                    w.as_str().to_string(),
                    json!({"ok": null, "note": "kein Endpoint konfiguriert (RBBRIDGE_<W>_URL) — kein Pause-Push"}),
                );
            }
        }
    }
    Value::Object(results)
}

/// Gespiegelter `pause_broadcast`-Status je Welt aus `/state` (für den Fall
/// „bereits pausiert, kein Retry" — kein erneuter Push, aber die UI sieht den
/// letzten Zustell-Stand).
async fn stored_pause_broadcast(app: &AppState) -> Value {
    let view = app.state.read().await.view();
    let mut results = serde_json::Map::new();
    for w in World::ALL {
        if let Some(t) = view.teams.get(w.as_str()) {
            results.insert(
                w.as_str().to_string(),
                serde_json::to_value(&t.pause_broadcast).unwrap_or(Value::Null),
            );
        }
    }
    Value::Object(results)
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

    /// Fester Test-Bearer (Issue #298): `call()` setzt ihn automatisch, damit
    /// die Bestandstests unveraendert bleiben; die Auth-Tests senden roh.
    const TEST_TOKEN: &str = "test-secret-token";

    fn test_cfg() -> Config {
        Config {
            host: "127.0.0.1".into(),
            port: 0,
            auto_go: false,
            bridge: [None, None],
            go_timeout: Duration::from_millis(800),
            incoming_delay_s: 5.0,
            hq_hp_start: 100.0,
            referee_max_wave: 0,
            // Explizite Test-Konfiguration (kein `Default`): hier bewusst
            // `rb_reset` wie der produktive Env-Default aus `main.rs` (#281).
            referee_restart_cmd: "rb_reset".to_string(),
            web_dir: PathBuf::from("web"), // wird in Tests nicht gebraucht
            // Deploy-Identitaet (Issue #483, US4): feste Test-Werte, damit der
            // /health-Vertrag deterministisch pruefbar ist.
            env: "test".to_string(),
            deploy_ref: "deadbeef".to_string(),
            db_path: temp_db_path(),
            token: TEST_TOKEN.to_string(),
        }
    }

    /// Eindeutiger Temp-DB-Pfad je Test (isoliert, keine Kollisionen).
    fn temp_db_path() -> PathBuf {
        let n = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        std::env::temp_dir().join(format!("rbbattle-api-test-{n}.db"))
    }

    async fn make_app(cfg: Config) -> Router {
        let store = RecordStore::open(&cfg.db_path).expect("test record store");
        router(AppState::new(cfg, Arc::new(store)))
    }

    /// Authentifizierter Aufruf (Bestandshelfer): setzt den Test-Bearer, damit
    /// mutierende Routen den Layer passieren (Issue #298).
    async fn call(
        app: &Router,
        method: &str,
        uri: &str,
        body: Option<Value>,
    ) -> (StatusCode, Value) {
        request(app, method, uri, body, Some(TEST_TOKEN)).await
    }

    /// Roher Aufruf mit waehlbarem Bearer (`None` = kein Authorization-Header).
    async fn request(
        app: &Router,
        method: &str,
        uri: &str,
        body: Option<Value>,
        auth: Option<&str>,
    ) -> (StatusCode, Value) {
        let mut builder = Request::builder().method(method).uri(uri);
        if let Some(token) = auth {
            builder = builder.header("authorization", format!("Bearer {token}"));
        }
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

    /// US1 (#1028): `POST /lobby {match_id}` wird je Welt gespeichert und als
    /// `teams.<W>.match_id` in `GET /state` ausgegeben; fehlend → `null`.
    #[tokio::test]
    async fn lobby_match_id_roundtrip_and_optional() {
        let app = make_app(test_cfg()).await;
        let (s, _) = call(
            &app,
            "POST",
            "/lobby",
            Some(json!({"player": "momo", "world": "A", "match_id": 7})),
        )
        .await;
        assert_eq!(s, StatusCode::OK);
        // B ohne match_id -> null.
        let (s, _) = register(&app, "B", "matheo").await;
        assert_eq!(s, StatusCode::OK);
        let (s, v) = call(&app, "GET", "/state", None).await;
        assert_eq!(s, StatusCode::OK);
        assert_eq!(v["teams"]["A"]["match_id"], 7);
        assert_eq!(v["teams"]["B"]["match_id"], Value::Null);
        // Zweites /lobby ohne match_id laesst den Wert unveraendert (additiv).
        let (s, _) = register(&app, "A", "momo2").await;
        assert_eq!(s, StatusCode::OK);
        let (_, v) = call(&app, "GET", "/state", None).await;
        assert_eq!(v["teams"]["A"]["match_id"], 7);
        // Namenswechsel setzt nur ready zurueck, nicht die match_id.
        assert_eq!(v["teams"]["A"]["ready"], false);
    }

    /// US1 (#1028): ungueltiger `match_id`-Typ -> 422, kein halber Zustand.
    #[tokio::test]
    async fn lobby_rejects_invalid_match_id_type() {
        let app = make_app(test_cfg()).await;
        let (s, _) = call(
            &app,
            "POST",
            "/lobby",
            Some(json!({"player": "momo", "world": "A", "match_id": "keine-zahl"})),
        )
        .await;
        assert_eq!(s, StatusCode::UNPROCESSABLE_ENTITY);
        let (_, v) = call(&app, "GET", "/state", None).await;
        assert_eq!(v["teams"]["A"]["player"], Value::Null);
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

    /// #1027: Der zweite `/ready` (AUTO_GO) fächert den GO-Fan-out an **beide**
    /// Bridges: je Welt genau zwei POSTs auf den verifizierten Routen
    /// `/resume_game` (Sim entfrieren) → `/start` (Zyklus armieren) — nie
    /// `/exec`, Body leer (`{}`). Ein einzelnes Ready broadcastet nichts; nach
    /// `Running` liefert ein weiteres Ready `409` (kein Doppel-GO).
    #[tokio::test]
    async fn second_ready_broadcasts_go_to_both_bridges() {
        let (addr_a, cap_a) = capture_endpoint().await;
        let (addr_b, cap_b) = capture_endpoint().await;
        let mut cfg = test_cfg();
        cfg.auto_go = true;
        cfg.bridge = [
            Some(format!("http://{addr_a}/exec")),
            Some(format!("http://{addr_b}/exec")),
        ];
        let app = make_app(cfg).await;
        register(&app, "A", "momo").await;
        register(&app, "B", "matheo").await;

        // Ein einzelnes Ready -> kein Broadcast, Phase bleibt Lobby/Ready.
        let (s, v) = call(&app, "POST", "/ready", Some(json!({"world": "A"}))).await;
        assert_eq!(s, StatusCode::OK);
        assert_eq!(v["match_started"], false);
        assert!(
            v["phase"] == "lobby" || v["phase"] == "ready",
            "phase nach einem ready: {}",
            v["phase"]
        );

        // Zweites Ready -> AUTO_GO startet das Match und broadcastet an BEIDE
        // Bridges (Fan-out asynchron).
        let (s, v) = call(&app, "POST", "/ready", Some(json!({"world": "B"}))).await;
        assert_eq!(s, StatusCode::OK);
        assert_eq!(v["match_started"], true);
        assert_eq!(v["phase"], "running");
        assert_eq!(v["round"], 1);

        tokio::time::sleep(Duration::from_millis(200)).await;
        assert_go_fanout(&cap_a, &["/resume_game", "/start"]).await;
        assert_go_fanout(&cap_b, &["/resume_game", "/start"]).await;

        // Idempotenz: erneutes `/ready` nach `Running` -> 409 (kein Doppel-GO).
        let (s, v) = call(&app, "POST", "/ready", Some(json!({"world": "A"}))).await;
        assert_eq!(s, StatusCode::CONFLICT);
        assert_eq!(err_type(&v), "conflict");
    }

    /// #1027: Prüft, dass ein Capture-Endpoint **genau** die GO-Routen in
    /// Reihenfolge gesehen hat (je ein POST, leerer Body, nie `/exec`).
    async fn assert_go_fanout(cap: &Arc<tokio::sync::Mutex<Vec<String>>>, routes: &[&str]) {
        let reqs = cap.lock().await;
        let posts: Vec<&String> = reqs.iter().filter(|r| r.starts_with("POST ")).collect();
        assert_eq!(
            posts.len(),
            routes.len(),
            "genau {} POSTs: {reqs:?}",
            routes.len()
        );
        for (req, route) in posts.iter().zip(routes.iter()) {
            assert!(
                req.starts_with(&format!("POST {route} HTTP/1.1")),
                "req: {req}"
            );
            assert!(!req.starts_with("POST /exec "), "kein /exec: {req}");
            let body = req.split("\r\n\r\n").nth(1).unwrap_or("");
            assert_eq!(body, "{}", "leerer Body: {req}");
        }
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

    /// `/health` traegt die Deploy-Identitaet (Issue #483, US4).
    #[tokio::test]
    async fn health_reports_deploy_identity() {
        let app = make_app(test_cfg()).await;
        let (s, v) = call(&app, "GET", "/health", None).await;
        assert_eq!(s, StatusCode::OK);
        assert_eq!(v["ok"], true);
        assert_eq!(v["phase"], "lobby");
        assert_eq!(v["env"], "test");
        assert_eq!(v["ref"], "deadbeef");
    }

    /// #1027: `POST /go` fächert pro Welt beide verifizierten Routen
    /// (`/resume_game` → `/start`); `/state` hält den aggregierten Zustell-Status
    /// fest; `{"retry":true}` fächert erneut (neue `start_epoch`-Runde).
    #[tokio::test]
    async fn go_broadcasts_to_both_bridge_endpoints() {
        let (addr_a, cap_a) = capture_endpoint().await;
        let (addr_b, cap_b) = capture_endpoint().await;
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
        // Welt-Block: Basis-Endpoint + Routen in Reihenfolge.
        assert_eq!(v["broadcast"]["A"]["endpoint"], format!("http://{addr_a}"));
        assert_eq!(v["broadcast"]["A"]["routes"][0]["route"], "resume_game");
        assert_eq!(v["broadcast"]["A"]["routes"][1]["route"], "start");
        assert_eq!(
            v["broadcast"]["A"]["routes"][1]["endpoint"],
            format!("http://{addr_a}/start")
        );

        // beide Welten haben exakt die GO-Routen gesehen (nie /exec).
        assert_go_fanout(&cap_a, &["/resume_game", "/start"]).await;
        assert_go_fanout(&cap_b, &["/resume_game", "/start"]).await;

        // /state zeigt den aggregierten Zustell-Status.
        let (_, v) = call(&app, "GET", "/state", None).await;
        assert_eq!(v["teams"]["A"]["go_broadcast"]["ok"], true);
        assert_eq!(
            v["teams"]["A"]["go_broadcast"]["endpoint"],
            format!("http://{addr_a}")
        );

        // Retry-Broadcast bei laufendem Match: erneuter Fan-out an alle Routen.
        let (s, v) = call(&app, "POST", "/go", Some(json!({"retry": true}))).await;
        assert_eq!(s, StatusCode::OK);
        assert_eq!(v["started"], false);
        assert_eq!(v["broadcast"]["A"]["ok"], true);
        tokio::time::sleep(Duration::from_millis(100)).await;
        {
            let reqs = cap_a.lock().await;
            for route in ["/resume_game", "/start"] {
                let n = reqs
                    .iter()
                    .filter(|r| r.starts_with(&format!("POST {route} ")))
                    .count();
                assert_eq!(n, 2, "retry: {route} genau zweimal: {reqs:?}");
            }
        }
        let (_, v) = call(&app, "GET", "/state", None).await;
        assert_eq!(v["teams"]["A"]["go_broadcast"]["ok"], true);
    }

    /// #1027: Partial-Fehler einer Welt (Endpoint tot) ist HTTP 200; die andere
    /// Welt wird trotzdem gepusht, `ok:false` je betroffener Route (kein 5xx).
    #[tokio::test]
    async fn go_partial_failure_is_ok_200() {
        let (addr_b, cap_b) = capture_endpoint().await;
        let mut cfg = test_cfg();
        cfg.bridge = [
            // Port 1 ist praktisch immer zu → Transportfehler für A.
            Some("http://127.0.0.1:1/exec".to_string()),
            Some(format!("http://{addr_b}/exec")),
        ];
        let app = make_app(cfg).await;
        ready_state(&app).await;

        let (s, v) = call(&app, "POST", "/go", Some(json!({}))).await;
        assert_eq!(s, StatusCode::OK);
        assert_eq!(v["broadcast"]["A"]["ok"], false);
        assert_eq!(v["broadcast"]["A"]["routes"][0]["ok"], false);
        assert!(v["broadcast"]["A"]["routes"][0]["error"].is_string());
        assert_eq!(v["broadcast"]["B"]["ok"], true);

        // Die erreichbare Welt B hat den Fan-out trotzdem gesehen.
        assert_go_fanout(&cap_b, &["/resume_game", "/start"]).await;

        let (_, st) = call(&app, "GET", "/state", None).await;
        assert_eq!(st["teams"]["A"]["go_broadcast"]["ok"], false);
        assert_eq!(st["teams"]["B"]["go_broadcast"]["ok"], true);
    }

    /// #1027: Ohne konfigurierten Endpoint bleibt der Handler funktionsfähig
    /// (`ok:null` + `note` je Welt, kein Panic/5xx).
    #[tokio::test]
    async fn go_without_bridge_reports_null_ok() {
        let app = make_app(test_cfg()).await; // bridge = [None, None]
        ready_state(&app).await;
        let (s, v) = call(&app, "POST", "/go", Some(json!({}))).await;
        assert_eq!(s, StatusCode::OK);
        assert_eq!(v["broadcast"]["A"]["ok"], Value::Null);
        assert!(v["broadcast"]["A"]["note"].is_string());
        assert_eq!(v["broadcast"]["B"]["ok"], Value::Null);
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
        assert_eq!(v["broadcast"][0]["command"], "rb_reset");
        assert_eq!(v["broadcast"][0]["cmd_id"], 2);

        // Genau EIN Capture mit `command:rb_reset` **inkl. `cmd_id`** (Dedup-Schlüssel).
        tokio::time::sleep(Duration::from_millis(100)).await;
        {
            let caps = captures.lock().await;
            assert_eq!(caps.len(), 1, "caps: {caps:?}");
            assert!(
                caps[0].contains("\"command\":\"rb_reset\""),
                "req: {}",
                caps[0]
            );
            assert!(caps[0].contains("\"cmd_id\":2"), "req: {}", caps[0]);
        }

        // B1-Kern: das gepushte `rb_reset` darf **nicht** erneut über den Poll
        // auftauchen (Push und Poll sind genau EINE Zustellung, nicht zwei).
        let (s, poll) = call(&app, "GET", "/referee/poll?world=A", None).await;
        assert_eq!(s, StatusCode::OK);
        let polled = poll["commands"].as_array().unwrap();
        assert!(
            !polled.iter().any(|c| c["command"] == "rb_reset"),
            "gepushtes rb_reset darf nicht doppelt im Poll liegen: {polled:?}"
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
            polled.iter().any(|c| c["command"] == "rb_reset"),
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
            polled.iter().any(|c| c["command"] == "rb_reset"),
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

    /// #1027: Der Routen-Helfer entfernt `/exec` und hängt die Aktion an;
    /// `dom_action_url`/`ingress_url` delegieren an dieselbe Quelle.
    #[test]
    fn bridge_action_url_strips_exec_and_appends_action() {
        assert_eq!(
            bridge_action_url("http://h:9002/exec", "start"),
            "http://h:9002/start"
        );
        // ohne `/exec`-Suffix wird die Basis nicht verändert (kein Strip)
        assert_eq!(
            bridge_action_url("http://h:9002/base", "resume_game"),
            "http://h:9002/base/resume_game"
        );
        assert_eq!(
            bridge_action_url("http://h:9002", "start"),
            "http://h:9002/start"
        );
        // Trailing-Slash robust
        assert_eq!(
            bridge_action_url("http://h:9002/", "start"),
            "http://h:9002/start"
        );
        assert_eq!(
            bridge_action_url("http://h:9002/exec/", "start"),
            "http://h:9002/start"
        );
        // eine Quelle
        assert_eq!(
            dom_action_url("http://h:9002/exec", "pause_dom"),
            "http://h:9002/pause_dom"
        );
        assert_eq!(
            ingress_url("http://h:9002/exec"),
            "http://h:9002/incoming_send"
        );
    }

    /// #1027: Reihenfolge des Fan-outs — erst Sim entfrieren, dann Zyklus armieren.
    #[test]
    fn go_routes_order_resume_before_start() {
        assert_eq!(GO_ROUTES, ["resume_game", "start"]);
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

        // hq_destroyed → rb_reset, Runde 1.
        let (s, v) = call(
            &app,
            "POST",
            "/referee/event",
            Some(json!({"world": "A", "type": "hq_destroyed"})),
        )
        .await;
        assert_eq!(s, StatusCode::OK);
        assert_eq!(v["commands"][0]["command"], "rb_reset");
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

    /// US2 (#996): wellen-basierter Send mit `level` landet in der Queue der
    /// Gegner-Welt und wird world-getaggt im Feed geführt.
    #[tokio::test]
    async fn send_with_level_queues_for_opponent() {
        let app = make_app(test_cfg()).await;
        register(&app, "A", "momo").await;
        register(&app, "B", "matheo").await;
        call(&app, "POST", "/go", Some(json!({}))).await;

        let (s, v) = call(
            &app,
            "POST",
            "/send",
            Some(json!({"world": "A", "level": 3, "value": 1400})),
        )
        .await;
        assert_eq!(s, StatusCode::OK);
        assert_eq!(v["queued_for"], "B");
        assert_eq!(v["level"], 3);
        assert_eq!(v["batch"]["level"], 3);
        assert_eq!(v["batch"]["from"], "A");
        assert_eq!(v["pending_sends"], 1);

        let (_, st) = call(&app, "GET", "/state", None).await;
        let pend = st["teams"]["B"]["pending_sends"].as_array().unwrap();
        assert_eq!(pend.len(), 1);
        assert_eq!(pend[0]["level"], 3);
        assert_eq!(pend[0]["value"], 1400);

        // level 0 → 400 invalid.
        let (s, v) = call(
            &app,
            "POST",
            "/send",
            Some(json!({"world": "A", "level": 0})),
        )
        .await;
        assert_eq!(s, StatusCode::BAD_REQUEST);
        assert_eq!(err_type(&v), "invalid");

        // Unit-Send bleibt unverändert möglich (Legacy).
        let (s, v) = call(
            &app,
            "POST",
            "/send",
            Some(json!({"world": "B", "units": [{"unit": "x", "count": 1}], "value": 10})),
        )
        .await;
        assert_eq!(s, StatusCode::OK);
        assert_eq!(v["queued_for"], "A");
        assert_eq!(v["level"], Value::Null);

        // Feed-Eintrag ist world-getaggt.
        let (_, ev) = call(&app, "GET", "/events", None).await;
        let send = ev["events"]
            .as_array()
            .unwrap()
            .iter()
            .find(|e| e["kind"] == "send")
            .unwrap();
        assert_eq!(send["world"], "A");
    }

    // ---- US4: Ingress-Transport beim Wellenstart der Zielwelt (#996, G5) ----

    /// Ein Level-Send A→B wird bei B's `wave_start` GENAU EINMAL als
    /// `POST /incoming_send {level,from,delay_s}` an die B-Bridge gepusht.
    #[tokio::test]
    async fn wave_start_pushes_incoming_send_to_target_bridge() {
        let (addr, captures) = capture_endpoint().await;
        let mut cfg = test_cfg();
        // Bridge-Endpoint wie im Betrieb (HTTP-Adapter-Pfad `/exec`).
        cfg.bridge = [None, Some(format!("http://{addr}/exec"))];
        let app = make_app(cfg).await;
        register(&app, "A", "momo").await;
        register(&app, "B", "matheo").await;
        call(&app, "POST", "/go", Some(json!({}))).await;

        // A sendet eine Welle (Level) an B.
        let (s, _) = call(
            &app,
            "POST",
            "/send",
            Some(json!({"world": "A", "level": 3, "value": 1400})),
        )
        .await;
        assert_eq!(s, StatusCode::OK);

        // B meldet seinen Wellenstart → Drain + Ingress-Push an B.
        let (s, v) = call(
            &app,
            "POST",
            "/report",
            Some(json!({"world": "B", "event": "wave_start", "built_value": 6400})),
        )
        .await;
        assert_eq!(s, StatusCode::OK);
        assert_eq!(v["effect"], "locked");
        let ingress = v["ingress"].as_array().unwrap();
        assert_eq!(ingress.len(), 1, "ingress: {v}");
        assert_eq!(ingress[0]["level"], 3);
        assert_eq!(ingress[0]["from"], "A");
        assert_eq!(ingress[0]["ok"], true);
        assert_eq!(ingress[0]["http_status"], 200);
        assert_eq!(
            ingress[0]["endpoint"],
            format!("http://{addr}/incoming_send")
        );

        // Die B-Bridge hat genau EINEN /incoming_send-POST gesehen
        // (die GO-Routen `/resume_game`/`/start` zählen hier nicht).
        tokio::time::sleep(Duration::from_millis(100)).await;
        {
            let all = captures.lock().await;
            let caps: Vec<&String> = all
                .iter()
                .filter(|c| c.starts_with("POST /incoming_send "))
                .collect();
            assert_eq!(caps.len(), 1, "caps: {all:?}");
            assert!(
                caps[0].starts_with("POST /incoming_send HTTP/1.1"),
                "req: {}",
                caps[0]
            );
            assert!(caps[0].contains("\"level\":3"), "req: {}", caps[0]);
            assert!(caps[0].contains("\"from\":\"A\""), "req: {}", caps[0]);
            // delay_s-Default aus der Config (5.0).
            assert!(caps[0].contains("\"delay_s\":5.0"), "req: {}", caps[0]);
        }
    }

    /// Ohne konfigurierte Ziel-Bridge bleibt der Referee funktionsfähig
    /// (`ingress.ok == null`), kein Panic; der Reveal trägt den Send weiter.
    #[tokio::test]
    async fn wave_start_without_bridge_still_returns_state() {
        let app = make_app(test_cfg()).await; // bridge = [None, None]
        register(&app, "A", "momo").await;
        register(&app, "B", "matheo").await;
        call(&app, "POST", "/go", Some(json!({}))).await;
        call(
            &app,
            "POST",
            "/send",
            Some(json!({"world": "A", "level": 2, "value": 700})),
        )
        .await;

        let (s, v) = call(
            &app,
            "POST",
            "/report",
            Some(json!({"world": "B", "event": "wave_start"})),
        )
        .await;
        assert_eq!(s, StatusCode::OK);
        let ingress = v["ingress"].as_array().unwrap();
        assert_eq!(ingress.len(), 1, "ingress: {v}");
        assert_eq!(ingress[0]["level"], 2);
        assert_eq!(ingress[0]["ok"], Value::Null);
        assert!(ingress[0]["note"].is_string());

        // Reveal trägt den Batch weiterhin.
        let (_, st) = call(&app, "GET", "/state", None).await;
        let inc = st["reveal"]["incoming"]["B"].as_array().unwrap();
        assert_eq!(inc.len(), 1);
        assert_eq!(inc[0]["level"], 2);
    }

    /// Duplikat/Retry des `wave_start` pusht den Ingress NICHT erneut.
    #[tokio::test]
    async fn duplicate_wave_start_does_not_repush_ingress() {
        let (addr, captures) = capture_endpoint().await;
        let mut cfg = test_cfg();
        cfg.bridge = [None, Some(format!("http://{addr}/exec"))];
        let app = make_app(cfg).await;
        register(&app, "A", "momo").await;
        register(&app, "B", "matheo").await;
        call(&app, "POST", "/go", Some(json!({}))).await;
        call(
            &app,
            "POST",
            "/send",
            Some(json!({"world": "A", "level": 3, "value": 1400})),
        )
        .await;

        let (_, v) = call(
            &app,
            "POST",
            "/report",
            Some(json!({"world": "B", "event": "wave_start"})),
        )
        .await;
        assert_eq!(v["ingress"].as_array().unwrap().len(), 1);

        // Retry von B (Duplikat) → keine neuen Ingress-Pushes.
        let (_, v2) = call(
            &app,
            "POST",
            "/report",
            Some(json!({"world": "B", "event": "wave_start"})),
        )
        .await;
        assert_eq!(v2["effect"], "duplicate");
        assert_eq!(v2["ingress"].as_array().unwrap().len(), 0);

        tokio::time::sleep(Duration::from_millis(100)).await;
        {
            let all = captures.lock().await;
            let ingress_pushes = all
                .iter()
                .filter(|c| c.starts_with("POST /incoming_send "))
                .count();
            assert_eq!(
                ingress_pushes, 1,
                "genau ein Ingress-Push trotz Retry: {all:?}"
            );
        }
    }

    /// Ein unit-basierter (level-loser) Send wird NICHT als Ingress gepusht
    /// (defensiv übersprungen), bleibt aber im Reveal.
    #[tokio::test]
    async fn unit_send_is_not_pushed_as_ingress() {
        let (addr, captures) = capture_endpoint().await;
        let mut cfg = test_cfg();
        cfg.bridge = [None, Some(format!("http://{addr}/exec"))];
        let app = make_app(cfg).await;
        register(&app, "A", "momo").await;
        register(&app, "B", "matheo").await;
        call(&app, "POST", "/go", Some(json!({}))).await;
        call(
            &app,
            "POST",
            "/send",
            Some(json!({"world": "A", "units": [{"unit": "x", "count": 1}], "value": 10})),
        )
        .await;

        let (_, v) = call(
            &app,
            "POST",
            "/report",
            Some(json!({"world": "B", "event": "wave_start"})),
        )
        .await;
        assert_eq!(v["ingress"].as_array().unwrap().len(), 0);
        let (_, st) = call(&app, "GET", "/state", None).await;
        assert_eq!(st["reveal"]["incoming"]["B"].as_array().unwrap().len(), 1);
        tokio::time::sleep(Duration::from_millis(80)).await;
        assert!(captures
            .lock()
            .await
            .iter()
            .all(|c| !c.starts_with("POST /incoming_send ")));
    }

    /// US2/US4 (#996): Cross-World-Send auch in der Gegenrichtung — ein Send
    /// aus Welt B landet bei A's `wave_start` als genau EIN
    /// `POST /incoming_send {level, from:"B"}` an die A-Bridge (der G5-Vertrag
    /// ist richtungsunabhaengig; die Abnahme deckt nur A→B ab).
    #[tokio::test]
    async fn reverse_cross_world_send_b_to_a_pushes_ingress_to_a_bridge() {
        let (addr_a, captures) = capture_endpoint().await;
        let mut cfg = test_cfg();
        cfg.bridge = [Some(format!("http://{addr_a}/exec")), None];
        let app = make_app(cfg).await;
        register(&app, "A", "momo").await;
        register(&app, "B", "matheo").await;
        call(&app, "POST", "/go", Some(json!({}))).await;

        // B sendet eine Welle (Level) an A.
        let (s, _) = call(
            &app,
            "POST",
            "/send",
            Some(json!({"world": "B", "level": 2, "value": 700})),
        )
        .await;
        assert_eq!(s, StatusCode::OK);

        // A meldet seinen Wellenstart -> Ingress-Push an A, from = B.
        let (s, v) = call(
            &app,
            "POST",
            "/report",
            Some(json!({"world": "A", "event": "wave_start", "built_value": 6400})),
        )
        .await;
        assert_eq!(s, StatusCode::OK);
        assert_eq!(v["effect"], "locked");
        let ingress = v["ingress"].as_array().unwrap();
        assert_eq!(ingress.len(), 1, "ingress: {v}");
        assert_eq!(ingress[0]["level"], 2);
        assert_eq!(ingress[0]["from"], "B");
        assert_eq!(ingress[0]["ok"], true);
        assert_eq!(
            ingress[0]["endpoint"],
            format!("http://{addr_a}/incoming_send")
        );

        tokio::time::sleep(Duration::from_millis(100)).await;
        let all = captures.lock().await;
        let caps: Vec<&String> = all
            .iter()
            .filter(|c| c.starts_with("POST /incoming_send "))
            .collect();
        assert_eq!(caps.len(), 1, "caps: {all:?}");
        assert!(caps[0].contains("\"level\":2"), "req: {}", caps[0]);
        assert!(caps[0].contains("\"from\":\"B\""), "req: {}", caps[0]);
    }

    /// US4 (#996): Mehrere offene Sends derselben Welt werden als EIN Ingress-
    /// Push JE Batch zugestellt (nicht gebuendelt) — der G5-Vertrag ist
    /// per-Batch; der Drain raeumt alle Batches.
    #[tokio::test]
    async fn multiple_pending_sends_push_one_ingress_per_batch() {
        let (addr_b, captures) = capture_endpoint().await;
        let mut cfg = test_cfg();
        cfg.bridge = [None, Some(format!("http://{addr_b}/exec"))];
        let app = make_app(cfg).await;
        register(&app, "A", "momo").await;
        register(&app, "B", "matheo").await;
        call(&app, "POST", "/go", Some(json!({}))).await;

        for lvl in [1u32, 3] {
            let (s, _) = call(
                &app,
                "POST",
                "/send",
                Some(json!({"world": "A", "level": lvl, "value": 300})),
            )
            .await;
            assert_eq!(s, StatusCode::OK);
        }

        let (_, v) = call(
            &app,
            "POST",
            "/report",
            Some(json!({"world": "B", "event": "wave_start"})),
        )
        .await;
        let ingress = v["ingress"].as_array().unwrap();
        assert_eq!(ingress.len(), 2, "ingress: {v}");

        tokio::time::sleep(Duration::from_millis(100)).await;
        let all = captures.lock().await;
        let pushes = all
            .iter()
            .filter(|c| c.starts_with("POST /incoming_send "))
            .count();
        assert_eq!(pushes, 2, "caps: {all:?}");
    }

    // ---- US7: host-loser Abnahme-Test des Kern-Pfads (#996, G5 + G6) ----

    /// Abnahme (host-los, in-process):
    ///   Satz 1 — ein Send aus A kommt in B an: bei B's `wave_start` geht
    ///            genau EIN `POST /incoming_send {level:3, from:"A"}` an B.
    ///   Satz 2 — HQ-Tod von B beendet das Match mit Sieger A (`finished`).
    #[tokio::test]
    async fn acceptance_cross_world_send_and_hq_win() {
        let (addr_b, captures) = capture_endpoint().await;
        let mut cfg = test_cfg();
        cfg.bridge = [None, Some(format!("http://{addr_b}/exec"))];
        let app = make_app(cfg).await;

        // Seed: A/B registriert, GO, Runde 1.
        register(&app, "A", "momo").await;
        register(&app, "B", "matheo").await;
        let (s, v) = call(&app, "POST", "/go", Some(json!({}))).await;
        assert_eq!(s, StatusCode::OK);
        assert_eq!(v["round"], 1);

        // Satz 1: Send A→B (Level 3) → bei B's wave_start genau ein Ingress-Push.
        let (s, _) = call(
            &app,
            "POST",
            "/send",
            Some(json!({"world": "A", "level": 3, "value": 1400})),
        )
        .await;
        assert_eq!(s, StatusCode::OK);
        let (s, v) = call(
            &app,
            "POST",
            "/report",
            Some(json!({"world": "B", "event": "wave_start", "built_value": 6400})),
        )
        .await;
        assert_eq!(s, StatusCode::OK);
        assert_eq!(v["effect"], "locked");
        assert_eq!(v["ingress"].as_array().unwrap().len(), 1);

        tokio::time::sleep(Duration::from_millis(100)).await;
        {
            let all = captures.lock().await;
            let incoming: Vec<&String> = all
                .iter()
                .filter(|c| c.starts_with("POST /incoming_send "))
                .collect();
            assert_eq!(incoming.len(), 1, "genau ein incoming_send: {all:?}");
            assert!(incoming[0].contains("\"level\":3"), "req: {}", incoming[0]);
            assert!(
                incoming[0].contains("\"from\":\"A\""),
                "req: {}",
                incoming[0]
            );
        }

        // Satz 2: HQ-Tod von B → Sieger A, Phase finished.
        let (s, v) = call(
            &app,
            "POST",
            "/report",
            Some(json!({"world": "B", "event": "hq_hp", "hp": 0})),
        )
        .await;
        assert_eq!(s, StatusCode::OK);
        assert_eq!(v["match_over"], true);
        assert_eq!(v["winner"], "A");
        assert_eq!(v["phase"], "finished");
        let (_, st) = call(&app, "GET", "/state", None).await;
        assert_eq!(st["phase"], "finished");
        assert_eq!(st["winner"], "A");

        // Gesamt: A→B-Send bleibt im Reveal von B sichtbar (level-getaggt).
        assert_eq!(st["reveal"]["incoming"]["B"][0]["level"], 3);
        assert_eq!(st["reveal"]["incoming"]["B"][0]["from"], "A");

        // Gegenprobe: ohne bridge_for(B) bleibt der Referee funktionsfähig.
        let app2 = make_app(test_cfg()).await;
        register(&app2, "A", "momo").await;
        register(&app2, "B", "matheo").await;
        call(&app2, "POST", "/go", Some(json!({}))).await;
        call(
            &app2,
            "POST",
            "/send",
            Some(json!({"world": "A", "level": 3, "value": 1400})),
        )
        .await;
        let (s, v) = call(
            &app2,
            "POST",
            "/report",
            Some(json!({"world": "B", "event": "wave_start"})),
        )
        .await;
        assert_eq!(s, StatusCode::OK);
        assert_eq!(v["ingress"][0]["ok"], Value::Null);
    }

    // ---- #997: Pause/Resume-Fan-out (HTTP) ----

    /// `POST /pause` fächert `POST /pause_dom` an **beide** Bridges; `POST /resume`
    /// fächert `POST /resume_dom` — je Welt der eigene Endpoint (Muster `broadcast_go`).
    #[tokio::test]
    async fn pause_fans_out_to_both_bridges() {
        let (addr_a, cap_a) = capture_endpoint().await;
        let (addr_b, cap_b) = capture_endpoint().await;
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

        // Pause → beide Bridges sehen POST /pause_dom.
        let (s, v) = call(&app, "POST", "/pause", Some(json!({}))).await;
        assert_eq!(s, StatusCode::OK);
        assert_eq!(v["paused"], true);
        assert_eq!(v["already"], false);
        assert_eq!(v["phase"], "running");
        assert_eq!(v["broadcast"]["A"]["ok"], true);
        assert_eq!(v["broadcast"]["B"]["ok"], true);
        assert_eq!(
            v["broadcast"]["A"]["endpoint"],
            format!("http://{addr_a}/pause_dom")
        );
        assert_eq!(
            v["broadcast"]["B"]["endpoint"],
            format!("http://{addr_b}/pause_dom")
        );

        // Resume → beide Bridges sehen POST /resume_dom.
        let (s, v) = call(&app, "POST", "/resume", Some(json!({}))).await;
        assert_eq!(s, StatusCode::OK);
        assert_eq!(v["paused"], false);
        assert_eq!(v["already"], false);
        assert_eq!(v["broadcast"]["A"]["ok"], true);
        assert_eq!(
            v["broadcast"]["A"]["endpoint"],
            format!("http://{addr_a}/resume_dom")
        );
        assert_eq!(
            v["broadcast"]["B"]["endpoint"],
            format!("http://{addr_b}/resume_dom")
        );

        tokio::time::sleep(Duration::from_millis(100)).await;
        let a = cap_a.lock().await;
        assert!(
            a.iter().any(|r| r.starts_with("POST /pause_dom ")),
            "A pause_dom: {a:?}"
        );
        assert!(
            a.iter().any(|r| r.starts_with("POST /resume_dom ")),
            "A resume_dom: {a:?}"
        );
        let b = cap_b.lock().await;
        assert!(
            b.iter().any(|r| r.starts_with("POST /pause_dom ")),
            "B pause_dom: {b:?}"
        );
        assert!(
            b.iter().any(|r| r.starts_with("POST /resume_dom ")),
            "B resume_dom: {b:?}"
        );
    }

    /// Pause/Resume nur in `Phase::Running` — sonst 409 `conflict`.
    #[tokio::test]
    async fn pause_blocked_outside_running() {
        let app = make_app(test_cfg()).await;

        // Lobby
        let (s, v) = call(&app, "POST", "/pause", Some(json!({}))).await;
        assert_eq!(s, StatusCode::CONFLICT);
        assert_eq!(err_type(&v), "conflict");
        let (s, _) = call(&app, "POST", "/resume", Some(json!({}))).await;
        assert_eq!(s, StatusCode::CONFLICT);

        // Ready
        ready_state(&app).await;
        let (s, _) = call(&app, "POST", "/pause", Some(json!({}))).await;
        assert_eq!(s, StatusCode::CONFLICT);

        // Finished
        call(&app, "POST", "/go", Some(json!({}))).await;
        call(
            &app,
            "POST",
            "/report",
            Some(json!({"world": "A", "event": "hq_hp", "hp": 0})),
        )
        .await;
        let (s, v) = call(&app, "POST", "/pause", Some(json!({}))).await;
        assert_eq!(s, StatusCode::CONFLICT);
        assert_eq!(err_type(&v), "conflict");
    }

    /// Doppel-`pause` ist idempotent (`already:true`, kein zweiter Push);
    /// `{"retry":true}` fächert erneut.
    #[tokio::test]
    async fn pause_retry_rebroadcasts() {
        let (addr, cap) = capture_endpoint().await;
        let mut cfg = test_cfg();
        cfg.bridge = [Some(format!("http://{addr}/exec")), None];
        let app = make_app(cfg).await;
        ready_state(&app).await;
        call(&app, "POST", "/go", Some(json!({}))).await;

        // 1. Pause → Push.
        let (_, v) = call(&app, "POST", "/pause", Some(json!({}))).await;
        assert_eq!(v["already"], false);
        assert_eq!(v["broadcast"]["A"]["ok"], true);

        // 2. Pause ohne retry → already, kein neuer Push.
        let (_, v) = call(&app, "POST", "/pause", Some(json!({}))).await;
        assert_eq!(v["already"], true);
        assert_eq!(v["paused"], true);
        tokio::time::sleep(Duration::from_millis(100)).await;
        let count1 = cap
            .lock()
            .await
            .iter()
            .filter(|r| r.starts_with("POST /pause_dom "))
            .count();
        assert_eq!(count1, 1, "kein zweiter Push ohne retry");

        // 3. Pause mit retry → erneuter Push.
        let (_, v) = call(&app, "POST", "/pause", Some(json!({"retry": true}))).await;
        assert_eq!(v["already"], true);
        assert_eq!(v["broadcast"]["A"]["ok"], true);
        tokio::time::sleep(Duration::from_millis(100)).await;
        let count2 = cap
            .lock()
            .await
            .iter()
            .filter(|r| r.starts_with("POST /pause_dom "))
            .count();
        assert_eq!(count2, 2, "retry fächert erneut");
    }

    /// `/state` liefert `paused` top-level + `teams.<W>.pause_broadcast`; ohne
    /// Bridge bleibt der Referee funktionsfähig (`ok:null` + `note`).
    #[tokio::test]
    async fn state_reports_paused() {
        let app = make_app(test_cfg()).await; // bridge = [None, None]
        ready_state(&app).await;
        call(&app, "POST", "/go", Some(json!({}))).await;

        let (_, st) = call(&app, "GET", "/state", None).await;
        assert_eq!(st["paused"], false);
        assert_eq!(st["teams"]["A"]["pause_broadcast"]["at"], Value::Null);

        let (s, v) = call(&app, "POST", "/pause", Some(json!({}))).await;
        assert_eq!(s, StatusCode::OK);
        assert_eq!(v["paused"], true);
        assert_eq!(v["broadcast"]["A"]["ok"], Value::Null);
        assert!(v["broadcast"]["A"]["note"].is_string());

        let (_, st) = call(&app, "GET", "/state", None).await;
        assert_eq!(st["paused"], true);
        assert_eq!(st["phase"], "running"); // Pause ändert die Phase nicht
        assert!(st["feed"]
            .as_array()
            .unwrap()
            .iter()
            .any(|e| e["kind"] == "pause"));

        let (s, v) = call(&app, "POST", "/resume", Some(json!({}))).await;
        assert_eq!(s, StatusCode::OK);
        assert_eq!(v["paused"], false);
        let (_, st) = call(&app, "GET", "/state", None).await;
        assert_eq!(st["paused"], false);
        assert!(st["feed"]
            .as_array()
            .unwrap()
            .iter()
            .any(|e| e["kind"] == "resume"));
    }

    /// Partial-Fehler einer Welt ist HTTP 200 mit `ok:false` je Welt; die
    /// andere Welt wird trotzdem gepusht (kein 5xx, kein Panic).
    #[tokio::test]
    async fn pause_partial_failure_is_ok_200() {
        let (addr_b, cap_b) = capture_endpoint().await;
        let mut cfg = test_cfg();
        cfg.bridge = [
            // Port 1 ist praktisch immer zu → Transportfehler für A.
            Some("http://127.0.0.1:1/exec".to_string()),
            Some(format!("http://{addr_b}/exec")),
        ];
        let app = make_app(cfg).await;
        ready_state(&app).await;
        call(&app, "POST", "/go", Some(json!({}))).await;

        let (s, v) = call(&app, "POST", "/pause", Some(json!({}))).await;
        assert_eq!(s, StatusCode::OK);
        assert_eq!(v["paused"], true);
        assert_eq!(v["broadcast"]["A"]["ok"], false);
        assert!(v["broadcast"]["A"]["error"].is_string());
        assert_eq!(v["broadcast"]["B"]["ok"], true);

        // Die erreichbare Welt B hat den Push trotzdem gesehen.
        tokio::time::sleep(Duration::from_millis(100)).await;
        assert!(cap_b
            .lock()
            .await
            .iter()
            .any(|r| r.starts_with("POST /pause_dom ")));
    }

    /// #997 End-to-End: der komplette Match-Flow gegen den echten Router mit
    /// zwei Mock-Bridge-Endpoints — lobby(A,B) → ready → go → `POST /pause` →
    /// `POST /resume`. Prüft, dass **beide** Mocks *genau einmal* `POST
    /// /pause_dom` bzw. `POST /resume_dom` sehen und dass `/state` `paused`
    /// true/false spiegelt (Pause ändert die Phase nicht).
    #[tokio::test]
    async fn pause_resume_full_flow_end_to_end() {
        let (addr_a, cap_a) = capture_endpoint().await;
        let (addr_b, cap_b) = capture_endpoint().await;
        let mut cfg = test_cfg();
        cfg.bridge = [
            Some(format!("http://{addr_a}/exec")),
            Some(format!("http://{addr_b}/exec")),
        ];
        let app = make_app(cfg).await;

        // lobby(A,B) → ready → go (der echte, bestehende VS-Flow).
        assert_eq!(register(&app, "A", "momo").await.0, StatusCode::OK);
        assert_eq!(register(&app, "B", "matheo").await.0, StatusCode::OK);
        let (_, v) = call(&app, "POST", "/ready", Some(json!({"world": "A"}))).await;
        assert_eq!(v["phase"], "lobby"); // erst ein Ready
        let (_, v) = call(&app, "POST", "/ready", Some(json!({"world": "B"}))).await;
        assert_eq!(v["phase"], "ready");
        let (s, v) = call(&app, "POST", "/go", Some(json!({}))).await;
        assert_eq!(s, StatusCode::OK);
        assert_eq!(v["phase"], "running");

        // vor Pause: /state meldet nicht pausiert.
        let (_, st) = call(&app, "GET", "/state", None).await;
        assert_eq!(st["paused"], false);

        // POST /pause → beide Welten gepusht, `paused` true, Phase bleibt running.
        let (s, v) = call(&app, "POST", "/pause", Some(json!({}))).await;
        assert_eq!(s, StatusCode::OK);
        assert_eq!(v["paused"], true);
        assert_eq!(v["already"], false);
        assert_eq!(v["phase"], "running");
        assert_eq!(v["broadcast"]["A"]["ok"], true);
        assert_eq!(v["broadcast"]["B"]["ok"], true);
        assert_eq!(
            v["broadcast"]["A"]["endpoint"],
            format!("http://{addr_a}/pause_dom")
        );
        assert_eq!(
            v["broadcast"]["B"]["endpoint"],
            format!("http://{addr_b}/pause_dom")
        );

        // /state spiegelt paused=true + pause_broadcast je Welt.
        let (_, st) = call(&app, "GET", "/state", None).await;
        assert_eq!(st["paused"], true);
        assert_eq!(st["phase"], "running");
        assert_eq!(st["teams"]["A"]["pause_broadcast"]["ok"], true);
        assert_eq!(st["teams"]["B"]["pause_broadcast"]["ok"], true);

        // POST /resume → beide Welten gepusht, `paused` false.
        let (s, v) = call(&app, "POST", "/resume", Some(json!({}))).await;
        assert_eq!(s, StatusCode::OK);
        assert_eq!(v["paused"], false);
        assert_eq!(v["already"], false);
        assert_eq!(v["broadcast"]["A"]["ok"], true);
        assert_eq!(
            v["broadcast"]["A"]["endpoint"],
            format!("http://{addr_a}/resume_dom")
        );
        assert_eq!(
            v["broadcast"]["B"]["endpoint"],
            format!("http://{addr_b}/resume_dom")
        );
        let (_, st) = call(&app, "GET", "/state", None).await;
        assert_eq!(st["paused"], false);
        assert_eq!(st["phase"], "running");

        // Beide Mocks haben GENAU EIN POST /pause_dom und EIN POST /resume_dom.
        tokio::time::sleep(Duration::from_millis(150)).await;
        for (name, cap) in [("A", &cap_a), ("B", &cap_b)] {
            let reqs = cap.lock().await;
            let pauses = reqs
                .iter()
                .filter(|r| r.starts_with("POST /pause_dom "))
                .count();
            let resumes = reqs
                .iter()
                .filter(|r| r.starts_with("POST /resume_dom "))
                .count();
            assert_eq!(pauses, 1, "{name}: genau ein pause_dom — {reqs:?}");
            assert_eq!(resumes, 1, "{name}: genau ein resume_dom — {reqs:?}");
        }
    }

    // ---- #999: Match-Records (Capture + Read-Endpoint) ----

    /// US2/US3: ein abgeschlossenes Match erzeugt genau EINEN Record mit
    /// Teilnehmern + Sieger; der zweite Report dupliziert nicht; ein Rematch
    /// erzeugt einen neuen Record; `GET /matches/{id}` liefert ihn additiv.
    #[tokio::test]
    async fn match_end_creates_record_and_read_endpoint() {
        let app = make_app(test_cfg()).await;
        register(&app, "A", "momo").await;
        register(&app, "B", "matheo").await;
        call(&app, "POST", "/go", Some(json!({}))).await;

        // Vor Match-Ende existiert noch kein Record.
        let (s, _) = call(&app, "GET", "/matches/rift-1", None).await;
        assert_eq!(s, StatusCode::NOT_FOUND);

        // HQ-Tod von A → Sieger B.
        let (s, v) = call(
            &app,
            "POST",
            "/report",
            Some(json!({"world": "A", "event": "hq_hp", "hp": 0})),
        )
        .await;
        assert_eq!(s, StatusCode::OK);
        assert_eq!(v["match_over"], true);

        let (s, rec) = call(&app, "GET", "/matches/rift-1", None).await;
        assert_eq!(s, StatusCode::OK);
        assert_eq!(rec["match_id"], "rift-1");
        assert_eq!(rec["rematch"], 0);
        assert_eq!(rec["mode"], "duel");
        assert_eq!(rec["winner_player"], "matheo");
        assert!(rec["finished_at"].as_str().unwrap().ends_with('Z'));
        let parts = rec["participants"].as_array().unwrap();
        assert_eq!(parts.len(), 2, "rec: {rec}");
        let momo = parts.iter().find(|p| p["display_name"] == "momo").unwrap();
        assert_eq!(momo["result"], "loss");
        assert_eq!(momo["opponent_id"], "B");
        let matheo = parts
            .iter()
            .find(|p| p["display_name"] == "matheo")
            .unwrap();
        assert_eq!(matheo["result"], "win");

        // Zweiter Report derselben Match-Instanz → kein Duplikat (Record
        // bleibt bei 2 Teilnehmern; nach `finished` lehnt der State ab).
        let (s, _) = call(
            &app,
            "POST",
            "/report",
            Some(json!({"world": "A", "event": "hq_hp", "hp": 0})),
        )
        .await;
        assert_eq!(s, StatusCode::CONFLICT);
        let (_, rec2) = call(&app, "GET", "/matches/rift-1", None).await;
        assert_eq!(rec2["participants"].as_array().unwrap().len(), 2);

        // Rematch → neuer Record (Key rematch=1), Sieger diesmal A (momo).
        let (s, _) = call(&app, "POST", "/rematch", None).await;
        assert_eq!(s, StatusCode::OK);
        call(&app, "POST", "/go", Some(json!({}))).await;
        call(
            &app,
            "POST",
            "/report",
            Some(json!({"world": "B", "event": "hq_hp", "hp": 0})),
        )
        .await;
        let (s, rec3) = call(&app, "GET", "/matches/rift-1?rematch=1", None).await;
        assert_eq!(s, StatusCode::OK);
        assert_eq!(rec3["rematch"], 1);
        assert_eq!(rec3["winner_player"], "momo");

        // Der Record der ersten Instanz bleibt unverändert.
        let (_, rec0) = call(&app, "GET", "/matches/rift-1?rematch=0", None).await;
        assert_eq!(rec0["winner_player"], "matheo");

        // Unbekannte match_id → 404.
        let (s, _) = call(&app, "GET", "/matches/nope", None).await;
        assert_eq!(s, StatusCode::NOT_FOUND);
    }

    /// Persistenz überlebt einen Store-Neustart (eigene DB-Datei).
    #[tokio::test]
    async fn match_record_persists_across_store_reopen() {
        let cfg = test_cfg();
        let db = cfg.db_path.clone();
        {
            let app = make_app(cfg).await;
            register(&app, "A", "momo").await;
            register(&app, "B", "matheo").await;
            call(&app, "POST", "/go", Some(json!({}))).await;
            call(
                &app,
                "POST",
                "/report",
                Some(json!({"world": "A", "event": "hq_hp", "hp": 0})),
            )
            .await;
        }
        // Frischer Store auf derselben Datei → Record ist noch da.
        let store = RecordStore::open(&db).unwrap();
        let rec = store.get("rift-1", 0).unwrap().unwrap();
        assert_eq!(rec.winner_player, "matheo");
        assert_eq!(rec.participants.len(), 2);
        let _ = std::fs::remove_file(&db);
    }

    // ---- US1 (#298): Bearer-Layer fuer mutierende Routen ----

    /// Jede mutierende Route weist ohne Bearer mit 401 ab.
    #[tokio::test]
    async fn every_write_route_rejects_without_bearer() {
        let app = make_app(test_cfg()).await;
        for route in WRITE_ROUTES {
            let (s, _) = request(&app, "POST", route, None, None).await;
            assert_eq!(s, StatusCode::UNAUTHORIZED, "{route} ohne Bearer");
        }
    }

    /// 401 traegt die Bearer-Challenge (`WWW-Authenticate: Bearer`).
    #[tokio::test]
    async fn missing_bearer_401_carries_www_authenticate() {
        let app = make_app(test_cfg()).await;
        let req = Request::builder()
            .method("POST")
            .uri("/wave")
            .header("content-type", "application/json")
            .body(axum::body::Body::from(json!({"n": 3}).to_string()))
            .unwrap();
        let resp = app.clone().oneshot(req).await.unwrap();
        assert_eq!(resp.status(), StatusCode::UNAUTHORIZED);
        assert_eq!(
            resp.headers()
                .get(axum::http::header::WWW_AUTHENTICATE)
                .and_then(|v| v.to_str().ok()),
            Some("Bearer")
        );
    }

    /// Falscher Bearer → 401; korrekter Bearer → bisheriges Verhalten (nie 401).
    #[tokio::test]
    async fn wrong_bearer_rejected_correct_bearer_passes() {
        let app = make_app(test_cfg()).await;
        let (s, v) = request(&app, "POST", "/wave", Some(json!({"n": 3})), Some("falsch")).await;
        assert_eq!(s, StatusCode::UNAUTHORIZED);
        assert_eq!(err_type(&v), "unauthorized");
        // Ohne Bridge antwortet /wave mit 409 — entscheidend: NICHT 401.
        let (s, _) = request(
            &app,
            "POST",
            "/wave",
            Some(json!({"n": 3})),
            Some(TEST_TOKEN),
        )
        .await;
        assert_ne!(s, StatusCode::UNAUTHORIZED);
        // Mutierende Route mit 200-Semantik: /rematch.
        let (s, _) = request(&app, "POST", "/rematch", None, Some(TEST_TOKEN)).await;
        assert_eq!(s, StatusCode::OK);
    }

    /// Lesende Routen + Web-UI bleiben ohne Bearer frei.
    #[tokio::test]
    async fn read_routes_and_web_ui_are_open() {
        let app = make_app(test_cfg()).await;
        for uri in [
            "/state",
            "/health",
            "/events",
            "/referee/poll?world=A",
            "/matches/rift-1",
        ] {
            let (s, _) = request(&app, "GET", uri, None, None).await;
            assert_ne!(s, StatusCode::UNAUTHORIZED, "{uri} muss frei sein");
        }
        let (s, _) = request(&app, "GET", "/state", None, None).await;
        assert_eq!(s, StatusCode::OK);
        let (s, _) = request(&app, "GET", "/health", None, None).await;
        assert_eq!(s, StatusCode::OK);
        // ServeDir-Web-UI (index.html) — freier Fallback.
        let (s, _) = request(&app, "GET", "/", None, None).await;
        assert_eq!(s, StatusCode::OK, "Web-UI / muss frei sein");
    }

    /// Leerer Token = fail-closed: mutierend 401 (auch mit beliebigem Bearer),
    /// Lesen bleibt frei.
    #[tokio::test]
    async fn empty_token_is_fail_closed() {
        let mut cfg = test_cfg();
        cfg.token = String::new();
        let app = make_app(cfg).await;
        let (s, v) = request(&app, "POST", "/rematch", None, Some("irgendwas")).await;
        assert_eq!(s, StatusCode::UNAUTHORIZED);
        assert_eq!(err_type(&v), "unauthorized");
        let (s, _) = request(
            &app,
            "POST",
            "/referee/event",
            Some(json!({"world":"A","type":"ready"})),
            None,
        )
        .await;
        assert_eq!(s, StatusCode::UNAUTHORIZED);
        let (s, _) = request(&app, "GET", "/health", None, None).await;
        assert_eq!(s, StatusCode::OK);
    }
}
