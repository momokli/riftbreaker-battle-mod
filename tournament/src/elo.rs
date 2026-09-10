//! ELO-Berechnung nach Schach-Vorbild (Issue #131, Konzept #128).
//!
//! Reine, I/O-freie Funktionen — die Fortschreibung der Profile (SQLite,
//! `player_profile.elo`) übernimmt später der Persistenz-Layer aus #129.
//!
//! Erwartete Gewinnchance von A gegen B:
//!
//! ```text
//!   E_A = 1 / (1 + 10^((R_B - R_A) / 400))
//! ```
//!
//! Punkteänderung nach dem Match (Ergebnis `S_A` ∈ {1, 0, 0.5}):
//!
//! ```text
//!   R_A' = R_A + K * (S_A - E_A)
//! ```
//!
//! Eigenschaften, die dieses Modul garantiert (siehe Tests):
//! - Startwert neuer Profile: [`ELO_START`] (1200).
//! - Untergrenze [`ELO_MIN`] (100) — Invariante 2 aus `docs/PLAYER_PROFILE_MODEL.md`.
//! - `E_A + E_B == 1` und Nullsummen-Näherung (`a_delta + b_delta` ∈ {0, ±1},
//!   Rundung je Spieler).
//! - Unentschieden ist `S = 0.5` und für gleich starke Spieler ratingsneutral.
//!
//! Der K-Faktor ist konfigurierbar ([`EloConfig`], Platzhalter
//! [`K_FACTOR_DEFAULT`]) — Tuning analog zur Preis-/HP-Balance aus #33.

// Integration am `match_end`-Event (state.rs) folgt mit dem Profil-Store aus
// #129; bis dahin nutzt nur die Test-Suite (cfg(test)) diese API.
#![allow(dead_code)]

use serde::Serialize;

/// Start-ELO für neue Profile (Schach-Analogie, #128/#131).
pub const ELO_START: i32 = 1200;

/// Untergrenze für Ratings — verhindert Kollaps ins Negative.
pub const ELO_MIN: i32 = 100;

/// Platzhalter-K-Faktor (späteres Tuning möglich, vgl. #33).
pub const K_FACTOR_DEFAULT: f64 = 32.0;

/// Ergebnis eines Matches aus Sicht **eines** Spielers.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum MatchResult {
    Win,
    Loss,
    Draw,
}

impl MatchResult {
    /// Ergebnis-Score `S` (1.0 / 0.0 / 0.5).
    pub fn score(self) -> f64 {
        match self {
            MatchResult::Win => 1.0,
            MatchResult::Loss => 0.0,
            MatchResult::Draw => 0.5,
        }
    }

    /// Umgekehrtes Ergebnis (Sieg↔Niederlage, Unentschieden bleibt).
    pub fn opponent(self) -> MatchResult {
        match self {
            MatchResult::Win => MatchResult::Loss,
            MatchResult::Loss => MatchResult::Win,
            MatchResult::Draw => MatchResult::Draw,
        }
    }
}

/// Erwartungswert `E_A` — Gewinnchance von `rating_a` gegen `rating_b`.
pub fn expected_score(rating_a: i32, rating_b: i32) -> f64 {
    1.0 / (1.0 + 10f64.powf((rating_b - rating_a) as f64 / 400.0))
}

/// Neues Rating von A nach dem Match, begrenzt auf [`ELO_MIN`].
///
/// `result` ist das Ergebnis aus Sicht von A; `k` der K-Faktor.
pub fn new_rating(rating_a: i32, rating_b: i32, result: MatchResult, k: f64) -> i32 {
    let delta = k * (result.score() - expected_score(rating_a, rating_b));
    (rating_a as f64 + delta).round() as i32
}

/// Konfiguration des ELO-Updates (K-Faktor).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct EloConfig {
    pub k_factor: f64,
}

impl Default for EloConfig {
    fn default() -> Self {
        EloConfig {
            k_factor: K_FACTOR_DEFAULT,
        }
    }
}

