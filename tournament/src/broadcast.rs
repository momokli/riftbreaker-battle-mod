//! Minimaler HTTP/1.1-POST-Client für den GO-Broadcast an die rbbridge-Endpoints.
//!
//! Bewusst keine schwere HTTP-Client-Dependency: Die rbbridge-HTTP-Adapter
//! (Python/tools auf den Dedi-Servern) sprechen schlichtes HTTP/1.1. Es wird
//! nur `http://` unterstützt — kein TLS, keine Redirects (v1; die Endpoints
//! liegen im selben Netz wie der Tournament-Server).

use serde_json::Value;
use std::time::Duration;

#[derive(Debug, Clone, PartialEq)]
pub struct PushResult {
    /// HTTP-Statuscode, falls eine Antwort gelesen werden konnte.
    pub status: Option<u16>,
    /// Fehlermeldung (Transport/Timeout/nicht-2xx).
    pub error: Option<String>,
}

impl PushResult {
    pub fn ok(&self) -> bool {
        self.error.is_none() && matches!(self.status, Some(s) if (200..300).contains(&s))
    }
}

/// Fehler der URL-Validierung.
pub fn validate_http_url(url: &str) -> Result<(), String> {
    let parsed = url
        .parse::<http::Uri>()
        .map_err(|_| format!("ungültige URL '{url}'"))?;
    match parsed.scheme_str() {
        Some("http") => {}
        _ => {
            return Err(format!(
                "nur http:// URLs werden unterstützt (nicht '{url}')"
            ))
        }
    }
    let host = parsed.host().unwrap_or("").to_string();
    if host.is_empty() {
        return Err(format!("URL '{url}' hat keinen Host"));
    }
    Ok(())
}

/// POSTet `body` als JSON an `url`. Antwort-Status wird geparst; der Body wird
/// nur für Fehlermeldungen gelesen (Connection: close, max. 4 KiB).
pub async fn post_json(url: &str, body: &Value, timeout: Duration) -> PushResult {
    let parsed = match url.parse::<http::Uri>() {
        Ok(u) => u,
        Err(e) => {
            return PushResult {
                status: None,
                error: Some(format!("URL unparsbar: {e}")),
            }
        }
    };
    let host = parsed.host().unwrap_or("").to_string();
    let port = parsed.port_u16().unwrap_or(80);
    let path = if parsed.path().is_empty() {
        "/"
    } else {
        parsed.path()
    };

    let body_bytes = serde_json::to_vec(body).unwrap_or_else(|_| b"{}".to_vec());
    let request = format!(
        "POST {path} HTTP/1.1\r\nHost: {host}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
        body_bytes.len()
    );

    let result = tokio::time::timeout(timeout, async {
        let mut stream = tokio::net::TcpStream::connect((host.as_str(), port)).await?;
        stream.write_all(request.as_bytes()).await?;
        stream.write_all(&body_bytes).await?;
        stream.shutdown().await?;

        // Antwortkopf lesen, dann Body (nur für Fehlertext).
        let mut buf = [0u8; 4096];
        let mut collected = Vec::new();
        loop {
            let n = stream.read(&mut buf).await?;
            if n == 0 {
                break;
            }
            collected.extend_from_slice(&buf[..n]);
            if collected.len() >= 4096 {
                break;
            }
        }
        Ok::<Vec<u8>, std::io::Error>(collected)
    })
    .await;

    match result {
        Err(_) => PushResult {
            status: None,
            error: Some(format!(
                "Timeout nach {} ms (Endpoint {host}:{port})",
                timeout.as_millis()
            )),
        },
        Ok(Err(e)) => PushResult {
            status: None,
            error: Some(format!("Transportfehler zu {host}:{port}: {e}")),
        },
        Ok(Ok(bytes)) => {
            let text = String::from_utf8_lossy(&bytes);
            let status_line = text.lines().next().unwrap_or("");
            let mut parts = status_line.split_whitespace();
            let _proto = parts.next();
            let code = parts.next().and_then(|c| c.parse::<u16>().ok());
            match code {
                Some(c) if (200..300).contains(&c) => PushResult {
                    status: Some(c),
                    error: None,
                },
                Some(c) => {
                    let snippet: String = text.chars().take(200).collect();
                    PushResult {
                        status: Some(c),
                        error: Some(format!(
                            "Endpoint antwortete {c}: {}",
                            snippet.replace('\n', " ")
                        )),
                    }
                }
                None => PushResult {
                    status: None,
                    error: Some(format!(
                        "Keine HTTP-Statuszeile von {host}:{port}: {}",
                        text.chars().take(120).collect::<String>()
                    )),
                },
            }
        }
    }
}

