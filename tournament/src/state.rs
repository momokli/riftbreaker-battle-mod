//! RIFT BATTLE — Match-State-Machine (pure logic, kein I/O).
//!
//! Lebenszyklus einer Partie (Referee-Sicht):
//!
//! ```text
//!   Lobby ──(beide Spieler registriert)──────────► Lobby
//!   Lobby ──(beide Welten ready + AUTO_GO=off)───► Ready  (GO steht aus)
//!   Lobby ──(start_match: beide registriert)─────► Running (Runde 1, GO)
//!   Ready ──(start_match / AUTO_GO=on bei 2. Ready) ► Running (Runde 1, GO)
//!   Running ──(HQ einer Welt ≤ 0)────────────────► Finished (winner = Gegner)
//!   Finished ──(rematch)─────────────────────────► Lobby   (Rematch-Zähler +1)
//! ```
//!
//! `ready()` markiert NUR die Bereitschaft — das Starten (GO) entscheidet der
//! HTTP-Layer (AUTO_GO beim zweiten Ready, sonst wartet die Phase `Ready` auf
//! ein manuelles `POST /go`). So bleibt die State-Machine ohne Konfiguration.
//!
//! Runden-Loop während `Running`: Jede Welt meldet ihren Wellenstart
//! (`wave_start`, inkl. gebautem Wert). Die beim Referee in dieser Runde
//! eingegangenen Sends (Routing via `route_send`) werden beim Wellenstart der
//! Ziel-Welt aus deren Queue gedraint und erscheinen im `Reveal`-Block von
//! `/state`. Wenn BEIDE Welten ihren Wellenstart der Runde R gemeldet haben,
//! ist der Reveal vollständig und der Referee zählt auf Runde R+1 hoch.
//!
//! Der Modul ist bewusst I/O-frei: Alle Mutationen laufen deterministisch
//! unter einem `RwLock<MatchState>` im HTTP-Layer.

use crate::records::{MatchRecord, Participant};
use serde::Serialize;
use std::collections::{BTreeMap, VecDeque};
use std::fmt;
use std::time::{SystemTime, UNIX_EPOCH};

/// Die zwei Welten (Teams) eines Matches.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize)]
#[serde(rename_all = "UPPERCASE")]
pub enum World {
    A,
    B,
}

impl World {
    pub const ALL: [World; 2] = [World::A, World::B];

    pub fn opponent(self) -> World {
        match self {
            World::A => World::B,
            World::B => World::A,
        }
    }

    pub fn as_str(self) -> &'static str {
        match self {
            World::A => "A",
            World::B => "B",
        }
    }
}

impl fmt::Display for World {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}

impl std::str::FromStr for World {
    type Err = String;

    fn from_str(s: &str) -> Result<Self, Self::Err> {
        match s.trim().to_ascii_uppercase().as_str() {
            "A" => Ok(World::A),
            "B" => Ok(World::B),
            _ => Err(format!("ungültige Welt '{s}' — erwartet 'A' oder 'B'")),
        }
    }
}

/// Match-Modus. Serialisiert als lowercase-String (duel|sp).
///
/// `Sp` = Solo-/SP-Mode (Issue #44): Der Mod läuft nur auf dem Server, ein
/// einzelner Spieler (P1, Welt A) tritt gegen eine serverseitig erzeugte
/// Spiegel-Seite (MIRROR, Welt B) an — man duelliert sich gegen sich selbst.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Mode {
    Duel,
    Sp,
}

impl Mode {
    pub fn as_str(self) -> &'static str {
        match self {
            Mode::Duel => "duel",
            Mode::Sp => "sp",
        }
    }
}

impl fmt::Display for Mode {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}

impl std::str::FromStr for Mode {
    type Err = String;

    fn from_str(s: &str) -> Result<Self, Self::Err> {
        match s.trim().to_ascii_lowercase().as_str() {
            "duel" => Ok(Mode::Duel),
            "sp" | "solo" => Ok(Mode::Sp),
            _ => Err(format!(
                "ungültiger Modus '{s}' — erwartet 'duel' oder 'sp'"
            )),
        }
    }
}

/// Match-Phase. Serialisiert als lowercase-String (lobby|ready|running|finished).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Phase {
    Lobby,
    Ready,
    Running,
    Finished,
}

impl Phase {
    pub fn as_str(self) -> &'static str {
        match self {
            Phase::Lobby => "lobby",
            Phase::Ready => "ready",
            Phase::Running => "running",
            Phase::Finished => "finished",
        }
    }
}

/// Eine Send-Batch (Welle), die von einer Welt zur anderen geroutet wurde.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct SendBatch {
    /// Welt, von der gesendet wurde.
    pub from: World,
    /// Einheiten-Komposition (frei definiert; `unit` = Kreaturen-Id des Mods).
    /// Leer für wellen-basierte Sends (US2, nur `level`).
    pub units: Vec<UnitSpec>,
    /// Gesendeter Wert in Send-Währung (z. B. Carbonium-Äquivalent).
    pub value: u64,
    /// Difficulty-Level des Wellen-Sends (US2, issue #996). Der Attack-Cycle
    /// kennt keine Unit-Komposition, nur ein Level; `None` für unit-basierte
    /// Sends (SP/direkt). Additiv/optional — fehlt für Alt-Sends.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub level: Option<u32>,
    /// Runde, in der der Send beim Referee einging (Zuordnung beim Drain).
    pub round: u32,
    /// Server-Zeitstempel (ms seit Unix-Epoch).
    pub ts: u64,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct UnitSpec {
    pub unit: String,
    pub count: u32,
}

/// Reveal-Daten eines Wellenstarts (Lock + Aufdeckung).
#[derive(Debug, Clone, PartialEq)]
pub struct Reveal {
    /// Runde, deren Wellenstart aufgedeckt wurde.
    pub round: u32,
    /// Gebaute Werte beider Teams zum Lock (nur gemeldete Werte).
    pub built: BTreeMap<World, u64>,
    /// Eingehende Send-Komposition pro Welt (was bei diesem Wellenstart spawnen durfte).
    pub incoming: BTreeMap<World, Vec<SendBatch>>,
}

/// Zustell-Status des GO-Broadcasts an einen rbbridge-HTTP-Endpoint.
#[derive(Debug, Clone, PartialEq, Serialize, Default)]
pub struct BroadcastStatus {
    pub at: Option<u64>,
    pub ok: bool,
    pub error: Option<String>,
    pub endpoint: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct LogEntry {
    /// Monotone Feed-Sequenz (Cursor für Poll-Bridges, z. B. Telegram-Feed).
    pub seq: u64,
    pub t: u64,
    pub kind: &'static str,
    pub msg: String,
    /// Welt, der das Event zuzuordnen ist (`Some(A|B)`), oder `None` für
    /// globale Einträge (`go`, `reveal`, `match_end`, `rematch`, `sp`).
    /// Additiv/optional: globale Einträge lassen das Feld weg (`world` fehlt).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub world: Option<World>,
}

/// Zustand einer einzelnen Welt (Team).
#[derive(Debug, Clone, PartialEq)]
pub struct TeamState {
    pub player: Option<String>,
    pub ready: bool,
    pub hq_hp: f64,
    /// Letzter `score_update`-Snapshot (send_state-Egress, Issue #13).
    pub score: u64,
    pub resources: BTreeMap<String, u64>,
    pub wave: u32,
    /// Sends, die in die nächste Welle dieser Welt laufen (noch nicht gedraint).
    pub pending: Vec<SendBatch>,
    pub broadcast: BroadcastStatus,
    /// Zustell-Status des letzten Pause-/Resume-Fan-outs an diese Welt
    /// (Spiegel von [`TeamState::broadcast`], #997).
    pub pause_broadcast: BroadcastStatus,
    /// Queue-`match_id` dieser Welt (Issue #1028): optional, additiv. Wird per
    /// `POST /lobby` gesetzt (falls mitgeschickt) und in `GET /state` als
    /// `teams.<W>.match_id` ausgegeben (`null`, wenn ungesetzt). Der Referee
    /// nutzt sie nur als Echo — die Queue zieht das Ergebnis selbst.
    pub queue_match_id: Option<i64>,
}

/// Der komplette Match-Zustand (Referee-Sicht).
#[derive(Debug, Clone)]
pub struct MatchState {
    pub match_id: String,
    pub mode: Mode,
    pub phase: Phase,
    /// Aktuelle Build-Runde (1 nach GO, +1 nach vollständigem Reveal).
    pub round: u32,
    /// Anzahl abgeschlossener Reveals (Runden mit Wellenstart beider Welten).
    pub rounds_done: u32,
    pub rematches: u32,
    pub winner: Option<World>,
    pub started_at: Option<u64>,
    pub hq_hp_start: f64,
    /// Match-weiter Pausen-Zustand (DOM-Freeze-Fan-out, #997). Additiv; die
    /// `phase` bleibt `running` — Pause ist kein Phasen-Übergang.
    pub paused: bool,
    pub teams: [TeamState; 2],
    pub reveal: Option<Reveal>,
    /// Letzte Ereignisse (Terminal-Feed für die Web-UI).
    pub feed: VecDeque<LogEntry>,
    pub feed_cap: usize,
    /// Nächste zu vergebende Feed-Sequenz.
    next_seq: u64,
}

impl MatchState {
    /// Neuer, leerer Match-State (Phase Lobby, keine Spieler).
    pub fn new(hq_hp_start: f64) -> Self {
        MatchState {
            match_id: "rift-1".to_string(),
            mode: Mode::Duel,
            phase: Phase::Lobby,
            round: 0,
            rounds_done: 0,
            rematches: 0,
            winner: None,
            started_at: None,
            hq_hp_start,
            paused: false,
            teams: [TeamState::fresh(hq_hp_start), TeamState::fresh(hq_hp_start)],
            reveal: None,
            feed: VecDeque::new(),
            feed_cap: 60,
            next_seq: 0,
        }
    }

    fn slot(w: World) -> usize {
        match w {
            World::A => 0,
            World::B => 1,
        }
    }

    fn team(&self, w: World) -> &TeamState {
        &self.teams[Self::slot(w)]
    }

    fn team_mut(&mut self, w: World) -> &mut TeamState {
        &mut self.teams[Self::slot(w)]
    }

