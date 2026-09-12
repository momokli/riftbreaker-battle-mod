//! Server-seitiger Referee — autoritative Event-/State-Quelle (Issue #268).
//!
//! Rolle: Der Referee ist das „Gehirn“; die in-game Lua ist reiner
//! **Executor**. Der Referee konsumiert Spiel-Events (über den Rückkanal
//! Relay/Pipe, Issue #265) und entscheidet daraus Commands, die der Executor
//! ausführt:
//!
//! * **Wellen-Takt:** Nach `ready` gibt der Referee die erste Welle aus,
//!   danach je abgeschlossener Welle die nächste (`rb_wave <level>`). Das
//!   Wellen-Level vergibt der Server (monoton, pro Welt) — die Lua zählt
//!   keine Runden mehr mit.
//! * **HQ-Tod:** `hq_destroyed` → `restart` + Runde hochzählen; die Wellen
//!   ruhen, bis der Executor nach dem Neustart wieder `ready` meldet.
//!
//! Der Modul ist **deterministisch**: keine Uhr, kein Zufall, kein I/O —
//! gleiche Event-Folge ⇒ gleiche Command-Folge. Genau darum ist der
//! Runden-/Wellen-/HQ-Kern ohne Spieler/Player testbar (Test-Split #268,
//! „Event-In → Command-Out“). Der volle Loop *mit* Spieler (Welle spawnt →
//! HQ zerstört → Restart) bleibt ein offener Player-Test.
//!
//! Events ohne Wirkung (Duplikate, veraltete `wave_done`-Level, `ready` im
//! laufenden Zustand) werden **idempotent ignoriert** und erzeugen keinen
//! Command — ein doppeltes Log-Event löst so nie eine Doppel-Welle aus.

use crate::state::World;
use serde::{Deserialize, Serialize};
use std::collections::VecDeque;

/// Spiel-Event-Typen, die der Executor nach oben meldet.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum GameEventKind {
    /// Executor ist bereit (Map geladen bzw. nach `restart` wieder oben).
    Ready,
    /// Eine Wellen-Spawnung ist abgeschlossen (`event=wave level=N status=done`).
    WaveDone,
    /// Das HQ der Welt wurde zerstört (`event=hq_dead`).
    HqDestroyed,
}

/// Ein eingehendes Spiel-Event (Envelope: Welt + Typ + optionales Level).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct GameEvent {
    pub world: World,
    pub kind: GameEventKind,
    /// Nur für [`GameEventKind::WaveDone`] relevant.
    pub level: Option<u32>,
}

/// Ein Command, das der Referee dem Executor gibt.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Command {
    /// Ziel-Welt (die Spiel-Instanz, die den Command ausführt).
    pub world: World,
    /// Auszuführender Konsolen-Command (z. B. `rb_wave 3`, `restart`).
    pub command: String,
    /// Server-seitige Command-ID (Dedup-Schlüssel für das Relay, Issue #60).
    pub cmd_id: u64,
    /// Begründung (Debug/Nachvollziehbarkeit, z. B. `ready`, `wave_done`,
    /// `hq_destroyed round=2`).
    pub reason: String,
}

/// Referee-Konfiguration (aus Env im `main`, in Tests direkt konstruierbar).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RefereeConfig {
    /// Obergrenze für das Wellen-Level; `0` = unbegrenzt. Ab dem Deckel gibt
    /// der Referee keine weitere Welle aus (Warten auf HQ-Tod/Reset).
    pub max_wave: u32,
    /// Command, den der Executor beim HQ-Tod ausführt (Neustart des Spiels).
    pub restart_cmd: String,
}

impl Default for RefereeConfig {
    fn default() -> Self {
        RefereeConfig {
            max_wave: 0,
            restart_cmd: "restart".to_string(),
        }
    }
}

