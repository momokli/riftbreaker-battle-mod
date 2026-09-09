//! HTTP-API des Tournament-Servers (axum).
//!
//! Routen (v1):
//!   POST /lobby   Spieler registrieren        {"player": "<name>", "world": "A"|"B"}
//!   POST /ready   Welt ready                  {"world": "A"|"B"}
//!   POST /go      GO-Broadcast + Start        {} | {"retry": true}
//!   POST /send    Wave-Routing A→B            {"world": "A", "units": [...], "value": n}
//!   POST /report  Welt-Events (send_state)    {"world": "A", "event": "wave_start"|"hq_hp", ...}
//!   POST /rematch Reset in die Lobby          {}
//!   GET  /state   Match-Zustand (Poll)        —
//!   GET  /health  Healthcheck                 —
//!   GET  /*       statische Web-UI            —
//!
//! Fehler: `{"error": "<meldung>", "type": "<invalid|not_found|conflict>"}`
//! mit 400/404/409. Unbekannte Felder in Bodies werden ignoriert
//! (vorwärtskompatibel, wie im Protokoll des Trainers üblich).

use crate::broadcast;
use crate::state::{MatchState, Phase, ReadyEffect, StateError, World};
use axum::extract::State as AxumState;
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Json, Router};
use serde::Deserialize;
use serde_json::{json, Value};
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
    /// Start-HP jedes HQ.
    pub hq_hp_start: f64,
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
    pub cfg: Arc<Config>,
}

impl AppState {
    pub fn new(cfg: Config) -> Self {
        AppState {
            state: Arc::new(RwLock::new(MatchState::new(cfg.hq_hp_start))),
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
        .route("/rematch", post(rematch))
        .route("/state", get(state_get))
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
        other => Err(StateError::new(
            "invalid",
            format!("unbekanntes event '{other}' (erwartet: wave_start, hq_hp)"),
        )
        .into()),
    }
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
fn go_payload(match_id: &str, round: u32) -> Value {
    json!({
        "cmd": "go",
        "match_id": match_id,
        "round": round,
    })
}

/// Broadcast an beide Endpoints, Ergebnisse im State festhalten.
async fn broadcast_go(app: &AppState, round: u32) -> Value {
    let match_id = {
        let guard = app.state.read().await;
        guard.match_id.clone()
    };
    let payload = go_payload(&match_id, round);
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
            go_timeout: Duration::from_millis(800),
            hq_hp_start: 100.0,
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

    #[tokio::test]
    async fn health_endpoint() {
        let app = make_app(test_cfg()).await;
        let (s, v) = call(&app, "GET", "/health", None).await;
        assert_eq!(s, StatusCode::OK);
        assert_eq!(v["ok"], true);
        assert_eq!(v["phase"], "lobby");
    }
}