    /// Globaler Feed-Eintrag (keine Welt-Zuordnung).
    fn log(&mut self, kind: &'static str, msg: impl Into<String>) {
        self.log_world(kind, None, msg);
    }

    /// Welt-getaggter Feed-Eintrag (US1): `world = Some(..)` für Ereignisse
    /// einer Welt, `None` für globale (siehe [`LogEntry::world`]).
    fn log_world(&mut self, kind: &'static str, world: Option<World>, msg: impl Into<String>) {
        if self.feed.len() >= self.feed_cap {
            self.feed.pop_front();
        }
        let seq = self.next_seq;
        self.next_seq += 1;
        self.feed.push_back(LogEntry {
            seq,
            t: now_ms(),
            kind,
            msg: msg.into(),
            world,
        });
    }

    /// Alle Feed-Einträge mit `seq > since` (chronologisch). Cursor für
    /// Poll-Bridges (Telegram-Feed u. a.), damit kein Event verloren geht.
    pub fn feed_since(&self, since: u64) -> Vec<LogEntry> {
        self.feed
            .iter()
            .filter(|e| e.seq > since)
            .cloned()
            .collect()
    }

    /// Höchste bislang vergebene Feed-Sequenz (Cursor-Stand für Bridges).
    pub fn last_seq(&self) -> u64 {
        self.feed.back().map(|e| e.seq).unwrap_or(0)
    }

    pub fn player(&self, w: World) -> Option<&str> {
        self.team(w).player.as_deref()
    }

    pub fn is_ready(&self, w: World) -> bool {
        self.team(w).ready
    }

    pub fn both_ready(&self) -> bool {
        World::ALL.iter().all(|w| self.is_ready(*w))
    }

    pub fn hq_hp_of(&self, w: World) -> f64 {
        self.team(w).hq_hp
    }

    /// POST /lobby — Spieler registrieren (idempotent; Namenswechsel setzt ready zurück).
    pub fn lobby_register(
        &mut self,
        world: World,
        player: &str,
    ) -> Result<RegisterEffect, StateError> {
        self.lobby_register_with_match_id(world, player, None)
    }

    /// Wie [`MatchState::lobby_register`], zusaetzlich mit optionaler Queue-
    /// `match_id` (Issue #1028). `match_id` ist rein additiv: fehlt sie (`None`),
    /// bleibt ein bereits gesetzter Wert unveraendert; ein Namenswechsel setzt
    /// weiterhin nur `ready` zurueck.
    pub fn lobby_register_with_match_id(
        &mut self,
        world: World,
        player: &str,
        match_id: Option<i64>,
    ) -> Result<RegisterEffect, StateError> {
        let player = player.trim();
        if player.is_empty() {
            return Err(StateError::new(
                "invalid",
                "player-Name darf nicht leer sein",
            ));
        }
        if player.chars().count() > 32 {
            return Err(StateError::new("invalid", "player-Name max. 32 Zeichen"));
        }
        if self.phase != Phase::Lobby {
            return Err(StateError::new(
                "conflict",
                format!(
                    "Registrierung nur in der Lobby (Phase: {})",
                    self.phase.as_str()
                ),
            ));
        }
        let team = self.team_mut(world);
        let created = team.player.is_none();
        if let Some(old) = &team.player {
            if old != player {
                team.ready = false; // Namenswechsel → neu bestätigen
            }
        }
        team.player = Some(player.to_string());
        if match_id.is_some() {
            team.queue_match_id = match_id;
        }
        let match_complete = World::ALL.iter().all(|w| self.player(*w).is_some());
        self.log_world(
            "register",
            Some(world),
            format!(
                "Spieler '{player}' registriert für Welt {world} ({})",
                if created { "Anlage" } else { "Update" }
            ),
        );
        Ok(RegisterEffect {
            created,
            match_complete,
        })
    }

    /// POST /ready — Welt meldet sich bereit (kein Auto-Start hier, siehe Modul-Doku).
    pub fn ready(&mut self, world: World) -> Result<ReadyEffect, StateError> {
        if self.player(world).is_none() {
            return Err(StateError::new(
                "not_found",
                format!("Welt {world} ist nicht registriert — erst POST /lobby"),
            ));
        }
        match self.phase {
            Phase::Lobby | Phase::Ready => {}
            _ => {
                return Err(StateError::new(
                    "conflict",
                    format!(
                        "Ready nur in Lobby/Ready möglich (Phase: {})",
                        self.phase.as_str()
                    ),
                ))
            }
        }
        if self.is_ready(world) {
            return Ok(ReadyEffect::AlreadyReady);
        }
        self.team_mut(world).ready = true;
        self.log_world("ready", Some(world), format!("Welt {world} ist bereit"));
        Ok(if self.both_ready() {
            ReadyEffect::BothReady
        } else {
            ReadyEffect::Waiting
        })
    }

    /// Lobby → Ready (beide Welten ready, GO steht aus). Idempotent.
    pub fn arm_go(&mut self) -> Result<(), StateError> {
        if self.phase == Phase::Ready {
            return Ok(());
        }
        if self.phase != Phase::Lobby {
            return Err(StateError::new(
                "conflict",
                format!("arm_go nur aus der Lobby (Phase: {})", self.phase.as_str()),
            ));
        }
        if !self.both_ready() {
            return Err(StateError::new(
                "conflict",
                "beide Welten müssen ready sein, bevor GO möglich ist",
            ));
        }
        self.phase = Phase::Ready;
        self.log("ready", "Ready-Check komplett — GO steht aus");
        Ok(())
    }

    /// Start (GO): Phase Lobby (Direkt-GO) oder Ready → Running, Runde 1.
    /// Voraussetzung: beide Welten registriert.
    pub fn start_match(&mut self) -> Result<StartEffect, StateError> {
        for w in World::ALL {
            if self.player(w).is_none() {
                return Err(StateError::new(
                    "conflict",
                    format!("Welt {w} ist nicht registriert — Match kann nicht starten"),
                ));
            }
        }
        match self.phase {
            Phase::Lobby => self.log("go", "GO (manuell, ohne Ready-Check)"),
            Phase::Ready => self.log("go", "GO — beide Welten starten den Runden-Loop"),
            _ => {
                return Err(StateError::new(
                    "conflict",
                    format!("Match läuft bereits (Phase: {})", self.phase.as_str()),
                ))
            }
        }
        self.phase = Phase::Running;
        self.round = 1;
        self.winner = None;
        self.reveal = None;
        self.paused = false;
        self.started_at = Some(now_ms());
        for w in World::ALL {
            let team = self.team_mut(w);
            team.pending.clear();
            team.broadcast = BroadcastStatus::default();
            team.pause_broadcast = BroadcastStatus::default();
        }
        self.log("go", "Runde 1 beginnt");
        Ok(StartEffect { started: true })
    }

    /// POST /sp — SP-Mode starten (Issue #44): Ein Spieler (P1, Welt A) gegen
    /// eine serverseitig erzeugte Spiegel-Seite (MIRROR, Welt B). Kein zweiter
    /// Client nötig — der Server ist der Gegner (man duelliert sich selbst).
    pub fn start_sp(&mut self, player: &str) -> Result<StartEffect, StateError> {
        let player = player.trim();
        if player.is_empty() {
            return Err(StateError::new(
                "invalid",
                "player-Name darf nicht leer sein",
            ));
        }
        if player.chars().count() > 32 {
            return Err(StateError::new("invalid", "player-Name max. 32 Zeichen"));
        }
        if self.phase == Phase::Running {
            return Err(StateError::new(
                "conflict",
                format!("Match läuft bereits (Phase: {})", self.phase.as_str()),
            ));
        }

        // Sauberer SP-Ausgangszustand (verwirft ready-/Rematch-Stände).
        let hq = self.hq_hp_start;
        self.mode = Mode::Sp;
        self.phase = Phase::Lobby;
        self.round = 0;
        self.rounds_done = 0;
        self.winner = None;
        self.reveal = None;
        self.paused = false;
        self.started_at = None;
        self.teams = [TeamState::fresh(hq), TeamState::fresh(hq)];
        self.teams[Self::slot(World::A)].player = Some(player.to_string());
        self.teams[Self::slot(World::B)].player = Some("MIRROR".to_string());
        self.teams[Self::slot(World::A)].ready = true;
        self.teams[Self::slot(World::B)].ready = true;

        self.log(
            "sp",
            format!("SP-Mode gestartet: '{player}' (P1) vs MIRROR (Server-Spiegel)"),
        );

        self.phase = Phase::Running;
        self.round = 1;
        self.started_at = Some(now_ms());
        self.log("go", "Runde 1 beginnt (SP: P1 vs MIRROR)");
        Ok(StartEffect { started: true })
    }

    /// POST /send — Send von `from` → Gegner-Welt (Wave-Routing), Runde wird zugestempelt.
    pub fn route_send(
        &mut self,
        from: World,
        units: Vec<UnitSpec>,
        value: u64,
    ) -> Result<SendBatch, StateError> {
        if self.phase != Phase::Running {
            return Err(StateError::new(
                "conflict",
                format!(
                    "Sends nur während des Matches (Phase: {})",
                    self.phase.as_str()
                ),
            ));
        }
        if self.player(from).is_none() {
            return Err(StateError::new(
                "not_found",
                format!("Welt {from} ist nicht registriert"),
            ));
        }
        if units.is_empty() {
            return Err(StateError::new("invalid", "units darf nicht leer sein"));
        }
        let mut clean = Vec::with_capacity(units.len());
        let mut total_units = 0u64;
        for u in units {
            let unit = u.unit.trim().to_string();
            if unit.is_empty() {
                return Err(StateError::new("invalid", "unit-Name darf nicht leer sein"));
            }
            if unit.chars().count() > 64 {
                return Err(StateError::new("invalid", "unit-Name max. 64 Zeichen"));
            }
            if u.count == 0 {
                return Err(StateError::new(
                    "invalid",
                    format!("count für '{unit}' muss ≥ 1 sein"),
                ));
            }
            total_units += u64::from(u.count);
            clean.push(UnitSpec {
                unit,
                count: u.count,
            });
        }
        let to = from.opponent();
        let batch = SendBatch {
            from,
            units: clean,
            value,
            level: None,
            round: self.round,
            ts: now_ms(),
        };
        self.team_mut(to).pending.push(batch.clone());
        self.log_world(
            "send",
            Some(from),
            format!(
                "Send {from} → {to}: {total_units} Einheiten, Wert {value} (Runde {})",
                self.round
            ),
        );
        // SP-Mode: P1-Sends werden gespiegelt — derselbe Send kommt als
        // Gegner-Seite (MIRROR) zurück zu P1 (man duelliert sich gegen sich selbst).
        if self.mode == Mode::Sp && from == World::A {
            let mirror = SendBatch {
                from: World::B,
                units: batch.units.clone(),
                value,
                level: None,
                round: batch.round,
                ts: batch.ts,
            };
            self.team_mut(World::A).pending.push(mirror);
            self.log_world(
                "send",
                Some(World::B),
                format!(
                    "MIRROR (Spiegel) → P1: {total_units} Einheiten, Wert {value} (Runde {})",
                    self.round
                ),
            );
        }
        Ok(batch)
    }