use tokio::io::{AsyncReadExt, AsyncWriteExt};

#[cfg(test)]
mod tests {
    use super::*;

    /// Mini-HTTP-Server für Tests: nimmt EINE Anfrage an, antwortet mit `resp_status`.
    async fn mock_endpoint(
        resp_status: &'static str,
    ) -> (std::net::SocketAddr, tokio::task::JoinHandle<String>) {
        use tokio::net::TcpListener;
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        let handle = tokio::spawn(async move {
            let (mut sock, _) = listener.accept().await.unwrap();
            let mut buf = [0u8; 8192];
            let n = sock.read(&mut buf).await.unwrap();
            sock.write_all(
                format!(
                    "HTTP/1.1 {resp_status}\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok"
                )
                .as_bytes(),
            )
            .await
            .unwrap();
            String::from_utf8_lossy(&buf[..n]).to_string()
        });
        (addr, handle)
    }

    #[tokio::test]
    async fn pushes_json_and_parses_2xx() {
        let (addr, handle) = mock_endpoint("200 OK").await;
        let url = format!("http://{addr}/exec");
        let body = serde_json::json!({"cmd": "go", "round": 1});
        let res = post_json(&url, &body, Duration::from_secs(2)).await;
        assert!(res.ok(), "unexpected: {res:?}");
        let request = handle.await.unwrap();
        let head = request.split("\r\n\r\n").next().unwrap();
        assert!(head.starts_with("POST /exec HTTP/1.1"), "head: {head}");
        assert!(head.contains("Content-Type: application/json"));
        assert!(request.contains("\"cmd\":\"go\""));
    }

    #[tokio::test]
    async fn parses_error_status() {
        let (addr, handle) = mock_endpoint("500 Internal Server Error").await;
        let url = format!("http://{addr}/exec");
        let res = post_json(
            &url,
            &serde_json::json!({"cmd": "go"}),
            Duration::from_secs(2),
        )
        .await;
        assert!(!res.ok());
        assert_eq!(res.status, Some(500));
        assert!(res.error.unwrap().contains("500"));
        handle.await.unwrap();
    }

    #[tokio::test]
    async fn connection_refused_is_error() {
        // Port 1 auf localhost ist praktisch immer zu.
        let url = "http://127.0.0.1:1/exec";
        let res = post_json(
            url,
            &serde_json::json!({"cmd": "go"}),
            Duration::from_millis(500),
        )
        .await;
        assert!(!res.ok());
        assert!(res.status.is_none());
        assert!(res.error.unwrap().contains("Transportfehler"));
    }

    #[tokio::test]
    async fn timeout_is_error() {
        // Schwarzes Loch: Listener akzeptiert, antwortet nie.
        use tokio::net::TcpListener;
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        let handle = tokio::spawn(async move {
            let (_sock, _) = listener.accept().await.unwrap();
            std::future::pending::<()>().await; // nie antworten
        });
        let url = format!("http://{addr}/exec");
        let res = post_json(
            &url,
            &serde_json::json!({"cmd": "go"}),
            Duration::from_millis(200),
        )
        .await;
        assert!(!res.ok());
        assert!(res.error.unwrap().contains("Timeout"));
        handle.abort();
    }

    #[test]
    fn url_validation() {
        assert!(validate_http_url("http://10.0.0.5:9001/exec").is_ok());
        assert!(validate_http_url("https://example.com/x").is_err());
        assert!(validate_http_url("ftp://x").is_err());
        assert!(validate_http_url("http:///nohost").is_err());
        assert!(validate_http_url("nonsense").is_err());
    }
}