/// Zustand einer Welt (Spiel-Instanz) aus Referee-Sicht.
#[derive(Debug, Clone, PartialEq, Eq)]
struct WorldReferee {
    /// Executor läuft (hat `ready` gemeldet, noch kein HQ-Tod).
    running: bool,
    /// `restart` ausgegeben, wartet auf erneutes `ready`.
    restart_pending: bool,
    /// Level der aktuell offenen Welle (`None` = keine Welle in Arbeit).
    waves_in_flight: Option<u32>,
    /// Nächstes zu vergebendes Wellen-Level (1-basiert).
    next_level: u32,
    /// Abgeschlossene Runden (= HQ-Zerstörungen) dieser Welt.
    rounds: u32,
    /// Insgesamt ausgegebene Commands (Monitoring).
    commands_sent: u64,
    /// Noch nicht abgeholte Commands (`GET /referee/poll`).
    outbox: VecDeque<Command>,
}

impl WorldReferee {
    fn fresh() -> Self {
        WorldReferee {
            running: false,
            restart_pending: false,
            waves_in_flight: None,
            next_level: 1,
            rounds: 0,
            commands_sent: 0,
            outbox: VecDeque::new(),
        }
    }
}

/// Öffentliche, serialisierbare Sicht auf eine Welt (für `/referee/*`).
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct WorldRefereeView {
    pub running: bool,
    pub restart_pending: bool,
    pub waves_in_flight: Option<u32>,
    pub next_level: u32,
    pub rounds: u32,
    pub commands_sent: u64,
    pub queued_commands: usize,
}

/// Der Referee über beide Welten.
#[derive(Debug, Clone)]
pub struct Referee {
    cfg: RefereeConfig,
    worlds: [WorldReferee; 2],
    /// Nächste zu vergebende Command-ID (global, monoton).
    next_cmd_id: u64,
}

impl Referee {
    pub fn new(cfg: RefereeConfig) -> Self {
        Referee {
            cfg,
            worlds: [WorldReferee::fresh(), WorldReferee::fresh()],
            next_cmd_id: 1,
        }
    }

    fn slot(w: World) -> usize {
        match w {
            World::A => 0,
            World::B => 1,
        }
    }

    fn world_of(idx: usize) -> World {
        if idx == 0 {
            World::A
        } else {
            World::B
        }
    }

    fn next_id(&mut self) -> u64 {
        let id = self.next_cmd_id;
        self.next_cmd_id += 1;
        id
    }

    /// Verarbeitet ein Spiel-Event und liefert die daraus entschiedenen
    /// Commands (in Reihenfolge). Deterministisch — Kern des Test-Splits
    /// „Event-In → Command-Out“.
    pub fn on_event(&mut self, ev: GameEvent) -> Vec<Command> {
        let idx = Self::slot(ev.world);
        match ev.kind {
            GameEventKind::Ready => {
                // Duplikat/Race: läuft schon → keine zweite Welle.
                if self.worlds[idx].running {
                    return Vec::new();
                }
                self.worlds[idx].running = true;
                self.worlds[idx].restart_pending = false;
                self.emit_next(idx, "ready")
            }
            GameEventKind::WaveDone => {
                // Ohne laufenden Executor bzw. bei veraltetem/fremdem Level
                // (Duplikat, Out-of-Order) keine neue Welle.
                if !self.worlds[idx].running {
                    return Vec::new();
                }
                let level = ev.level.unwrap_or(0);
                if self.worlds[idx].waves_in_flight != Some(level) {
                    return Vec::new();
                }
                self.worlds[idx].waves_in_flight = None;
                self.worlds[idx].next_level = level + 1;
                self.emit_next(idx, "wave_done")
            }
            GameEventKind::HqDestroyed => {
                // Duplikat (schon tot / Restart läuft) → kein zweiter Restart.
                if !self.worlds[idx].running {
                    return Vec::new();
                }
                self.worlds[idx].running = false;
                self.worlds[idx].restart_pending = true;
                self.worlds[idx].waves_in_flight = None;
                self.worlds[idx].next_level = 1;
                self.worlds[idx].rounds += 1;
                let rounds = self.worlds[idx].rounds;
                let command = self.cfg.restart_cmd.clone();
                let cmd = Command {
                    world: Self::world_of(idx),
                    command,
                    cmd_id: self.next_id(),
                    reason: format!("hq_destroyed round={rounds}"),
                };
                self.worlds[idx].commands_sent += 1;
                self.worlds[idx].outbox.push_back(cmd.clone());
                vec![cmd]
            }
        }
    }