    /// POST /send mit `level` — wellen-basierter Cross-World-Send (US2, #996).
    ///
    /// Der Attack-Cycle kennt keine Unit-Komposition, nur ein
    /// Difficulty-Level; deshalb trägt dieser Send ein `level` (statt `units`).
    /// Er landet in der `pending`-Queue der **Gegner**-Welt und wird bei deren
    /// nächstem `wave_start` gedraint (Reveal + Ingress-Push, US4).
    ///
    /// Validierung: `level >= 1`, nur in `Phase::Running`, sendende Welt muss
    /// registriert sein (`invalid`/`conflict`/`not_found` wie bei `route_send`).
    /// Der SP-Mirror bleibt unberührt: ein Level-Send spiegelt **nicht** doppelt
    /// (SP braucht keinen Level-Send).
    pub fn route_wave_send(
        &mut self,
        from: World,
        level: u32,
        value: u64,
    ) -> Result<SendBatch, StateError> {
        if self.phase != Phase::Running {
            return Err(StateError::new(
                "conflict",
                format!(
                    "Sends nur während des Matches (Phase: {})",
                    self.phase.as_str()
                ),
            ));
        }
        if self.player(from).is_none() {
            return Err(StateError::new(
                "not_found",
                format!("Welt {from} ist nicht registriert"),
            ));
        }
        if level < 1 {
            return Err(StateError::new(
                "invalid",
                format!("level muss ≥ 1 sein (war {level})"),
            ));
        }
        let to = from.opponent();
        let batch = SendBatch {
            from,
            units: Vec::new(),
            value,
            level: Some(level),
            round: self.round,
            ts: now_ms(),
        };
        self.team_mut(to).pending.push(batch.clone());
        self.log_world(
            "send",
            Some(from),
            format!(
                "Send {from} → {to}: Level {level}, Wert {value} (Runde {})",
                self.round
            ),
        );
        Ok(batch)
    }

    pub fn wave_start(
        &mut self,
        world: World,
        built_value: Option<u64>,
    ) -> Result<WaveEffect, StateError> {
        if self.phase != Phase::Running {
            return Err(StateError::new(
                "conflict",
                format!(
                    "wave_start nur während des Matches (Phase: {})",
                    self.phase.as_str()
                ),
            ));
        }
        if self.player(world).is_none() {
            return Err(StateError::new(
                "not_found",
                format!("Welt {world} ist nicht registriert"),
            ));
        }
        let round = self.round;
        // Retry/Duplikat: Welt hat den Lock dieser Runde schon gemeldet.
        if let Some(r) = self.reveal.as_ref() {
            if r.round == round && r.incoming.contains_key(&world) {
                return Ok(WaveEffect::Duplicate);
            }
        }

        // SP-Mode: Die Gegner-Seite (MIRROR, Welt B) wird serverseitig erzeugt und
        // mitgelockt, sobald P1 (Welt A) seinen Wellenstart meldet. Ein expliziter
        // Wellenstart von B ist im SP-Mode nicht vorgesehen.
        if self.mode == Mode::Sp && world == World::B {
            return Err(StateError::new(
                "conflict",
                "SP-Mode: MIRROR (B) wird serverseitig gelockt — nur P1 (A) meldet wave_start",
            ));
        }
        let lock_worlds: Vec<World> = if self.mode == Mode::Sp && world == World::A {
            vec![World::A, World::B]
        } else {
            vec![world]
        };

        let mut reveal = match self.reveal.take() {
            Some(r) if r.round == round => r,
            _ => Reveal {
                round,
                built: BTreeMap::new(),
                incoming: BTreeMap::new(),
            },
        };

        for w in &lock_worlds {
            let pending = std::mem::take(&mut self.team_mut(*w).pending);
            // Batchs dieser (oder früherer) Runden werden gedraint; später gestempelte
            // bleiben für kommende Wellen stehen (defensiv — siehe Invariante: Stempel
            // ist immer die aktuelle Runde, die erst nach beiden Locks hochzählt).
            let (drained, remaining): (Vec<SendBatch>, Vec<SendBatch>) =
                pending.into_iter().partition(|b| b.round <= round);
            let drained_count = drained.len();
            self.team_mut(*w).pending = remaining;
            // Im SP-Mode spiegelt MIRROR (B) den Built-Value von P1 (gleiche Seite).
            if let Some(bv) = built_value {
                reveal.built.insert(*w, bv);
            }
            reveal.incoming.insert(*w, drained);
            self.log_world(
                "wave",
                Some(*w),
                format!("Welt {w}: Wellenstart Runde {round} — {drained_count} Send(s) aufgedeckt"),
            );
        }

        let complete = World::ALL.iter().all(|w| reveal.incoming.contains_key(w));
        self.reveal = Some(reveal);

        // Beide Welten gelockt? → Reveal komplett, nächste Runde beginnt.
        if complete {
            let next_round = round + 1;
            self.rounds_done = round;
            self.round = next_round;
            self.log(
                "reveal",
                format!("Reveal Runde {round} vollständig — Runde {next_round} beginnt"),
            );
        }
        Ok(WaveEffect::Locked)
    }

    /// POST /report — HQ-HP-Meldung (absolut). Bei ≤ 0 endet das Match.
    pub fn report_hq(&mut self, world: World, hp: f64) -> Result<HqEffect, StateError> {
        if self.phase != Phase::Running {
            return Err(StateError::new(
                "conflict",
                format!(
                    "HQ-Meldung nur während des Matches (Phase: {})",
                    self.phase.as_str()
                ),
            ));
        }
        if self.player(world).is_none() {
            return Err(StateError::new(
                "not_found",
                format!("Welt {world} ist nicht registriert"),
            ));
        }
        if !hp.is_finite() || !(0.0..=1_000_000.0).contains(&hp) {
            return Err(StateError::new(
                "invalid",
                format!("ungültiger HQ-HP-Wert: {hp}"),
            ));
        }
        let before = self.hq_hp_of(world);
        let hp = hp.min(self.hq_hp_start); // heilen über Startwert nicht erlaubt
        self.team_mut(world).hq_hp = hp;
        // SP-Mode: MIRROR (B) spiegelt die HQ-HP von P1 (A) — es gibt nur EIN
        // reales HQ, die Gegner-Seite ist die Spiegelung des Spielers.
        if self.mode == Mode::Sp && world == World::A {
            self.team_mut(World::B).hq_hp = hp;
        }
        self.log_world(
            "hq",
            Some(world),
            format!("Welt {world}: HQ-HP {before:.0} → {hp:.0}"),
        );
        if hp <= 0.0 {
            let winner = world.opponent();
            self.winner = Some(winner);
            self.phase = Phase::Finished;
            self.log_world(
                "finish",
                Some(world),
                format!("HQ von Welt {world} zerstört — Sieger: Welt {winner}"),
            );
            // Match-Ende-Hinweis (Log + Telegram + UI): nächster Spieler kann joinen.
            self.log("match_end", "Match beendet — nächster Spieler kann joinen");
            return Ok(HqEffect { match_over: true });
        }
        Ok(HqEffect { match_over: false })
    }

    /// POST /report — score_update (send_state-Egress, Issue #13): periodischer
    /// State-Snapshot (Score, Ressourcen, Wave) einer Welt. Idempotent; der
    /// Feed wird nur bei Score-/Wave-Änderung belastet.
    pub fn score_update(
        &mut self,
        world: World,
        score: u64,
        resources: BTreeMap<String, u64>,
        wave: u32,
    ) -> Result<ScoreEffect, StateError> {
        if self.player(world).is_none() {
            return Err(StateError::new(
                "not_found",
                format!("Welt {world} ist nicht registriert"),
            ));
        }
        let changed;
        {
            let team = self.team_mut(world);
            changed = team.score != score || team.wave != wave;
            team.score = score;
            team.resources = resources;
            team.wave = wave;
        }
        if changed {
            self.log_world(
                "score",
                Some(world),
                format!("Welt {world}: Score {score}, Wave {wave}"),
            );
        }
        Ok(ScoreEffect { changed })
    }

    /// POST /rematch — Reset in die Lobby (Spieler bleiben registriert).
    pub fn rematch(&mut self) -> Result<(), StateError> {
        if self.phase == Phase::Running {
            return Err(StateError::new(
                "conflict",
                "Rematch während eines laufenden Matches nicht möglich (erst HQ-Tod)",
            ));
        }
        let rematch_no = self.rematches + 1;
        let hq = self.hq_hp_start;
        self.phase = Phase::Lobby;
        self.round = 0;
        self.rounds_done = 0;
        self.winner = None;
        self.reveal = None;
        self.paused = false;
        self.started_at = None;
        self.rematches = rematch_no;
        for w in World::ALL {
            let team = self.team_mut(w);
            team.ready = false;
            team.hq_hp = hq;
            team.score = 0;
            team.resources.clear();
            team.wave = 0;
            team.pending.clear();
            team.broadcast = BroadcastStatus::default();
            team.pause_broadcast = BroadcastStatus::default();
        }
        self.log(
            "rematch",
            format!("Rematch #{rematch_no} — zurück in die Lobby"),
        );
        Ok(())
    }