impl EloConfig {
    /// Neue Konfiguration; `k_factor` muss endlich und ≥ 0 sein.
    pub fn new(k_factor: f64) -> Self {
        assert!(
            k_factor.is_finite() && k_factor >= 0.0,
            "K-Faktor muss endlich und >= 0 sein, war {k_factor}"
        );
        EloConfig { k_factor }
    }

    /// Erwartungswert aus Sicht von A (siehe [`expected_score`]).
    pub fn expected(&self, rating_a: i32, rating_b: i32) -> f64 {
        expected_score(rating_a, rating_b)
    }

    /// Rating-Update für A (siehe [`new_rating`]).
    pub fn new_rating(&self, rating_a: i32, rating_b: i32, result: MatchResult) -> i32 {
        new_rating(rating_a, rating_b, result, self.k_factor)
    }

    /// Paar-Update beider Spieler inkl. Vorher/Nachher und Deltas.
    ///
    /// `result_from_a` ist das Ergebnis aus Sicht von A. Beide Ratings werden
    /// unabhängig gerundet und auf [`ELO_MIN`] begrenzt; die Deltas sind daher
    /// bis auf eine Rundungsdifferenz von ±1 nullsummig.
    pub fn update_pair(
        &self,
        rating_a: i32,
        rating_b: i32,
        result_from_a: MatchResult,
    ) -> EloUpdate {
        let a_after = self.new_rating(rating_a, rating_b, result_from_a);
        let b_after = self.new_rating(rating_b, rating_a, result_from_a.opponent());
        EloUpdate {
            a_before: rating_a,
            a_after,
            a_delta: a_after - rating_a,
            b_before: rating_b,
            b_after,
            b_delta: b_after - rating_b,
            expected_a: self.expected(rating_a, rating_b),
        }
    }
}

/// Ergebnis eines Paar-Updates (Rohdaten für den `match_record`-Eintrag, #129).
#[derive(Debug, Clone, Copy, PartialEq, Serialize)]
pub struct EloUpdate {
    pub a_before: i32,
    pub a_after: i32,
    pub a_delta: i32,
    pub b_before: i32,
    pub b_after: i32,
    pub b_delta: i32,
    /// Erwartungswert aus Sicht von A (Beleg für Debug/Feed).
    pub expected_a: f64,
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Bekannte Schach-Tabelle: Rating-Differenz → gerundete Gewinnchance
    /// (FIDE-/Elo-Standardwerte, z. B. Wikipedia „Elo rating system").
    const CHESS_TABLE: &[(i32, f64)] = &[
        (0, 0.50),
        (100, 0.64),
        (200, 0.76),
        (300, 0.85),
        (400, 0.91),
        (500, 0.95),
        (600, 0.97),
        (700, 0.98),
        (800, 0.99),
    ];

    fn approx(a: f64, b: f64, eps: f64) -> bool {
        (a - b).abs() < eps
    }

    #[test]
    fn expected_score_matches_chess_reference_table() {
        // Nullpunkt: gleich starke Spieler gewinnen je zur Hälfte.
        for &(diff, expected) in CHESS_TABLE {
            let score_weak = expected_score(1200, 1200 + diff);
            let score_strong = expected_score(1200 + diff, 1200);
            // Tabellenwert = Chance des höher bewerteten Spielers, auf zwei
            // Nachkommastellen gerundet.
            assert_eq!(
                (score_strong * 100.0).round() / 100.0,
                expected,
                "Differenz {diff}: {score_strong}"
            );
            // Symmetrie: E_A + E_B == 1.
            assert!(approx(score_weak + score_strong, 1.0, 1e-12));
        }
    }

    #[test]
    fn expected_score_exact_reference_values() {
        // Exakte Formelwerte (Wikipedia-Rechenbeispiele).
        assert!(approx(expected_score(1600, 1400), 0.759_746_926_6, 1e-9));
        assert!(approx(expected_score(1400, 1600), 0.240_253_073_4, 1e-9));
        // 400 Punkte Vorsprung → 10/11.
        assert!(approx(expected_score(1600, 1200), 10.0 / 11.0, 1e-9));
    }

