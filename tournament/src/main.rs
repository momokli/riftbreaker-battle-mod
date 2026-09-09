//! RIFT BATTLE Tournament-Server — Einstiegspunkt.
//!
//! Konfiguration ausschließlich über Umgebungsvariablen (keine Hardcodes):
//!
//! | Env | Default | Bedeutung |
//! |---|---|---|
//! | `TOURNAMENT_HOST` | `0.0.0.0` | Bind-Adresse |
//! | `TOURNAMENT_PORT` | `8080` | HTTP-Port (API + Web-UI) |
//! | `TOURNAMENT_AUTO_GO` | `true` | GO automatisch, sobald beide Welten ready |
//! | `RBBRIDGE_A_URL` | — | HTTP-Endpoint Welt A (GO-Broadcast, z. B. `http://10.0.0.5:9001/exec`) |
//! | `RBBRIDGE_B_URL` | — | HTTP-Endpoint Welt B |
//! | `TOURNAMENT_GO_TIMEOUT_MS` | `3000` | Timeout je Broadcast-Endpoint |
//! | `TOURNAMENT_HQ_HP` | `100` | Start-HP jedes HQ |
//! | `TOURNAMENT_WEB_DIR` | `<crate>/web` | Verzeichnis der statischen Web-UI |
//! | `RUST_LOG` | `info` | Log-Level (tracing) |
//!
//! Siehe `docs/TOURNAMENT_API.md` für das komplette Protokoll.

mod api;
mod broadcast;
mod state;

use api::{AppState, Config};
use std::path::PathBuf;
use std::process::ExitCode;
use std::time::Duration;

fn env_bool(name: &str, default: bool) -> Result<bool, String> {
    match std::env::var(name) {
        Ok(v) => match v.trim().to_ascii_lowercase().as_str() {
            "1" | "true" | "yes" | "on" => Ok(true),
            "0" | "false" | "no" | "off" | "" => Ok(false),
            other => Err(format!(
                "{name}: ungültiger Boolean '{other}' (erwartet true/false)"
            )),
        },
        Err(std::env::VarError::NotPresent) => Ok(default),
        Err(e) => Err(format!("{name}: {e}")),
    }
}

fn env_str(name: &str, default: &str) -> String {
    std::env::var(name).unwrap_or_else(|_| default.to_string())
}

fn config_from_env() -> Result<Config, String> {
    let host = env_str("TOURNAMENT_HOST", "0.0.0.0");
    let port: u16 = env_str("TOURNAMENT_PORT", "8080")
        .parse()
        .map_err(|_| "TOURNAMENT_PORT muss eine Zahl sein (0–65535)".to_string())?;
    let auto_go = env_bool("TOURNAMENT_AUTO_GO", true)?;
    let go_timeout_ms: u64 = env_str("TOURNAMENT_GO_TIMEOUT_MS", "3000")
        .parse()
        .map_err(|_| "TOURNAMENT_GO_TIMEOUT_MS muss eine Zahl sein (ms)".to_string())?;
    let hq_hp_start: f64 = env_str("TOURNAMENT_HQ_HP", "100")
        .parse()
        .map_err(|_| "TOURNAMENT_HQ_HP muss eine Zahl sein".to_string())?;

    // rbbridge-Endpoints validieren (nur http://, v1)
    let bridge_a = match std::env::var("RBBRIDGE_A_URL") {
        Ok(u) if !u.trim().is_empty() => {
            broadcast::validate_http_url(&u)?;
            Some(u)
        }
        _ => None,
    };
    let bridge_b = match std::env::var("RBBRIDGE_B_URL") {
        Ok(u) if !u.trim().is_empty() => {
            broadcast::validate_http_url(&u)?;
            Some(u)
        }
        _ => None,
    };

    let web_dir = match std::env::var("TOURNAMENT_WEB_DIR") {
        Ok(d) if !d.trim().is_empty() => PathBuf::from(d),
        _ => PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("web"),
    };
    if !web_dir.is_dir() {
        return Err(format!(
            "Web-UI-Verzeichnis nicht gefunden: {} (TOURNAMENT_WEB_DIR?)",
            web_dir.display()
        ));
    }

    Ok(Config {
        host,
        port,
        auto_go,
        bridge: [bridge_a, bridge_b],
        go_timeout: Duration::from_millis(go_timeout_ms),
        hq_hp_start,
        web_dir,
    })
}

#[tokio::main]
async fn main() -> ExitCode {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("info")),
        )
        .init();

    let cfg = match config_from_env() {
        Ok(c) => c,
        Err(e) => {
            eprintln!("Konfigurationsfehler: {e}");
            return ExitCode::from(2);
        }
    };

    tracing::info!(
        "RIFT BATTLE Tournament-Server startet auf {}:{} (auto_go={}, hq_hp_start={}, bridge_a={}, bridge_b={})",
        cfg.host,
        cfg.port,
        cfg.auto_go,
        cfg.hq_hp_start,
        cfg.bridge[0].as_deref().unwrap_or("-"),
        cfg.bridge[1].as_deref().unwrap_or("-"),
    );

    let bind_host = cfg.host.clone();
    let bind_port = cfg.port;
    let app_state = AppState::new(cfg);
    let app = api::router(app_state);

    let listener = match tokio::net::TcpListener::bind((bind_host.as_str(), bind_port)).await {
        Ok(l) => l,
        Err(e) => {
            tracing::error!("Bind auf {}:{bind_port} fehlgeschlagen: {e}", bind_host);
            return ExitCode::from(1);
        }
    };
    tracing::info!(
        "API + Web-UI lauschen auf http://{}",
        listener.local_addr().unwrap()
    );

    if let Err(e) = axum::serve(listener, app).await {
        tracing::error!("Serverfehler: {e}");
        return ExitCode::from(1);
    }
    ExitCode::SUCCESS
}