    /// POST /pause — Match-weiter DOM-Freeze (Pause-Fan-out, #997).
    ///
    /// Nur in `Phase::Running` erlaubt (sonst `conflict`). Setzt `paused=true`
    /// und schreibt ein globales Feed-Event `kind="pause"`. Der Doppelaufruf
    /// ist idempotent: bereits pausiert → [`PauseEffect::AlreadyPaused`] (kein
    /// zweites Feed-Event; der HTTP-Layer entscheidet, ob er trotzdem erneut
    /// fächert — `retry`).
    pub fn pause(&mut self) -> Result<PauseEffect, StateError> {
        if self.phase != Phase::Running {
            return Err(StateError::new(
                "conflict",
                format!(
                    "Pause nur während des Matches (Phase: {})",
                    self.phase.as_str()
                ),
            ));
        }
        if self.paused {
            return Ok(PauseEffect::AlreadyPaused);
        }
        self.paused = true;
        self.log(
            "pause",
            "Match pausiert — DOM-Freeze an beide Welten gefächert",
        );
        Ok(PauseEffect::Applied)
    }

    /// POST /resume — Match-weiter DOM-Resume (Pause-Fan-out, #997).
    ///
    /// Nur in `Phase::Running` erlaubt (sonst `conflict`). Läuft das Match
    /// bereits (nicht pausiert) → [`ResumeEffect::AlreadyRunning`] (kein
    /// Doppel-Feed). Sonst `paused=false` + globales Feed-Event `kind="resume"`.
    pub fn resume(&mut self) -> Result<ResumeEffect, StateError> {
        if self.phase != Phase::Running {
            return Err(StateError::new(
                "conflict",
                format!(
                    "Resume nur während des Matches (Phase: {})",
                    self.phase.as_str()
                ),
            ));
        }
        if !self.paused {
            return Ok(ResumeEffect::AlreadyRunning);
        }
        self.paused = false;
        self.log(
            "resume",
            "Match fortgesetzt — Resume an beide Welten gefächert",
        );
        Ok(ResumeEffect::Applied)
    }

    /// Broadcast-Ergebnis je Welt festhalten (für /state + UI).
    pub fn record_broadcast(
        &mut self,
        world: World,
        ok: bool,
        error: Option<String>,
        endpoint: Option<String>,
    ) {
        self.team_mut(world).broadcast = BroadcastStatus {
            at: Some(now_ms()),
            ok,
            error,
            endpoint,
        };
    }

    /// Zustell-Ergebnis des Pause-/Resume-Fan-outs je Welt festhalten
    /// (Spiegel von [`MatchState::record_broadcast`], #997).
    pub fn record_pause_broadcast(
        &mut self,
        world: World,
        ok: bool,
        error: Option<String>,
        endpoint: Option<String>,
    ) {
        self.team_mut(world).pause_broadcast = BroadcastStatus {
            at: Some(now_ms()),
            ok,
            error,
            endpoint,
        };
    }

    /// Feed-Eintrag für einen Operator-Wellen-Spawn (`POST /wave`, Issue #266).
    ///
    /// `ok` = **Zustell-Erfolg** (Bridge/Relay antwortete HTTP 2xx, kein
    /// Transportfehler) — nicht der Ausführ-Erfolg; den liefert die
    /// `/wave`-Antwort als `exec_ok` aus dem `exec_result`. Das
    /// Gegenstück im Spiel-Log ist `[RBBATTLE] event=wave level=<n> status=start`
    /// (Mod) — nur mit laufendem Spiel verifizierbar (Player-Test offen).
    pub fn log_wave(
        &mut self,
        world: World,
        command: &str,
        ok: bool,
        status: Option<u16>,
        error: Option<&str>,
    ) {
        let msg = if ok {
            format!(
                "Welt {world}: {command} — zugestellt (HTTP {})",
                status
                    .map(|s| s.to_string())
                    .unwrap_or_else(|| "?".to_string())
            )
        } else {
            format!(
                "Welt {world}: {command} — fehlgeschlagen: {}",
                error.unwrap_or("unbekannt")
            )
        };
        self.log_world("wave", Some(world), msg);
    }

    /// Baut den persistenten Match-Record aus dem Endzustand (Issue #999).
    ///
    /// Pure: nur `Phase::Finished` liefert `Some`; Teilnehmer kommen aus
    /// `teams`, Sieger aus `winner`, der Rematch-Zähler ist Teil des Keys.
    /// Kein I/O — das Schreiben übernimmt der HTTP-Layer ([`crate::records`]).
    pub fn to_record(&self, finished_at: u64) -> Option<MatchRecord> {
        if self.phase != Phase::Finished {
            return None;
        }
        let winner = self.winner?;
        let mut participants = Vec::new();
        for w in World::ALL {
            let Some(name) = self.player(w) else { continue };
            participants.push(Participant {
                player_id: name.to_string(),
                display_name: name.to_string(),
                // Identität #992 ist heute der freie Textname.
                identity_source: "name".to_string(),
                opponent_id: w.opponent().as_str().to_string(),
                result: if w == winner { "win" } else { "loss" }.to_string(),
            });
        }
        let winner_player = self.player(winner).unwrap_or_default().to_string();
        Some(MatchRecord {
            match_id: self.match_id.clone(),
            rematch: self.rematches,
            mode: self.mode.as_str().to_string(),
            rounds_done: self.rounds_done,
            winner_player,
            finished_at: crate::records::rfc3339_utc(finished_at),
            participants,
        })
    }

    /// Öffentliche Sicht auf den Zustand (wird als `/state` serialisiert).
    pub fn view(&self) -> StateView {
        let mut teams = BTreeMap::new();
        for w in World::ALL {
            let t = self.team(w);
            teams.insert(
                w.as_str().to_string(),
                TeamView {
                    player: t.player.clone(),
                    ready: t.ready,
                    hq_hp: t.hq_hp,
                    score: t.score,
                    resources: t.resources.clone(),
                    wave: t.wave,
                    pending_sends: t.pending.clone(),
                    go_broadcast: t.broadcast.clone(),
                    pause_broadcast: t.pause_broadcast.clone(),
                    match_id: t.queue_match_id,
                },
            );
        }
        let reveal = self.reveal.as_ref().map(|r| RevealView {
            round: r.round,
            built: r
                .built
                .iter()
                .map(|(w, v)| (w.as_str().to_string(), *v))
                .collect(),
            incoming: r
                .incoming
                .iter()
                .map(|(w, b)| (w.as_str().to_string(), b.clone()))
                .collect(),
        });
        let feed: Vec<LogEntry> = self.feed.iter().rev().take(30).cloned().collect();
        StateView {
            match_id: self.match_id.clone(),
            mode: self.mode.as_str().to_string(),
            phase: self.phase.as_str().to_string(),
            round: self.round,
            rounds_done: self.rounds_done,
            rematches: self.rematches,
            winner: self.winner.map(|w| w.as_str().to_string()),
            started_at: self.started_at,
            hq_hp_start: self.hq_hp_start,
            paused: self.paused,
            teams,
            reveal,
            feed,
        }
    }
}

impl TeamState {
    fn fresh(hq_hp_start: f64) -> Self {
        TeamState {
            player: None,
            ready: false,
            hq_hp: hq_hp_start,
            score: 0,
            resources: BTreeMap::new(),
            wave: 0,
            pending: Vec::new(),
            broadcast: BroadcastStatus::default(),
            pause_broadcast: BroadcastStatus::default(),
            queue_match_id: None,
        }
    }
}

// ---- Öffentliche View-Strukturen (Serialisierung von /state) ----

#[derive(Debug, Clone, Serialize)]
pub struct StateView {
    pub match_id: String,
    pub mode: String,
    pub phase: String,
    pub round: u32,
    pub rounds_done: u32,
    pub rematches: u32,
    pub winner: Option<String>,
    pub started_at: Option<u64>,
    pub hq_hp_start: f64,
    /// Match-weiter Pausen-Zustand (#997) — additiv, `phase` bleibt unberührt.
    pub paused: bool,
    pub teams: BTreeMap<String, TeamView>,
    pub reveal: Option<RevealView>,
    pub feed: Vec<LogEntry>,
}

#[derive(Debug, Clone, Serialize)]
pub struct TeamView {
    pub player: Option<String>,
    pub ready: bool,
    pub hq_hp: f64,
    pub score: u64,
    pub resources: BTreeMap<String, u64>,
    pub wave: u32,
    pub pending_sends: Vec<SendBatch>,
    pub go_broadcast: BroadcastStatus,
    /// Zustell-Status des letzten Pause-/Resume-Fan-outs (#997).
    pub pause_broadcast: BroadcastStatus,
    /// Queue-`match_id` dieser Welt (Issue #1028, additiv) — `null` wenn ungesetzt.
    pub match_id: Option<i64>,
}

#[derive(Debug, Clone, Serialize)]
pub struct RevealView {
    pub round: u32,
    pub built: BTreeMap<String, u64>,
    pub incoming: BTreeMap<String, Vec<SendBatch>>,
}

// ---- Fehler ----

#[derive(Debug, Clone, PartialEq)]
pub struct StateError {
    pub code: &'static str,
    pub message: String,
}

impl StateError {
    pub(crate) fn new(code: &'static str, message: impl Into<String>) -> Self {
        StateError {
            code,
            message: message.into(),
        }
    }

    /// Abbildung auf HTTP-Status (400/404/409).
    pub fn http_status(&self) -> axum::http::StatusCode {
        match self.code {
            "conflict" => axum::http::StatusCode::CONFLICT,
            "not_found" => axum::http::StatusCode::NOT_FOUND,
            _ => axum::http::StatusCode::BAD_REQUEST,
        }
    }
}

impl fmt::Display for StateError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}: {}", self.code, self.message)
    }
}

impl std::error::Error for StateError {}

// ---- Effekte ----

#[derive(Debug, Clone, PartialEq)]
pub struct RegisterEffect {
    pub created: bool,
    pub match_complete: bool,
}

#[derive(Debug, Clone, PartialEq)]
pub enum ReadyEffect {
    Waiting,
    AlreadyReady,
    BothReady,
}

#[derive(Debug, Clone, PartialEq)]
pub struct StartEffect {
    pub started: bool,
}

