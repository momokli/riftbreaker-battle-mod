//! Persistente Match-Records (Issue #999) — SQLite, **ELO-frei**.
//!
//! Ein abgeschlossenes Match erzeugt genau EINEN Record, der Teilnehmer und
//! Sieger abbildet (Abnahme #999). Das Modell ist bewusst ranking-neutral: es
//! speichert keine Elo/MMR-Werte (die gehören zu #131). Der Key ist
//! (`match_id`, `rematch`) — `rematch()` regeneriert die `match_id` nicht, der
//! Rematch-Zähler unterscheidet die Records daher eindeutig.
//!
//! Design-Vorlage: `docs/PLAYER_PROFILE_MODEL.md` (SQLite/rusqlite,
//! `TOURNAMENT_DB_PATH`, `journal_mode=WAL`).
//!
//! I/O lebt ausschließlich hier; [`crate::state`] bleibt pur.

use rusqlite::{params, Connection};
use serde::{Deserialize, Serialize};
use std::path::Path;
use std::sync::Mutex;

/// Ein Teilnehmer eines Matches (eine Welt/ein Spieler).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Participant {
    /// Identitäts-Key, falls vorhanden, sonst der Anzeigename (Identität #992
    /// ist heute serverseitig freier Textname → `identity_source = "name"`).
    pub player_id: String,
    /// Anzeigename (max. 32 Zeichen, wie `POST /lobby`).
    pub display_name: String,
    /// Herkunft der Identität: `name` (heute) bzw. `str`/`steamid`/`account`
    /// (Client-Identität #992, sobald verdrahtet).
    pub identity_source: String,
    /// Gegenüber (Welt-Id `A`/`B`) — erlaubt Ranking ohne zweiten Join.
    pub opponent_id: String,
    /// Ergebnis aus Sicht des Teilnehmers: `win` | `loss`.
    pub result: String,
}

/// Persistenter Match-Record (ELO-frei). `Serialize` = Read-Vertrag (`GET /matches/{id}`).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct MatchRecord {
    pub match_id: String,
    pub rematch: u32,
    pub mode: String,
    pub rounds_done: u32,
    pub winner_player: String,
    /// RFC3339 UTC (`YYYY-MM-DDTHH:MM:SSZ`).
    pub finished_at: String,
    pub participants: Vec<Participant>,
}

/// SQLite-Store für [`MatchRecord`]s. Die Connection liegt hinter einem
/// `Mutex`, damit der Store `Sync` ist (axum-State).
pub struct RecordStore {
    conn: Mutex<Connection>,
}

impl RecordStore {
    /// Öffnet (bzw. legt an) die Datenbank, aktiviert WAL und legt das Schema an.
    ///
    /// Fehlende Elternverzeichnisse werden angelegt (Default-Pfad
    /// `./data/rbbattle.db` liegt nicht zwingend vor).
    pub fn open(path: impl AsRef<Path>) -> rusqlite::Result<Self> {
        let path = path.as_ref();
        if let Some(parent) = path.parent() {
            if !parent.as_os_str().is_empty() {
                std::fs::create_dir_all(parent).map_err(|e| {
                    rusqlite::Error::SqliteFailure(
                        rusqlite::ffi::Error::new(rusqlite::ffi::SQLITE_CANTOPEN),
                        Some(format!("Datenverzeichnis {}: {e}", parent.display())),
                    )
                })?;
            }
        }
        let conn = Connection::open(path)?;
        conn.pragma_update(None, "journal_mode", "WAL")?;
        conn.execute_batch(
            "CREATE TABLE IF NOT EXISTS match_record (\n\
                 match_id       TEXT    NOT NULL,\n\
                 rematch        INTEGER NOT NULL,\n\
                 player_id      TEXT    NOT NULL,\n\
                 display_name   TEXT    NOT NULL,\n\
                 identity_source TEXT   NOT NULL,\n\
                 opponent_id    TEXT    NOT NULL,\n\
                 result         TEXT    NOT NULL,\n\
                 mode           TEXT    NOT NULL,\n\
                 rounds_done    INTEGER NOT NULL,\n\
                 winner_player  TEXT    NOT NULL,\n\
                 finished_at    TEXT    NOT NULL,\n\
                 PRIMARY KEY (match_id, rematch, player_id)\n\
             );",
        )?;
        Ok(RecordStore {
            conn: Mutex::new(conn),
        })
    }

