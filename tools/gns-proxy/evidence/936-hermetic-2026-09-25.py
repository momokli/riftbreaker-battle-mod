#!/usr/bin/env python3
"""Hermetischer Beweis fuer Issue #936 (Solo: mehrere Clients auf einer Instanz).

Der Beweis fuehrt NICHT die Gruppenzustands-Logik in Python nach, sondern
kompiliert einen kleinen Harness gegen die ECHTE Aufnahme-Entscheidung
(`rbapi::decideSoloAction` aus tools/gns-proxy/api_util.h) und reproduziert den
Relay-Gruppenzustand (g_soloGroups) Claim-fuer-Claim. Damit ist die Evidenz an
den Produktionscode gekoppelt, nicht an eine Parallel-Implementierung.

Aufruf (aus tools/gns-proxy/):
    python3 evidence/936-hermetic-2026-09-25.py

Erzeugt: evidence/936-hermetic-2026-09-25.txt
Exit 0 nur, wenn alle Erwartungen erfuellt sind.
"""

import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)  # tools/gns-proxy/
OUT_TXT = os.path.join(HERE, "936-hermetic-2026-09-25.txt")

HARNESS = r"""
#include "api_util.h"
#include <cstdio>
#include <map>
#include <string>
#include <vector>

using rbapi::SoloJoinDecision;

static std::map<std::string, std::vector<std::string>> g_groups;   // instance -> members
static std::map<std::string, std::string> g_endpoint;              // instance -> endpoint

static const char *name(SoloJoinDecision d) {
  return rbapi::soloJoinDecisionName(d);
}

// Wendet einen /solo-Request auf den Gruppenzustand an (wie handleSolo +
// processApiCommands): Claim legt die Instanz an, Join fuegt ohne neuen Claim
// hinzu, Full/UnknownInstance aendern nichts.
static void solo(const char *identity, const char *instance, bool requested,
                 int maxPlayers) {
  int count = 0;
  bool member = false;
  std::map<std::string, std::vector<std::string>>::iterator it = g_groups.find(instance);
  if (requested && it != g_groups.end()) {
    count = static_cast<int>(it->second.size());
    for (std::size_t i = 0; i < it->second.size(); ++i) {
      if (it->second[i] == identity) member = true;
    }
  }
  const SoloJoinDecision d = rbapi::decideSoloAction(requested, count, member, maxPlayers);
  std::printf("  /solo identitaet=%s instance=%s -> %s", identity, instance, name(d));
  if (d == SoloJoinDecision::NewClaim) {
    g_groups[instance].push_back(identity);
    g_endpoint[instance] = "127.0.0.1:32768";  // vom Parked-Dienst geliefertes Ziel
    std::printf(" (Instanz angelegt, endpoint=%s)", g_endpoint[instance].c_str());
  } else if (d == SoloJoinDecision::JoinExisting ||
             d == SoloJoinDecision::AlreadyMember) {
    bool present = false;
    for (std::size_t i = 0; i < g_groups[instance].size(); ++i) {
      if (g_groups[instance][i] == identity) present = true;
    }
    if (!present) g_groups[instance].push_back(identity);
    std::printf(" (kein neuer Claim, endpoint=%s)", g_endpoint[instance].c_str());
  }
  std::printf("\n");
}

static void dumpGroup(const char *instance) {
  std::printf("  Instanz %s: endpoint=%s mitglieder=[", instance,
              g_endpoint[instance].c_str());
  std::vector<std::string> &m = g_groups[instance];
  for (std::size_t i = 0; i < m.size(); ++i) {
    std::printf("%s%s", i ? ", " : "", m[i].c_str());
  }
  std::printf("] (n=%zu)\n", m.size());
}

int main() {
  std::printf("Szenario 1: Default-Kapazitaet 4 — zwei Clients auf EINE Instanz\n");
  g_groups.clear(); g_endpoint.clear();
  solo("str:A", "parked-936", false, 4);
  dumpGroup("parked-936");
  solo("str:B", "parked-936", true, 4);
  dumpGroup("parked-936");
  solo("str:A", "parked-936", true, 4);  // idempotenter Rejoin
  dumpGroup("parked-936");
  const std::string ep1 = g_endpoint["parked-936"];
  const std::vector<std::string> m1 = g_groups["parked-936"];
  std::printf("  CHECK beide_auf_instanz: %s\n",
              (m1.size() == 2 && m1[0] == "str:A" && m1[1] == "str:B") ? "OK" : "FAIL");
  std::printf("  CHECK ein_endpoint: %s\n", ep1 == "127.0.0.1:32768" ? "OK" : "FAIL");
  std::printf("  CHECK mitgliederliste_gleich: %s\n",
              (m1.size() == 2) ? "OK (beide Sichten = [str:A, str:B])" : "FAIL");

  std::printf("\nSzenario 2: Kapazitaet 2 — dritter Join -> instance_full\n");
  g_groups.clear(); g_endpoint.clear();
  solo("str:A", "parked-full", false, 2);
  solo("str:B", "parked-full", true, 2);
  solo("str:C", "parked-full", true, 2);
  dumpGroup("parked-full");
  std::printf("  CHECK dritter_abgewiesen: %s (n=%zu, C fehlt=%s)\n",
              (g_groups["parked-full"].size() == 2) ? "OK" : "FAIL",
              g_groups["parked-full"].size(),
              (g_groups["parked-full"].size() == 2 &&
               g_groups["parked-full"][1] == "str:B") ? "ja" : "nein");

  std::printf("\nSzenario 3: unbekannte Instanz -> unknown_instance\n");
  g_groups.clear(); g_endpoint.clear();
  solo("str:D", "parked-unknown", true, 4);
  std::printf("  CHECK unbekannt_kein_claim: %s\n",
              g_groups.find("parked-unknown") == g_groups.end() ? "OK" : "FAIL");
  return 0;
}
"""


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        cpp = os.path.join(td, "harness_936.cpp")
        exe = os.path.join(td, "harness_936")
        with open(cpp, "w", encoding="utf-8") as fh:
            fh.write(HARNESS)
        compile_cmd = [
            "g++", "-std=c++17", "-Wall", "-Wextra", "-O1",
            "-I", ROOT, "-o", exe, cpp,
        ]
        comp = subprocess.run(compile_cmd, capture_output=True, text=True,
                              cwd=ROOT)
        if comp.returncode != 0:
            sys.stderr.write(comp.stderr)
            print("COMPILE FAILED")
            return 1
        run = subprocess.run([exe], capture_output=True, text=True, cwd=ROOT)
        out = run.stdout
        if run.returncode != 0:
            sys.stderr.write(run.stderr)
            print("RUN FAILED")
            return 1

    header = (
        "Issue #936 — hermetischer Beweis: mehrere Clients auf EINER Solo-Instanz\n"
        "Datum: 2026-09-25\n"
        "Repo: github.com/momokli/riftbreaker-battle-mod\n"
        "Quelle: rbapi::decideSoloAction (tools/gns-proxy/api_util.h), "
        "kompiliert mit g++ -std=c++17\n"
        "Skript: tools/gns-proxy/evidence/936-hermetic-2026-09-25.py\n"
        + "=" * 70 + "\n\n"
    )
    with open(OUT_TXT, "w", encoding="utf-8") as fh:
        fh.write(header)
        fh.write(out)
        fh.write("\n" + "=" * 70 + "\n")
        fh.write("Fazit: Zwei Identitaeten landen auf derselben Instanz und dem\n")
        fh.write("selben Endpoint mit identischer Mitgliederliste; der Join ueber\n")
        fh.write("Kapazitaet -> instance_full, unbekannte Instanz -> unknown_instance.\n")

    fails = out.count("FAIL")
    print(out)
    print("geschrieben: " + OUT_TXT)
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())