    #[test]
    fn equal_ratings_k32() {
        assert_eq!(new_rating(1200, 1200, MatchResult::Win, 32.0), 1216);
        assert_eq!(new_rating(1200, 1200, MatchResult::Loss, 32.0), 1184);
        assert_eq!(new_rating(1200, 1200, MatchResult::Draw, 32.0), 1200);
    }

    #[test]
    fn favourite_beats_underdog() {
        // Favorit (1600) gewinnt: E ≈ 0.7597 → +7.69 → gerundet +8.
        assert_eq!(new_rating(1600, 1400, MatchResult::Win, 32.0), 1608);
        // Favorit verliert: 32 * (0 - 0.7597) ≈ -24.31 → -24.
        assert_eq!(new_rating(1600, 1400, MatchResult::Loss, 32.0), 1576);
        // Unentschieden: 32 * (0.5 - 0.7597) ≈ -8.31 → -8.
        assert_eq!(new_rating(1600, 1400, MatchResult::Draw, 32.0), 1592);
    }

    #[test]
    fn underdog_win_gains_more_than_favourite() {
        let underdog = new_rating(1200, 1600, MatchResult::Win, 32.0) - 1200; // E ≈ 0.0909
        let favourite = new_rating(1600, 1400, MatchResult::Win, 32.0) - 1600; // E ≈ 0.7597
        assert_eq!(underdog, 29);
        assert_eq!(favourite, 8);
        assert!(underdog > favourite);
    }

    #[test]
    fn draw_is_rating_neutral_for_equal_strength() {
        let cfg = EloConfig::default();
        let up = cfg.update_pair(1200, 1200, MatchResult::Draw);
        assert_eq!(up.a_after, 1200);
        assert_eq!(up.b_after, 1200);
        assert_eq!(up.a_delta, 0);
        assert_eq!(up.b_delta, 0);
    }

    #[test]
    fn pair_update_is_near_zero_sum() {
        let cfg = EloConfig::new(K_FACTOR_DEFAULT);
        for diff in [0, 50, 100, 250, 400, 800] {
            for result in [MatchResult::Win, MatchResult::Loss, MatchResult::Draw] {
                let up = cfg.update_pair(1200, 1200 + diff, result);
                let sum = up.a_delta + up.b_delta;
                assert!(
                    sum.abs() <= 1,
                    "diff {diff}, {result:?}: a_delta {} + b_delta {} = {sum}",
                    up.a_delta,
                    up.b_delta
                );
                // Vorher/Nachher-Konsistenz (Invariante 5 aus #129).
                assert_eq!(up.a_after, up.a_before + up.a_delta);
                assert_eq!(up.b_after, up.b_before + up.b_delta);
            }
        }
    }

    #[test]
    fn k_factor_is_configurable_and_scales_delta() {
        let small = EloConfig::new(10.0).update_pair(1200, 1200, MatchResult::Win);
        let large = EloConfig::new(40.0).update_pair(1200, 1200, MatchResult::Win);
        assert_eq!(small.a_delta, 5);
        assert_eq!(large.a_delta, 20);
    }

    #[test]
    fn rating_never_drops_below_floor() {
        // Extrem schwächerer Spieler verliert: Rohwert < ELO_MIN → Floor greift.
        let after = new_rating(ELO_MIN, 2000, MatchResult::Loss, K_FACTOR_DEFAULT);
        assert_eq!(after, ELO_MIN);
        let up = EloConfig::default().update_pair(ELO_MIN, 2000, MatchResult::Loss);
        assert_eq!(up.a_after, ELO_MIN);
        assert!(up.a_after >= ELO_MIN);
    }

    #[test]
    fn match_result_is_symmetric() {
        assert_eq!(MatchResult::Win.opponent(), MatchResult::Loss);
        assert_eq!(MatchResult::Loss.opponent(), MatchResult::Win);
        assert_eq!(MatchResult::Draw.opponent(), MatchResult::Draw);
        assert_eq!(MatchResult::Win.score(), 1.0);
        assert_eq!(MatchResult::Loss.score(), 0.0);
        assert_eq!(MatchResult::Draw.score(), 0.5);
    }

    #[test]
    #[should_panic]
    fn invalid_k_factor_panics() {
        EloConfig::new(-1.0);
    }
}