    /// Schreibt einen Match-Record idempotent (Key `match_id` + `rematch`).
    ///
    /// Ein erneuter Aufruf mit denselben Teilnehmern legt keine Duplikate an
    /// (`INSERT OR IGNORE` je Teilnehmer-Zeile) — genau der Fall „zweiter
    /// `/report` derselben Match-Instanz“.
    pub fn record(&self, rec: &MatchRecord) -> rusqlite::Result<()> {
        let conn = self.conn.lock().expect("record-store mutex");
        let tx = conn.unchecked_transaction()?;
        for p in &rec.participants {
            tx.execute(
                "INSERT OR IGNORE INTO match_record (\n\
                     match_id, rematch, player_id, display_name, identity_source,\n\
                     opponent_id, result, mode, rounds_done, winner_player, finished_at\n\
                 ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11)",
                params![
                    rec.match_id,
                    rec.rematch,
                    p.player_id,
                    p.display_name,
                    p.identity_source,
                    p.opponent_id,
                    p.result,
                    rec.mode,
                    rec.rounds_done,
                    rec.winner_player,
                    rec.finished_at,
                ],
            )?;
        }
        tx.commit()
    }

    /// Liest den Record zu (`match_id`, `rematch`) — `None`, wenn unbekannt.
    pub fn get(&self, match_id: &str, rematch: u32) -> rusqlite::Result<Option<MatchRecord>> {
        let conn = self.conn.lock().expect("record-store mutex");
        let mut stmt = conn.prepare(
            "SELECT player_id, display_name, identity_source, opponent_id, result,\n\
                    mode, rounds_done, winner_player, finished_at\n\
             FROM match_record\n\
             WHERE match_id = ?1 AND rematch = ?2\n\
             ORDER BY player_id",
        )?;
        let rows = stmt.query_map(params![match_id, rematch], |row| {
            Ok((
                Participant {
                    player_id: row.get(0)?,
                    display_name: row.get(1)?,
                    identity_source: row.get(2)?,
                    opponent_id: row.get(3)?,
                    result: row.get(4)?,
                },
                // Match-weite Felder (je Zeile identisch).
                (
                    row.get::<_, String>(5)?,
                    row.get::<_, u32>(6)?,
                    row.get::<_, String>(7)?,
                    row.get::<_, String>(8)?,
                ),
            ))
        })?;

        let mut parts: Vec<Participant> = Vec::new();
        let mut meta: Option<(String, u32, String, String)> = None;
        for row in rows {
            let (p, m) = row?;
            meta.get_or_insert(m);
            parts.push(p);
        }
        Ok(meta.map(
            |(mode, rounds_done, winner_player, finished_at)| MatchRecord {
                match_id: match_id.to_string(),
                rematch,
                mode,
                rounds_done,
                winner_player,
                finished_at,
                participants: parts,
            },
        ))
    }
}

/// Aktuelle Server-Zeit in ms seit Unix-Epoch (für `finished_at`).
pub fn now_ms() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
}

/// Formatiert ms seit Unix-Epoch als RFC3339 UTC (ohne chrono).
///
/// `Z` statt Offset, Sekundenauflösung — so ist der Zeitstempel in
/// `match_record.finished_at` textuell sortierbar.
pub fn rfc3339_utc(ms: u64) -> String {
    let secs = ms / 1000;
    let days = (secs / 86_400) as i64;
    let rem = secs % 86_400;
    let (h, m, s) = (rem / 3600, (rem % 3600) / 60, rem % 60);
    let (y, mo, d) = civil_from_days(days);
    format!("{y:04}-{mo:02}-{d:02}T{h:02}:{m:02}:{s:02}Z")
}

