// Host-Test fuer player_identity.h (Issue #992) — laeuft in der CI auf
// ubuntu-latest (g++ -std=c++17), ohne Wine/Windows/Spiel. Reine Logik.
//
// Deckt die Identitaets-Abstraktion ab, die zuvor als Inline-Prefix-Check in
// gns_probe.cpp stand: `steamid:<id>` (Steam), `str:<hex>` (anonymer Generic,
// stabil pro Installation/GOG), `account:<id>` (reservierter Andockpunkt).
//
// Kern-Invariante (Routen-/Pin-/Claim-Kompatibilitaet): `canonical` ist fuer
// Steam/Generic/Account byte-gleich zu `raw` — nur Hex wird lowercase
// normalisiert. Nur so bleibt der heutige Routen-Key (`str:…`/`steamid:…`)
// unveraendert.

#include "player_identity.h"

#include <cstdio>
#include <string>

using rbident::Kind;
using rbident::PlayerIdentity;

static int g_failures = 0;
static int g_checks = 0;

static void check(bool cond, const char *what) {
  ++g_checks;
  if (!cond) {
    ++g_failures;
    std::fprintf(stderr, "FAIL: %s\n", what);
  }
}

static void checkEq(const std::string &got, const std::string &want,
                    const char *what) {
  ++g_checks;
  if (got != want) {
    ++g_failures;
    std::fprintf(stderr, "FAIL: %s (got '%s', want '%s')\n", what, got.c_str(),
                 want.c_str());
  }
}

// ---------------------------------------------------------------------------
// Story 1: Wertobjekt + Parser
// ---------------------------------------------------------------------------

static void testParseSteam() {
  PlayerIdentity id;
  check(rbident::parsePlayerIdentity("steamid:123", id), "steamid parst");
  check(id.valid, "steamid valid");
  check(id.kind == Kind::Steam, "steamid kind=Steam");
  checkEq(id.raw, "steamid:123", "steamid raw");
  checkEq(id.canonical, "steamid:123", "steamid canonical byte-gleich");
}

static void testParseGeneric() {
  PlayerIdentity id;
  check(rbident::parsePlayerIdentity("str:AB12", id), "str parst");
  check(id.valid, "str valid");
  check(id.kind == Kind::Generic, "str kind=Generic");
  checkEq(id.raw, "str:AB12", "str raw bleibt roh");
  checkEq(id.canonical, "str:ab12", "str canonical hex lowercase");
}

static void testParseAccount() {
  PlayerIdentity id;
  check(rbident::parsePlayerIdentity("account:4711", id), "account parst");
  check(id.valid, "account valid");
  check(id.kind == Kind::Account, "account kind=Account");
  checkEq(id.canonical, "account:4711", "account canonical byte-gleich");
}

static void testParseInvalid() {
  PlayerIdentity id;
  check(!rbident::parsePlayerIdentity("", id), "leer -> invalid");
  check(!id.valid, "leer valid=false");
  check(!rbident::parsePlayerIdentity("momo", id), "Spielname -> invalid");
  check(!id.valid, "Spielname valid=false");
  check(!rbident::parsePlayerIdentity("steamid:", id), "leerer Rest -> invalid");
  check(!rbident::parsePlayerIdentity("str:", id), "leerer str-Rest -> invalid");
  check(!rbident::parsePlayerIdentity("steam:1", id), "falsches Praefix -> invalid");
}

static void testIsIdentityLike() {
  check(rbident::isIdentityLike("str:AB12"), "isIdentityLike str:");
  check(rbident::isIdentityLike("steamid:7"), "isIdentityLike steamid:");
  check(rbident::isIdentityLike("account:9"), "isIdentityLike account:");
  check(!rbident::isIdentityLike("momo-dev"), "Spielname ist keine Identitaet");
  check(!rbident::isIdentityLike(""), "leer ist keine Identitaet");
  check(!rbident::isIdentityLike("stranger:1"), "nur bekannte Praefixe");
}

// ---------------------------------------------------------------------------
// Story 2: kanonische Form + passives authorized
// ---------------------------------------------------------------------------

static void testCanonicalDistinguishes() {
  PlayerIdentity steam;
  PlayerIdentity generic;
  rbident::parsePlayerIdentity("steamid:123", steam);
  rbident::parsePlayerIdentity("str:123", generic);
  check(steam.canonical != generic.canonical,
        "Steam und Generic nie derselbe canonical");
  check(steam.kind != generic.kind, "Steam und Generic unterscheidbare kinds");

  PlayerIdentity a;
  PlayerIdentity b;
  rbident::parsePlayerIdentity("str:aa", a);
  rbident::parsePlayerIdentity("str:ab", b);
  check(a.canonical != b.canonical, "zwei Generic-Installationen unterscheidbar");

  // Normalisierung idempotent: canonical ist bereits kanonisch.
  PlayerIdentity again;
  rbident::parsePlayerIdentity(a.canonical, again);
  checkEq(again.canonical, a.canonical, "canonical stabil bei Re-Parse");
}

static void testIsAuthorizedPassive() {
  PlayerIdentity id;
  rbident::parsePlayerIdentity("str:AB12", id);
  check(rbident::isAuthorized(true, id), "verbunden+valid -> authorized");
  check(!rbident::isAuthorized(false, id), "nicht verbunden -> nicht authorized");

  PlayerIdentity bad;
  rbident::parsePlayerIdentity("momo", bad);
  check(!rbident::isAuthorized(true, bad), "verbunden+invalid -> nicht authorized");
  check(!rbident::isAuthorized(false, bad), "nicht verbunden+invalid -> false");
}

int main() {
  testParseSteam();
  testParseGeneric();
  testParseAccount();
  testParseInvalid();
  testIsIdentityLike();
  testCanonicalDistinguishes();
  testIsAuthorizedPassive();

  if (g_failures == 0) {
    std::printf("test_player_identity: %d Checks OK\n", g_checks);
    return 0;
  }
  std::fprintf(stderr, "test_player_identity: %d von %d Checks fehlgeschlagen\n",
               g_failures, g_checks);
  return 1;
}