    /// Gibt die nächste Welle aus — außer der Wellen-Deckel ist erreicht.
    fn emit_next(&mut self, idx: usize, reason: &str) -> Vec<Command> {
        let next = self.worlds[idx].next_level;
        if self.cfg.max_wave > 0 && next > self.cfg.max_wave {
            return Vec::new();
        }
        self.worlds[idx].waves_in_flight = Some(next);
        self.worlds[idx].commands_sent += 1;
        let cmd = Command {
            world: Self::world_of(idx),
            command: format!("rb_wave {next}"),
            cmd_id: self.next_id(),
            reason: reason.to_string(),
        };
        self.worlds[idx].outbox.push_back(cmd.clone());
        vec![cmd]
    }

    /// Holt alle offenen Commands einer Welt ab (leert deren Outbox).
    /// Der Relay-/Bridge-Poll zieht die Commands so nach dem Push-Fallback ab.
    pub fn poll(&mut self, w: World) -> Vec<Command> {
        self.worlds[Self::slot(w)].outbox.drain(..).collect()
    }

    /// Sicht auf eine Welt (für `/referee/event`-Antwort und Monitoring).
    pub fn world_view(&self, w: World) -> WorldRefereeView {
        let wr = &self.worlds[Self::slot(w)];
        WorldRefereeView {
            running: wr.running,
            restart_pending: wr.restart_pending,
            waves_in_flight: wr.waves_in_flight,
            next_level: wr.next_level,
            rounds: wr.rounds,
            commands_sent: wr.commands_sent,
            queued_commands: wr.outbox.len(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ready(w: World) -> GameEvent {
        GameEvent {
            world: w,
            kind: GameEventKind::Ready,
            level: None,
        }
    }

    fn wave_done(w: World, level: u32) -> GameEvent {
        GameEvent {
            world: w,
            kind: GameEventKind::WaveDone,
            level: Some(level),
        }
    }

    fn hq_destroyed(w: World) -> GameEvent {
        GameEvent {
            world: w,
            kind: GameEventKind::HqDestroyed,
            level: None,
        }
    }

    #[test]
    fn ready_triggers_first_wave() {
        let mut r = Referee::new(RefereeConfig::default());
        let cmds = r.on_event(ready(World::A));
        assert_eq!(cmds.len(), 1);
        assert_eq!(cmds[0].command, "rb_wave 1");
        assert_eq!(cmds[0].world, World::A);
        assert_eq!(cmds[0].reason, "ready");
        assert_eq!(cmds[0].cmd_id, 1);
        assert_eq!(r.world_view(World::A).waves_in_flight, Some(1));
    }

    #[test]
    fn wave_done_advances_to_next_level() {
        let mut r = Referee::new(RefereeConfig::default());
        r.on_event(ready(World::A));
        let cmds = r.on_event(wave_done(World::A, 1));
        assert_eq!(cmds.len(), 1);
        assert_eq!(cmds[0].command, "rb_wave 2");
        assert_eq!(cmds[0].reason, "wave_done");
        let cmds = r.on_event(wave_done(World::A, 2));
        assert_eq!(cmds[0].command, "rb_wave 3");
    }

    #[test]
    fn duplicate_ready_is_idempotent() {
        let mut r = Referee::new(RefereeConfig::default());
        assert_eq!(r.on_event(ready(World::A)).len(), 1);
        assert!(r.on_event(ready(World::A)).is_empty());
        // Keine zweite Welle: noch immer genau Welle 1 offen.
        assert_eq!(r.world_view(World::A).waves_in_flight, Some(1));
        assert_eq!(r.world_view(World::A).commands_sent, 1);
    }

    #[test]
    fn duplicate_and_stale_wave_done_ignored() {
        let mut r = Referee::new(RefereeConfig::default());
        r.on_event(ready(World::A)); // rb_wave 1
                                     // Doppeltes Level-1-done → ignoriert (kein rb_wave 2 zweimal).
        assert!(r.on_event(wave_done(World::A, 1)).len() == 1);
        assert!(r.on_event(wave_done(World::A, 1)).is_empty());
        // Veraltetes Level → ignoriert.
        r.on_event(wave_done(World::A, 2)); // rb_wave 3
        assert!(r.on_event(wave_done(World::A, 1)).is_empty());
        assert!(r.on_event(wave_done(World::A, 2)).is_empty());
    }

    #[test]
    fn wave_done_without_ready_is_ignored() {
        let mut r = Referee::new(RefereeConfig::default());
        assert!(r.on_event(wave_done(World::A, 1)).is_empty());
    }

    #[test]
    fn hq_destroyed_emits_restart_and_resets_round() {
        let mut r = Referee::new(RefereeConfig::default());
        r.on_event(ready(World::A)); // rb_wave 1
        let cmds = r.on_event(hq_destroyed(World::A));
        assert_eq!(cmds.len(), 1);
        assert_eq!(cmds[0].command, "restart");
        assert_eq!(cmds[0].reason, "hq_destroyed round=1");
        let v = r.world_view(World::A);
        assert!(!v.running);
        assert!(v.restart_pending);
        assert_eq!(v.rounds, 1);
        assert_eq!(v.waves_in_flight, None);
        // Nach dem Neustart: ready → wieder Welle 1 der neuen Runde.
        let cmds = r.on_event(ready(World::A));
        assert_eq!(cmds[0].command, "rb_wave 1");
        let v = r.world_view(World::A);
        assert!(v.running);
        assert!(!v.restart_pending);
        assert_eq!(v.waves_in_flight, Some(1));
        assert_eq!(v.next_level, 1); // Welle 1 läuft; nächster Level kommt mit wave_done
    }

    #[test]
    fn duplicate_hq_destroyed_only_one_restart() {
        let mut r = Referee::new(RefereeConfig::default());
        r.on_event(ready(World::A));
        assert_eq!(r.on_event(hq_destroyed(World::A)).len(), 1);
        assert!(r.on_event(hq_destroyed(World::A)).is_empty());
        assert_eq!(r.world_view(World::A).rounds, 1);
    }

    #[test]
    fn hq_destroyed_without_ready_is_ignored() {
        let mut r = Referee::new(RefereeConfig::default());
        assert!(r.on_event(hq_destroyed(World::A)).is_empty());
        assert_eq!(r.world_view(World::A).rounds, 0);
    }

    #[test]
    fn max_wave_caps_emitted_waves() {
        let mut r = Referee::new(RefereeConfig {
            max_wave: 2,
            ..Default::default()
        });
        r.on_event(ready(World::A)); // rb_wave 1
        r.on_event(wave_done(World::A, 1)); // rb_wave 2
                                            // Welle 2 fertig → Deckel erreicht, keine Welle 3.
        assert!(r.on_event(wave_done(World::A, 2)).is_empty());
        assert_eq!(r.world_view(World::A).next_level, 3);
        assert_eq!(r.world_view(World::A).waves_in_flight, None);
    }

    #[test]
    fn worlds_are_independent() {
        let mut r = Referee::new(RefereeConfig::default());
        r.on_event(ready(World::A)); // A: rb_wave 1
        let cmds = r.on_event(ready(World::B)); // B: rb_wave 1
        assert_eq!(cmds[0].world, World::B);
        assert_eq!(cmds[0].command, "rb_wave 1");
        // A bleibt unberührt bei B-Events.
        r.on_event(wave_done(World::B, 1));
        assert_eq!(r.world_view(World::A).waves_in_flight, Some(1));
        assert_eq!(r.world_view(World::B).waves_in_flight, Some(2));
    }

    #[test]
    fn poll_drains_outbox_per_world_and_keeps_cmd_ids() {
        let mut r = Referee::new(RefereeConfig::default());
        r.on_event(ready(World::A)); // cmd_id 1
        r.on_event(ready(World::B)); // cmd_id 2
        let a = r.poll(World::A);
        assert_eq!(a.len(), 1);
        assert_eq!(a[0].cmd_id, 1);
        assert!(r.poll(World::A).is_empty()); // geleert
        let b = r.poll(World::B);
        assert_eq!(b[0].cmd_id, 2);
    }

    #[test]
    fn custom_restart_command_is_used() {
        let mut r = Referee::new(RefereeConfig {
            max_wave: 0,
            restart_cmd: "rb_restart".to_string(),
        });
        r.on_event(ready(World::A));
        let cmds = r.on_event(hq_destroyed(World::A));
        assert_eq!(cmds[0].command, "rb_restart");
    }
}