/// Ergebnis eines `pause()`-Aufrufs (#997).
#[derive(Debug, Clone, PartialEq)]
pub enum PauseEffect {
    /// Match war nicht pausiert — jetzt pausiert (Fan-out senden).
    Applied,
    /// Match war bereits pausiert — idempotent, kein zweites Feed-Event.
    AlreadyPaused,
}

/// Ergebnis eines `resume()`-Aufrufs (#997).
#[derive(Debug, Clone, PartialEq)]
pub enum ResumeEffect {
    /// Match war pausiert — jetzt fortgesetzt (Fan-out senden).
    Applied,
    /// Match lief bereits (nicht pausiert) — idempotent, kein Doppel-Feed.
    AlreadyRunning,
}

#[derive(Debug, Clone, PartialEq)]
pub enum WaveEffect {
    Locked,
    Duplicate,
}

#[derive(Debug, Clone, PartialEq)]
pub struct HqEffect {
    pub match_over: bool,
}

#[derive(Debug, Clone, PartialEq)]
pub struct ScoreEffect {
    pub changed: bool,
}

fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fresh() -> MatchState {
        MatchState::new(100.0)
    }

    fn pending(s: &MatchState, w: World) -> &[SendBatch] {
        &s.teams[MatchState::slot(w)].pending
    }

    fn register_all(s: &mut MatchState) {
        s.lobby_register(World::A, "momo").unwrap();
        s.lobby_register(World::B, "matheo").unwrap();
    }

    /// Standard-Flow: beide registrieren, beide ready, GO.
    fn start(s: &mut MatchState) {
        register_all(s);
        assert_eq!(s.ready(World::A).unwrap(), ReadyEffect::Waiting);
        assert_eq!(s.ready(World::B).unwrap(), ReadyEffect::BothReady);
        s.arm_go().unwrap();
        assert_eq!(s.phase, Phase::Ready);
        s.start_match().unwrap();
        assert_eq!(s.phase, Phase::Running);
        assert_eq!(s.round, 1);
    }

    #[test]
    fn lobby_flow_register_and_validate() {
        let mut s = fresh();
        assert_eq!(s.phase, Phase::Lobby);
        assert!(s.player(World::A).is_none());

        let e = s.lobby_register(World::A, "  momo  ").unwrap();
        assert!(e.created && !e.match_complete);
        assert_eq!(s.player(World::A), Some("momo"));
        assert_eq!(s.player(World::B), None);

        let e2 = s.lobby_register(World::B, "matheo").unwrap();
        assert!(e2.created && e2.match_complete);

        // Namens-Validierung
        assert_eq!(
            s.lobby_register(World::A, "   ").unwrap_err().code,
            "invalid"
        );
        assert_eq!(
            s.lobby_register(World::A, &"x".repeat(40))
                .unwrap_err()
                .code,
            "invalid"
        );
    }

    #[test]
    fn idempotent_register_keeps_ready_but_name_change_resets_it() {
        let mut s = fresh();
        register_all(&mut s);
        s.ready(World::A).unwrap();
        assert!(s.is_ready(World::A));
        let e = s.lobby_register(World::A, "momo").unwrap();
        assert!(!e.created);
        assert!(s.is_ready(World::A)); // gleicher Name → ready bleibt
        s.lobby_register(World::A, "momo2").unwrap();
        assert!(!s.is_ready(World::A)); // Namenswechsel → ready verfällt
    }

    #[test]
    fn register_blocked_outside_lobby() {
        let mut s = fresh();
        register_all(&mut s);
        s.start_match().unwrap(); // Direkt-GO aus Lobby
        assert_eq!(
            s.lobby_register(World::A, "neu").unwrap_err().code,
            "conflict"
        );
    }

    #[test]
    fn ready_check_and_arm_go() {
        let mut s = fresh();
        // ohne Registrierung → 404
        assert_eq!(s.ready(World::A).unwrap_err().code, "not_found");
        register_all(&mut s);
        assert_eq!(s.ready(World::A).unwrap(), ReadyEffect::Waiting);
        assert_eq!(s.ready(World::A).unwrap(), ReadyEffect::AlreadyReady);
        // arm_go vor zweitem Ready → Fehler
        assert_eq!(s.arm_go().unwrap_err().code, "conflict");
        assert_eq!(s.ready(World::B).unwrap(), ReadyEffect::BothReady);
        assert_eq!(s.phase, Phase::Lobby);
        s.arm_go().unwrap();
        assert_eq!(s.phase, Phase::Ready);
        s.arm_go().unwrap(); // idempotent
        assert_eq!(s.phase, Phase::Ready);
    }

    #[test]
    fn ready_blocked_after_start() {
        let mut s = fresh();
        register_all(&mut s);
        s.start_match().unwrap();
        assert_eq!(s.ready(World::A).unwrap_err().code, "conflict");
    }

    #[test]
    fn manual_go_from_lobby_requires_both_players() {
        let mut s = fresh();
        s.lobby_register(World::A, "momo").unwrap();
        // nur ein Spieler → kein Start
        assert_eq!(s.start_match().unwrap_err().code, "conflict");
        s.lobby_register(World::B, "matheo").unwrap();
        let e = s.start_match().unwrap();
        assert!(e.started);
        assert_eq!(s.phase, Phase::Running);
        assert_eq!(s.round, 1);
        assert!(s.started_at.is_some());
        // zweiter Start → Konflikt
        assert_eq!(s.start_match().unwrap_err().code, "conflict");
    }

    #[test]
    fn send_routes_to_opponent_queue_stamped_with_round() {
        let mut s = fresh();
        start(&mut s);
        assert!(pending(&s, World::A).is_empty());

        let batch = s
            .route_send(
                World::A,
                vec![
                    UnitSpec {
                        unit: "creeper".into(),
                        count: 4,
                    },
                    UnitSpec {
                        unit: "brute".into(),
                        count: 2,
                    },
                ],
                1500,
            )
            .unwrap();
        assert_eq!(batch.from, World::A);
        assert_eq!(batch.round, 1);
        assert_eq!(batch.value, 1500);
        assert_eq!(pending(&s, World::B).len(), 1);
        assert_eq!(pending(&s, World::B)[0].units.len(), 2);
        assert!(pending(&s, World::A).is_empty());

        // Send außerhalb Running → conflict
        let mut s2 = fresh();
        register_all(&mut s2);
        assert_eq!(
            s2.route_send(
                World::A,
                vec![UnitSpec {
                    unit: "x".into(),
                    count: 1
                }],
                1
            )
            .unwrap_err()
            .code,
            "conflict"
        );
    }

    #[test]
    fn send_validation() {
        let mut s = fresh();
        start(&mut s);
        assert_eq!(
            s.route_send(World::A, vec![], 10).unwrap_err().code,
            "invalid"
        );
        assert_eq!(
            s.route_send(
                World::A,
                vec![UnitSpec {
                    unit: "x".into(),
                    count: 0
                }],
                10
            )
            .unwrap_err()
            .code,
            "invalid"
        );
        assert_eq!(
            s.route_send(
                World::A,
                vec![UnitSpec {
                    unit: "  ".into(),
                    count: 1
                }],
                10
            )
            .unwrap_err()
            .code,
            "invalid"
        );
        assert_eq!(
            s.route_send(
                World::A,
                vec![
                    UnitSpec {
                        unit: "x".into(),
                        count: 1
                    },
                    UnitSpec {
                        unit: "y".into(),
                        count: 0
                    }
                ],
                10
            )
            .unwrap_err()
            .code,
            "invalid"
        );
        // gültiger Send danach ok
        let ok = s
            .route_send(
                World::A,
                vec![UnitSpec {
                    unit: "x".into(),
                    count: 1,
                }],
                10,
            )
            .unwrap();
        assert_eq!(ok.round, 1);
    }

    #[test]
    fn wave_start_drains_queue_reveal_and_round_advance() {
        let mut s = fresh();
        start(&mut s); // Runde 1
        s.route_send(
            World::A,
            vec![UnitSpec {
                unit: "creeper".into(),
                count: 3,
            }],
            300,
        )
        .unwrap();
        s.route_send(
            World::B,
            vec![UnitSpec {
                unit: "brute".into(),
                count: 1,
            }],
            900,
        )
        .unwrap();
        assert_eq!(pending(&s, World::A).len(), 1);
        assert_eq!(pending(&s, World::B).len(), 1);

        // Wellenstart Welt A (built 5000)
        assert_eq!(
            s.wave_start(World::A, Some(5000)).unwrap(),
            WaveEffect::Locked
        );
        let rev = s.reveal.as_ref().unwrap();
        assert_eq!(rev.round, 1);
        // Incoming von A = Sends, die B geschickt hat
        assert_eq!(rev.incoming[&World::A].len(), 1);
        assert_eq!(rev.incoming[&World::A][0].value, 900);
        assert_eq!(rev.built.get(&World::A), Some(&5000));
        assert!(!rev.built.contains_key(&World::B)); // B hat noch nicht gelockt
                                                     // A's Send liegt weiterhin bei B
        assert_eq!(pending(&s, World::B).len(), 1);
        assert_eq!(s.round, 1); // Runde zählt erst nach beiden Locks hoch

        // Duplikat/Retry VOR Lock der Gegenseite → kein Doppel-Drain, keine Änderung
        assert_eq!(
            s.wave_start(World::A, Some(9999)).unwrap(),
            WaveEffect::Duplicate
        );
        assert_eq!(s.reveal.as_ref().unwrap().built.get(&World::A), Some(&5000)); // unverändert
        assert_eq!(pending(&s, World::B).len(), 1); // kein zweiter Drain von Bs Queue

        // Wellenstart Welt B (built 4200) → komplett
        assert_eq!(
            s.wave_start(World::B, Some(4200)).unwrap(),
            WaveEffect::Locked
        );
        let rev = s.reveal.as_ref().unwrap();
        assert_eq!(rev.built.get(&World::A), Some(&5000));
        assert_eq!(rev.built.get(&World::B), Some(&4200));
        assert_eq!(rev.incoming[&World::B].len(), 1);
        assert_eq!(rev.incoming[&World::B][0].value, 300);
        assert!(pending(&s, World::A).is_empty());
        assert!(pending(&s, World::B).is_empty());
        assert_eq!(s.round, 2);
        assert_eq!(s.rounds_done, 1);
    }

    #[test]
    fn wave_start_empty_incoming_still_marks_lock() {
        let mut s = fresh();
        start(&mut s);
        // beide Welten locken ohne Sends
        assert_eq!(s.wave_start(World::A, None).unwrap(), WaveEffect::Locked);
        assert_eq!(s.wave_start(World::B, None).unwrap(), WaveEffect::Locked);
        assert_eq!(s.round, 2);
        let rev = s.reveal.as_ref().unwrap();
        assert!(rev.incoming[&World::A].is_empty());
        assert!(rev.incoming[&World::B].is_empty());
        assert!(rev.built.is_empty());
    }

    #[test]
    fn sends_straddling_wave_lock_land_in_correct_round() {
        let mut s = fresh();
        start(&mut s);
        // A sendet 100 in Runde 1, dann lockt A (B noch nicht) → Batch bleibt bei B (round 1)
        s.route_send(
            World::A,
            vec![UnitSpec {
                unit: "creeper".into(),
                count: 1,
            }],
            100,
        )
        .unwrap();
        s.wave_start(World::A, None).unwrap();
        assert_eq!(pending(&s, World::B).len(), 1);
        assert_eq!(pending(&s, World::B)[0].round, 1);
        // B lockt Runde 1 → 100er-Batch wird gedraint (incoming B)
        s.wave_start(World::B, None).unwrap();
        assert_eq!(s.round, 2);
        assert_eq!(s.reveal.as_ref().unwrap().incoming[&World::B].len(), 1);
        // A sendet jetzt in Runde 2 → Batch.round == 2; beide Welten locken Runde 2,
        // erst beim Lock von B wird der Runde-2-Batch gedraint und Runde 3 beginnt.
        let b2 = s
            .route_send(
                World::A,
                vec![UnitSpec {
                    unit: "creeper".into(),
                    count: 5,
                }],
                500,
            )
            .unwrap();
        assert_eq!(b2.round, 2);
        s.wave_start(World::A, None).unwrap(); // Lock A für Runde 2 (noch kein Drain bei B)
        assert_eq!(pending(&s, World::B).len(), 1);
        assert_eq!(s.round, 2);
        s.wave_start(World::B, None).unwrap(); // Lock B für Runde 2 → drain + Runde 3
        let rev = s.reveal.as_ref().unwrap();
        assert_eq!(rev.round, 2);
        assert_eq!(rev.incoming[&World::B].len(), 1);
        assert_eq!(rev.incoming[&World::B][0].value, 500);
        assert!(pending(&s, World::B).is_empty());
        assert_eq!(s.round, 3);
    }

    #[test]
    fn hq_damage_reduces_and_zero_ends_match() {
        let mut s = fresh();
        start(&mut s);
        assert_eq!(s.hq_hp_of(World::A), 100.0);

        let e = s.report_hq(World::A, 70.0).unwrap();
        assert!(!e.match_over);
        assert_eq!(s.hq_hp_of(World::A), 70.0);
        assert_eq!(s.phase, Phase::Running);

        // Heilen über Startwert wird gedeckelt
        s.report_hq(World::A, 500.0).unwrap();
        assert_eq!(s.hq_hp_of(World::A), 100.0);

        let e = s.report_hq(World::A, 0.0).unwrap();
        assert!(e.match_over);
        assert_eq!(s.phase, Phase::Finished);
        assert_eq!(s.winner, Some(World::B));
        assert_eq!(s.hq_hp_of(World::A), 0.0);

        // Meldungen nach Match-Ende → conflict
        assert_eq!(s.report_hq(World::B, 50.0).unwrap_err().code, "conflict");
        assert_eq!(
            s.route_send(
                World::B,
                vec![UnitSpec {
                    unit: "x".into(),
                    count: 1
                }],
                1
            )
            .unwrap_err()
            .code,
            "conflict"
        );
        assert_eq!(s.wave_start(World::B, None).unwrap_err().code, "conflict");
    }

    #[test]
    fn hq_validation() {
        let mut s = fresh();
        start(&mut s);
        assert_eq!(s.report_hq(World::A, -5.0).unwrap_err().code, "invalid");
        assert_eq!(s.report_hq(World::A, f64::NAN).unwrap_err().code, "invalid");
        assert_eq!(
            s.report_hq(World::A, 2_000_000.0).unwrap_err().code,
            "invalid"
        );
    }

    #[test]
    fn wave_start_validation() {
        let mut s = fresh();
        register_all(&mut s);
        // vor dem Start → conflict
        assert_eq!(s.wave_start(World::A, None).unwrap_err().code, "conflict");
    }

    #[test]
    fn rematch_resets_but_keeps_players() {
        let mut s = fresh();
        start(&mut s);
        s.route_send(
            World::A,
            vec![UnitSpec {
                unit: "x".into(),
                count: 2,
            }],
            100,
        )
        .unwrap();
        s.wave_start(World::A, None).unwrap();
        s.wave_start(World::B, None).unwrap();
        s.report_hq(World::A, 0.0).unwrap();
        assert_eq!(s.phase, Phase::Finished);
        assert_eq!(s.winner, Some(World::B));

        s.rematch().unwrap();
        assert_eq!(s.phase, Phase::Lobby);
        assert_eq!(s.rematches, 1);
        assert_eq!(s.round, 0);
        assert!(s.winner.is_none());
        assert!(s.reveal.is_none());
        assert_eq!(s.player(World::A), Some("momo")); // Spieler bleiben registriert
        assert_eq!(s.player(World::B), Some("matheo"));
        for w in World::ALL {
            assert!(!s.is_ready(w));
            assert_eq!(s.hq_hp_of(w), 100.0);
            assert!(pending(&s, w).is_empty());
            assert_eq!(s.team(w).broadcast, BroadcastStatus::default());
        }

        // Direkt-GO für Rematch-Match möglich (beide registriert)
        let e = s.start_match().unwrap();
        assert!(e.started);
        assert_eq!(s.phase, Phase::Running);
    }

    #[test]
    fn rematch_blocked_while_running() {
        let mut s = fresh();
        start(&mut s);
        assert_eq!(s.rematch().unwrap_err().code, "conflict");
    }

    #[test]
    fn rematch_from_ready_phase_works() {
        let mut s = fresh();
        register_all(&mut s);
        s.ready(World::A).unwrap();
        s.ready(World::B).unwrap();
        s.arm_go().unwrap();
        assert_eq!(s.phase, Phase::Ready);
        s.rematch().unwrap();
        assert_eq!(s.phase, Phase::Lobby);
    }

    #[test]
    fn full_match_flow_with_reveal_and_rematch() {
        let mut s = fresh();
        // Lobby
        assert_eq!(s.phase, Phase::Lobby);
        register_all(&mut s);
        // Ready-Check
        assert_eq!(s.ready(World::A).unwrap(), ReadyEffect::Waiting);
        assert_eq!(s.ready(World::B).unwrap(), ReadyEffect::BothReady);
        // GO
        s.start_match().unwrap();
        assert_eq!(s.phase, Phase::Running);
        assert_eq!(s.round, 1);
        // Sends + Welle
        s.route_send(
            World::A,
            vec![UnitSpec {
                unit: "spitter".into(),
                count: 6,
            }],
            600,
        )
        .unwrap();
        s.wave_start(World::A, Some(8000)).unwrap();
        s.wave_start(World::B, Some(7500)).unwrap();
        assert_eq!(s.round, 2);
        // HQ-Verlust von A in Runde 2 → Sieger B
        s.report_hq(World::A, 0.0).unwrap();
        assert_eq!(s.phase, Phase::Finished);
        assert_eq!(s.winner, Some(World::B));
        // Rematch
        s.rematch().unwrap();
        assert_eq!(s.phase, Phase::Lobby);
        assert_eq!(s.rematches, 1);
        assert_eq!(s.player(World::A), Some("momo"));
        // Feed dokumentiert den Ablauf
        let kinds: Vec<&str> = s.feed.iter().map(|e| e.kind).collect();
        assert!(kinds.contains(&"go"));
        assert!(kinds.contains(&"finish"));
        assert!(kinds.contains(&"rematch"));
    }

    #[test]
    fn state_view_shape() {
        let mut s = fresh();
        start(&mut s);
        let v = s.view();
        assert_eq!(v.phase, "running");
        assert_eq!(v.round, 1);
        assert_eq!(v.winner, None);
        assert_eq!(v.teams["A"].hq_hp, 100.0);
        assert_eq!(v.teams["B"].player.as_deref(), Some("matheo"));
        assert!(v.teams["A"].go_broadcast.at.is_none());
        // Feed ist neueste-zuerst
        assert_eq!(v.feed[0].kind, "go");
    }

    #[test]
    fn world_parsing_and_display() {
        assert_eq!("A".parse::<World>().unwrap(), World::A);
        assert_eq!("b".parse::<World>().unwrap(), World::B);
        assert_eq!(" B ".parse::<World>().unwrap(), World::B);
        assert!("C".parse::<World>().is_err());
        assert_eq!(World::A.opponent(), World::B);
        assert_eq!(World::B.opponent(), World::A);
        assert_eq!(World::A.as_str(), "A");
        assert_eq!(format!("{}", World::B), "B");
    }

    #[test]
    fn state_error_maps_to_http() {
        use axum::http::StatusCode;
        assert_eq!(
            StateError::new("conflict", "x").http_status(),
            StatusCode::CONFLICT
        );
        assert_eq!(
            StateError::new("not_found", "x").http_status(),
            StatusCode::NOT_FOUND
        );
        assert_eq!(
            StateError::new("invalid", "x").http_status(),
            StatusCode::BAD_REQUEST
        );
    }

    // ---- SP-Mode (Issue #44): Mirror-Konzept, Match-Ende, Feed-Cursor ----

    #[test]
    fn sp_mode_registers_p1_and_mirror() {
        let mut s = fresh();
        let e = s.start_sp("  momo  ").unwrap();
        assert!(e.started);
        assert_eq!(s.mode, Mode::Sp);
        assert_eq!(s.phase, Phase::Running);
        assert_eq!(s.round, 1);
        assert_eq!(s.player(World::A), Some("momo"));
        assert_eq!(s.player(World::B), Some("MIRROR"));
        assert!(s.is_ready(World::A));
        assert!(s.is_ready(World::B));

        let mut s2 = fresh();
        assert_eq!(s2.start_sp("   ").unwrap_err().code, "invalid");
        assert_eq!(s2.start_sp(&"x".repeat(40)).unwrap_err().code, "invalid");
    }

    #[test]
    fn sp_mode_blocked_while_running() {
        let mut s = fresh();
        s.start_sp("momo").unwrap();
        assert_eq!(s.start_sp("matheo").unwrap_err().code, "conflict");
    }

    #[test]
    fn sp_mode_send_mirrors_back() {
        let mut s = fresh();
        s.start_sp("momo").unwrap();
        let batch = s
            .route_send(
                World::A,
                vec![UnitSpec {
                    unit: "creeper".into(),
                    count: 4,
                }],
                400,
            )
            .unwrap();
        assert_eq!(batch.from, World::A);
        // Original-Send landet bei MIRROR (B).
        assert_eq!(pending(&s, World::B).len(), 1);
        // Spiegel-Send kommt zurück zu P1 (A).
        assert_eq!(pending(&s, World::A).len(), 1);
        let mirror = &pending(&s, World::A)[0];
        assert_eq!(mirror.from, World::B);
        assert_eq!(mirror.value, 400);
        assert_eq!(mirror.units.len(), 1);
        assert_eq!(mirror.units[0].unit, "creeper");
    }

    #[test]
    fn sp_mode_wave_start_locks_both_sides() {
        let mut s = fresh();
        s.start_sp("momo").unwrap();
        s.route_send(
            World::A,
            vec![UnitSpec {
                unit: "creeper".into(),
                count: 3,
            }],
            300,
        )
        .unwrap();
        // Nur P1 (A) meldet den Wellenstart → beide Seiten werden gelockt.
        assert_eq!(
            s.wave_start(World::A, Some(5000)).unwrap(),
            WaveEffect::Locked
        );
        let rev = s.reveal.as_ref().unwrap();
        assert_eq!(rev.round, 1);
        assert_eq!(rev.built.get(&World::A), Some(&5000));
        assert_eq!(rev.built.get(&World::B), Some(&5000)); // MIRROR spiegelt Built-Value
        assert_eq!(rev.incoming[&World::A].len(), 1);
        assert_eq!(rev.incoming[&World::B].len(), 1);
        assert_eq!(rev.incoming[&World::A][0].from, World::B); // Spiegel
        assert_eq!(rev.incoming[&World::B][0].from, World::A); // Original
        assert_eq!(s.round, 2);
        assert_eq!(s.rounds_done, 1);

        // Expliziter Wellenstart von B ist im SP-Mode nicht erlaubt.
        let mut s2 = fresh();
        s2.start_sp("momo").unwrap();
        assert_eq!(s2.wave_start(World::B, None).unwrap_err().code, "conflict");
    }

    #[test]
    fn sp_mode_hq_mirrors_and_emits_match_end() {
        let mut s = fresh();
        s.start_sp("momo").unwrap();
        s.report_hq(World::A, 70.0).unwrap();
        assert_eq!(s.hq_hp_of(World::A), 70.0);
        assert_eq!(s.hq_hp_of(World::B), 70.0); // Spiegel
        let e = s.report_hq(World::A, 0.0).unwrap();
        assert!(e.match_over);
        assert_eq!(s.phase, Phase::Finished);
        assert_eq!(s.hq_hp_of(World::B), 0.0);
        assert!(s
            .feed
            .iter()
            .any(|f| f.kind == "match_end" && f.msg.contains("nächster Spieler")));
    }

    #[test]
    fn feed_seq_is_monotonic_and_cursor_works() {
        let mut s = fresh();
        register_all(&mut s);
        s.start_match().unwrap();
        let seqs: Vec<u64> = s.feed.iter().map(|e| e.seq).collect();
        assert!(!seqs.is_empty());
        assert!(seqs.windows(2).all(|w| w[0] < w[1]));
        let last = *seqs.last().unwrap();
        assert!(s.feed_since(last).is_empty());
        assert_eq!(s.feed_since(seqs[0]).len(), seqs.len() - 1);
        assert_eq!(s.last_seq(), last);
    }

    #[test]
    fn mode_parsing_and_display() {
        assert_eq!("sp".parse::<Mode>().unwrap(), Mode::Sp);
        assert_eq!("duel".parse::<Mode>().unwrap(), Mode::Duel);
        assert_eq!(" DUEL ".parse::<Mode>().unwrap(), Mode::Duel);
        assert_eq!("solo".parse::<Mode>().unwrap(), Mode::Sp);
        assert!("x".parse::<Mode>().is_err());
        assert_eq!(Mode::Sp.as_str(), "sp");
        assert_eq!(Mode::Duel.as_str(), "duel");
        assert_eq!(format!("{}", Mode::Sp), "sp");
    }

    // ---- send_state-Egress (Issue #13): score_update-Snapshot ----

    fn res(iron: u64, carbon: u64) -> BTreeMap<String, u64> {
        BTreeMap::from([("iron".to_string(), iron), ("carbon".to_string(), carbon)])
    }

    #[test]
    fn score_update_records_snapshot_and_logs_on_change() {
        let mut s = fresh();
        // nicht registriert → not_found
        assert_eq!(
            s.score_update(World::A, 10, res(1, 2), 1).unwrap_err().code,
            "not_found"
        );
        start(&mut s);
        let e = s.score_update(World::A, 1240, res(320, 80), 4).unwrap();
        assert!(e.changed);
        assert_eq!(s.team(World::A).score, 1240);
        assert_eq!(s.team(World::A).wave, 4);
        assert_eq!(s.team(World::A).resources["iron"], 320);
        assert_eq!(s.team(World::A).resources["carbon"], 80);
        // unveränderter Snapshot → kein neuer Feed-Eintrag
        let before = s.feed.len();
        let e = s.score_update(World::A, 1240, res(320, 80), 4).unwrap();
        assert!(!e.changed);
        assert_eq!(s.feed.len(), before);
        // geänderte Wave → Feed-Eintrag
        s.score_update(World::A, 1240, res(320, 80), 5).unwrap();
        assert!(s.feed.iter().any(|f| f.kind == "score"));
        // View enthält den Snapshot
        let v = s.view();
        assert_eq!(v.teams["A"].score, 1240);
        assert_eq!(v.teams["A"].wave, 5);
        assert_eq!(v.teams["A"].resources["iron"], 320);
    }

    #[test]
    fn score_update_reset_on_rematch() {
        let mut s = fresh();
        start(&mut s);
        s.score_update(World::A, 900, res(10, 10), 3).unwrap();
        s.report_hq(World::A, 0.0).unwrap();
        s.rematch().unwrap();
        assert_eq!(s.team(World::A).score, 0);
        assert_eq!(s.team(World::A).wave, 0);
        assert!(s.team(World::A).resources.is_empty());
    }

    // ---- US1: Welt-getaggte Feed-Events (Issue #996) ----

    /// Welt-spezifische Log-Aufrufe tragen `world`; globale bleiben `None`.
    #[test]
    fn log_entries_are_world_tagged() {
        let mut s = fresh();
        // register (A) → world A.
        s.lobby_register(World::A, "momo").unwrap();
        let reg = s.feed.iter().find(|e| e.kind == "register").unwrap();
        assert_eq!(reg.world, Some(World::A));

        // ready (B) → world B.
        s.lobby_register(World::B, "matheo").unwrap();
        s.ready(World::B).unwrap();
        let rdy = s
            .feed
            .iter()
            .rev()
            .find(|e| e.kind == "ready" && e.msg.contains("B"))
            .unwrap();
        assert_eq!(rdy.world, Some(World::B));

        // go (global) → None.
        s.start_match().unwrap();
        let go = s.feed.iter().find(|e| e.kind == "go").unwrap();
        assert_eq!(go.world, None);

        // send (A→B) ist der Sendewelt A zugeordnet.
        s.route_send(
            World::A,
            vec![UnitSpec {
                unit: "creeper".into(),
                count: 1,
            }],
            100,
        )
        .unwrap();
        let send = s.feed.iter().rev().find(|e| e.kind == "send").unwrap();
        assert_eq!(send.world, Some(World::A));

        // wave (B) → world B.
        s.wave_start(World::B, None).unwrap();
        let wave_b = s
            .feed
            .iter()
            .rev()
            .find(|e| e.kind == "wave" && e.msg.contains("Welt B"))
            .unwrap();
        assert_eq!(wave_b.world, Some(World::B));

        // hq (A) / finish (A) tragen die betroffene Welt; match_end global.
        s.report_hq(World::A, 0.0).unwrap();
        let hq = s.feed.iter().rev().find(|e| e.kind == "hq").unwrap();
        assert_eq!(hq.world, Some(World::A));
        let fin = s.feed.iter().rev().find(|e| e.kind == "finish").unwrap();
        assert_eq!(fin.world, Some(World::A));
        let end = s.feed.iter().rev().find(|e| e.kind == "match_end").unwrap();
        assert_eq!(end.world, None);

        // score (B) ist world-getaggt (nach Rematch irrelevant; separat prüfen).
        let mut s2 = fresh();
        start(&mut s2);
        s2.score_update(World::B, 5, res(1, 1), 1).unwrap();
        let sc = s2.feed.iter().rev().find(|e| e.kind == "score").unwrap();
        assert_eq!(sc.world, Some(World::B));
    }

    /// Das additive `world`-Feld bricht den Feed-Cursor nicht (seq bleibt
    /// monoton, `feed_since`/`last_seq` unverändert).
    #[test]
    fn feed_seq_unchanged_with_world_field() {
        let mut s = fresh();
        register_all(&mut s);
        s.start_match().unwrap();
        let seqs: Vec<u64> = s.feed.iter().map(|e| e.seq).collect();
        assert!(seqs.windows(2).all(|w| w[0] < w[1]));
        let last = s.last_seq();
        assert_eq!(*seqs.last().unwrap(), last);
        assert!(s.feed_since(last).is_empty());
        assert_eq!(s.feed_since(seqs[0]).len(), seqs.len() - 1);
    }

    /// Global-Einträge lassen das `world`-Feld aus (kein Bestandsbruch).
    #[test]
    fn log_entry_world_is_omitted_for_global_entries() {
        let mut s = fresh();
        register_all(&mut s);
        s.start_match().unwrap();
        let v = s.view();
        let go = v.feed.iter().find(|e| e.kind == "go").unwrap();
        assert!(go.world.is_none());
        let json = serde_json::to_value(go).unwrap();
        assert!(
            json.get("world").is_none(),
            "world darf global fehlen: {json}"
        );
        // Welt-Eintrag trägt das Feld explizit.
        let reg = v
            .feed
            .iter()
            .find(|e| e.kind == "register" && e.msg.contains("Welt A"))
            .unwrap();
        let json = serde_json::to_value(reg).unwrap();
        assert_eq!(json["world"], "A");
    }

    // ---- US2: wellen-basierter Send mit Level (Issue #996) ----

    #[test]
    fn send_batch_carries_level_and_routes_to_opponent() {
        let mut s = fresh();
        start(&mut s);
        let b = s.route_wave_send(World::A, 3, 1400).unwrap();
        assert_eq!(b.from, World::A);
        assert_eq!(b.level, Some(3));
        assert_eq!(b.value, 1400);
        assert!(b.units.is_empty());
        assert_eq!(b.round, 1);
        // Batch landet bei der Gegner-Welt B.
        assert_eq!(pending(&s, World::B).len(), 1);
        assert_eq!(pending(&s, World::B)[0].level, Some(3));
        assert!(pending(&s, World::A).is_empty());
        // Feed-Eintrag ist world-getaggt und nennt das Level (US1).
        let send = s.feed.iter().rev().find(|e| e.kind == "send").unwrap();
        assert_eq!(send.world, Some(World::A));
        assert!(send.msg.contains("Level 3"), "msg: {}", send.msg);

        // level < 1 → invalid; vor dem Start → conflict.
        assert_eq!(
            s.route_wave_send(World::A, 0, 1).unwrap_err().code,
            "invalid"
        );
        let mut s2 = fresh();
        register_all(&mut s2);
        assert_eq!(
            s2.route_wave_send(World::A, 3, 1).unwrap_err().code,
            "conflict"
        );

        // Level-Send trägt das Drain-Level ins Reveal der Zielwelt.
        let mut s3 = fresh();
        start(&mut s3);
        s3.route_wave_send(World::A, 5, 4000).unwrap();
        s3.wave_start(World::B, None).unwrap();
        let inc = &s3.reveal.as_ref().unwrap().incoming[&World::B];
        assert_eq!(inc.len(), 1);
        assert_eq!(inc[0].level, Some(5));
        assert_eq!(inc[0].from, World::A);
    }

    /// SP-Mirror unverändert: ein Level-Send wird NICHT zurückgespiegelt.
    #[test]
    fn wave_send_sp_does_not_mirror() {
        let mut s = fresh();
        s.start_sp("momo").unwrap();
        s.route_wave_send(World::A, 2, 700).unwrap();
        assert_eq!(pending(&s, World::B).len(), 1);
        assert!(
            pending(&s, World::A).is_empty(),
            "SP darf einen Level-Send nicht doppelt spiegeln"
        );
    }

    /// Unit-basierte Sends tragen kein `level`-Feld (additiv, kein Bestandsbruch).
    #[test]
    fn unit_send_omits_level_field() {
        let mut s = fresh();
        start(&mut s);
        let b = s
            .route_send(
                World::A,
                vec![UnitSpec {
                    unit: "creeper".into(),
                    count: 2,
                }],
                400,
            )
            .unwrap();
        assert!(b.level.is_none());
        let json = serde_json::to_value(&b).unwrap();
        assert!(json.get("level").is_none(), "level darf fehlen: {json}");
    }

    // ---- #997: Pause/Resume (Match-weiter DOM-Freeze-Fan-out) ----

    #[test]
    fn pause_and_resume_toggle_paused_in_running() {
        let mut s = fresh();
        start(&mut s);
        assert!(!s.paused);

        assert_eq!(s.pause().unwrap(), PauseEffect::Applied);
        assert!(s.paused);
        assert_eq!(s.phase, Phase::Running); // Pause ist kein Phasen-Übergang
        assert!(s.feed.iter().any(|e| e.kind == "pause"));
        // Pause ist global getaggt (world fehlt).
        let p = s.feed.iter().find(|e| e.kind == "pause").unwrap();
        assert!(p.world.is_none());

        // Doppel-Pause → idempotent, kein zweites Feed-Event.
        let before = s.feed.len();
        assert_eq!(s.pause().unwrap(), PauseEffect::AlreadyPaused);
        assert!(s.paused);
        assert_eq!(s.feed.len(), before);

        assert_eq!(s.resume().unwrap(), ResumeEffect::Applied);
        assert!(!s.paused);
        assert!(s.feed.iter().any(|e| e.kind == "resume"));

        // Resume ohne Pause → idempotent, kein Doppel-Feed.
        let before = s.feed.len();
        assert_eq!(s.resume().unwrap(), ResumeEffect::AlreadyRunning);
        assert!(!s.paused);
        assert_eq!(s.feed.len(), before);
    }

    #[test]
    fn pause_and_resume_blocked_outside_running() {
        // Lobby
        let mut s = fresh();
        register_all(&mut s);
        assert_eq!(s.pause().unwrap_err().code, "conflict");
        assert_eq!(s.resume().unwrap_err().code, "conflict");

        // Ready
        s.ready(World::A).unwrap();
        s.ready(World::B).unwrap();
        s.arm_go().unwrap();
        assert_eq!(s.phase, Phase::Ready);
        assert_eq!(s.pause().unwrap_err().code, "conflict");
        assert_eq!(s.resume().unwrap_err().code, "conflict");

        // Finished
        let mut s2 = fresh();
        start(&mut s2);
        s2.report_hq(World::A, 0.0).unwrap();
        assert_eq!(s2.phase, Phase::Finished);
        assert_eq!(s2.pause().unwrap_err().code, "conflict");
        assert_eq!(s2.resume().unwrap_err().code, "conflict");
    }

    #[test]
    fn paused_reset_on_start_match_rematch_and_start_sp() {
        // start_match (Rematch-Zyklus)
        let mut s = fresh();
        start(&mut s);
        s.pause().unwrap();
        assert!(s.paused);
        s.report_hq(World::A, 0.0).unwrap();
        s.rematch().unwrap();
        assert!(!s.paused);
        s.start_match().unwrap();
        assert!(!s.paused);

        // rematch aus pausiertem Finished-Zustand setzt zurück (bereits oben),
        // zusätzlich: pause → resume vor Rematch bleibt sauber.
        let mut s2 = fresh();
        start(&mut s2);
        s2.pause().unwrap();
        s2.resume().unwrap();
        assert!(!s2.paused);

        // start_sp
        let mut s3 = fresh();
        start(&mut s3);
        s3.pause().unwrap();
        // start_sp ist nur außerhalb Running erlaubt → erst beenden.
        s3.report_hq(World::A, 0.0).unwrap();
        s3.start_sp("momo").unwrap();
        assert!(!s3.paused);
        assert_eq!(s3.phase, Phase::Running);
    }

    #[test]
    fn view_reports_paused_and_pause_broadcast() {
        let mut s = fresh();
        start(&mut s);
        assert!(!s.view().paused);
        assert!(s.view().teams["A"].pause_broadcast.at.is_none());

        s.pause().unwrap();
        s.record_pause_broadcast(
            World::A,
            true,
            None,
            Some("http://10.0.0.5:9002/pause_dom".to_string()),
        );
        s.record_pause_broadcast(World::B, false, Some("down".to_string()), None);
        let v = s.view();
        assert!(v.paused);
        assert!(v.teams["A"].pause_broadcast.ok);
        assert_eq!(
            v.teams["A"].pause_broadcast.endpoint.as_deref(),
            Some("http://10.0.0.5:9002/pause_dom")
        );
        assert!(v.teams["A"].pause_broadcast.at.is_some());
        assert!(!v.teams["B"].pause_broadcast.ok);
        assert_eq!(v.teams["B"].pause_broadcast.error.as_deref(), Some("down"));

        s.resume().unwrap();
        assert!(!s.view().paused);
    }

    #[test]
    fn rematch_clears_pause_broadcast() {
        let mut s = fresh();
        start(&mut s);
        s.record_pause_broadcast(World::A, true, None, Some("http://x/pause_dom".to_string()));
        s.report_hq(World::A, 0.0).unwrap();
        s.rematch().unwrap();
        assert_eq!(s.team(World::A).pause_broadcast, BroadcastStatus::default());
    }

    // ---- #999: pure Record-Ableitung (state.rs bleibt I/O-frei) ----

    #[test]
    fn to_record_only_when_finished() {
        let mut s = fresh();
        assert!(s.to_record(0).is_none()); // Lobby

        start(&mut s);
        assert!(s.to_record(0).is_none()); // Running

        s.report_hq(World::A, 0.0).unwrap(); // Sieger B (matheo)
        let r = s.to_record(1_700_000_000_000).unwrap();
        assert_eq!(r.match_id, "rift-1");
        assert_eq!(r.rematch, 0);
        assert_eq!(r.mode, "duel");
        assert_eq!(r.winner_player, "matheo");
        assert_eq!(r.finished_at, "2023-11-14T22:13:20Z");
        assert_eq!(r.participants.len(), 2);

        let momo = r
            .participants
            .iter()
            .find(|p| p.display_name == "momo")
            .unwrap();
        assert_eq!(momo.result, "loss");
        assert_eq!(momo.opponent_id, "B");
        let matheo = r
            .participants
            .iter()
            .find(|p| p.display_name == "matheo")
            .unwrap();
        assert_eq!(matheo.result, "win");
        assert_eq!(matheo.opponent_id, "A");

        // Rematch → neuer Key, kein Record mehr (Phase Lobby).
        s.rematch().unwrap();
        assert!(s.to_record(0).is_none());
        s.start_match().unwrap();
        s.report_hq(World::A, 0.0).unwrap();
        let r2 = s.to_record(0).unwrap();
        assert_eq!(r2.rematch, 1);
        assert_eq!(r2.winner_player, "matheo");
    }
}