/// Zivil-Datum aus Tagen seit 1970-01-01 (Howard-Hinnant-Algorithmus).
fn civil_from_days(z: i64) -> (i64, u32, u32) {
    let z = z + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let doe = (z - era * 146_097) as u64; // [0, 146096]
    let yoe = (doe - doe / 1_460 + doe / 36_524 - doe / 146_096) / 365; // [0, 399]
    let y = yoe as i64 + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100); // [0, 365]
    let mp = (5 * doy + 2) / 153; // [0, 11]
    let d = (doy - (153 * mp + 2) / 5 + 1) as u32; // [1, 31]
    let m = if mp < 10 { mp + 3 } else { mp - 9 } as u32; // [1, 12]
    (y + i64::from(m <= 2), m, d)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    fn tmp_db(tag: &str) -> PathBuf {
        let n = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let p = std::env::temp_dir().join(format!("rbbattle-rec-{tag}-{n}.db"));
        let _ = std::fs::remove_file(&p);
        p
    }

    fn sample() -> MatchRecord {
        MatchRecord {
            match_id: "rift-1".into(),
            rematch: 0,
            mode: "duel".into(),
            rounds_done: 3,
            winner_player: "momo".into(),
            finished_at: "2026-09-29T21:11:00Z".into(),
            participants: vec![
                Participant {
                    player_id: "momo".into(),
                    display_name: "momo".into(),
                    identity_source: "name".into(),
                    opponent_id: "B".into(),
                    result: "win".into(),
                },
                Participant {
                    player_id: "matheo".into(),
                    display_name: "matheo".into(),
                    identity_source: "name".into(),
                    opponent_id: "A".into(),
                    result: "loss".into(),
                },
            ],
        }
    }

    #[test]
    fn roundtrip_record_and_get() {
        let path = tmp_db("roundtrip");
        let store = RecordStore::open(&path).unwrap();
        assert!(store.get("rift-1", 0).unwrap().is_none());

        store.record(&sample()).unwrap();
        let got = store.get("rift-1", 0).unwrap().unwrap();
        assert_eq!(got.match_id, "rift-1");
        assert_eq!(got.rematch, 0);
        assert_eq!(got.mode, "duel");
        assert_eq!(got.rounds_done, 3);
        assert_eq!(got.winner_player, "momo");
        assert_eq!(got.finished_at, "2026-09-29T21:11:00Z");

        let mut names: Vec<_> = got
            .participants
            .iter()
            .map(|p| p.display_name.clone())
            .collect();
        names.sort();
        assert_eq!(names, vec!["matheo".to_string(), "momo".to_string()]);
        let momo = got
            .participants
            .iter()
            .find(|p| p.display_name == "momo")
            .unwrap();
        assert_eq!(momo.result, "win");
        assert_eq!(momo.opponent_id, "B");

        let _ = std::fs::remove_file(&path);
    }

    #[test]
    fn record_is_idempotent() {
        let path = tmp_db("idem");
        let store = RecordStore::open(&path).unwrap();
        store.record(&sample()).unwrap();
        store.record(&sample()).unwrap(); // zweiter Report → kein Duplikat
        let got = store.get("rift-1", 0).unwrap().unwrap();
        assert_eq!(got.participants.len(), 2, "kein Duplikat erwartet");
        let _ = std::fs::remove_file(&path);
    }

    #[test]
    fn rematch_is_separate_record() {
        let path = tmp_db("rematch");
        let store = RecordStore::open(&path).unwrap();
        store.record(&sample()).unwrap();

        let mut r = sample();
        r.rematch = 1;
        r.winner_player = "matheo".into();
        store.record(&r).unwrap();

        assert_eq!(
            store.get("rift-1", 0).unwrap().unwrap().winner_player,
            "momo"
        );
        assert_eq!(
            store.get("rift-1", 1).unwrap().unwrap().winner_player,
            "matheo"
        );
        let _ = std::fs::remove_file(&path);
    }

    #[test]
    fn rfc3339_formats_epoch_correctly() {
        assert_eq!(rfc3339_utc(0), "1970-01-01T00:00:00Z");
        assert_eq!(rfc3339_utc(1_700_000_000_000), "2023-11-14T22:13:20Z");
    }
}
