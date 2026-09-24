// gns_probe.cpp — Spike #831, Experimente E1/E2.
//
// Laeuft als **GNS-Server** auf einem UDP-Port (Default 6321) und protokolliert
// alles, was ein echter Riftbreaker-Client dorthin schickt:
//
//   E1: Kommt der Handshake zustande? (Akzeptiert der Client einen fremden
//       GNS-Server, oder prueft er eine Server-Identitaet?)
//   E2: Sehen wir seine Game-Nachrichten — steht der Spielername im Klartext?
//
// Bewusste Konstruktions-Entscheidungen:
//   * Nur `steamnetworkingtypes.h` (Datentypen) wird inkludiert — KEINE
//     C++-Interface-Header. Dadurch haengen wir an keiner vtable-Reihenfolge.
//   * Alle Funktionen kommen per `GetProcAddress` aus der mitgelieferten
//     `GameNetworkingSockets.dll` (die Flat-Exports
//     `SteamAPI_ISteamNetworkingSockets_*` plus den Accessor
//     `SteamAPI_SteamNetworkingSockets_v009`, den die DLL nachweislich
//     exportiert). Kein Import-Lib, kein Link gegen die DLL.
//   * Es wird nichts weitergeleitet und nichts beantwortet: der Probe soll nur
//     zeigen, ob und was der Client sendet.
//
// Aufruf unter Wine (DLL muss neben der EXE oder im --dll-Pfad liegen):
//     gns_probe.exe [--port 6321] [--dll <pfad zu GameNetworkingSockets.dll>]
//
// Upstream-Header: tools/gns-proxy/include/ (Lizenz:
// GameNetworkingSockets-LICENSE.txt)

#include <steam/steamnetworkingtypes.h>
// Nur fuer die *Definition* von SteamNetConnectionStatusChangedCallback_t und
// SteamNetConnectionInfo_t — Methoden des Interfaces rufen wir bewusst nie auf
// (alles per GetProcAddress auf die Flat-Exports, damit wir an keiner
// vtable-Reihenfolge haengen).
#include <steam/isteamnetworkingsockets.h>

// Winsock2 MUSS vor windows.h stehen (windows.h zieht sonst die alte winsock.h
// und es gibt Typ-Konflikte). Die Steuer-API/Web-UI (Issue #857) braucht
// TCP-Sockets fuer den HTTP-Listener.
#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>

// Reine Routing-Logik (exakt / Suffix-Wildcard / Default) — host-testbar in der
// CI, siehe route_rules.h und test_route_rules.cpp.
#include "route_rules.h"
// Reine Helfer fuer die Steuer-API (Target-Spec, JSON) — host-testbar in der CI
// (test_api_util.cpp).
#include "api_util.h"

#include <chrono>
#include <cstdarg>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <deque>
#include <map>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <utility>
#include <vector>

namespace {

// --- Flat-API: eigene Signaturen (Referenzen = Pointer, ABI-identisch)
// --------

// steamnetworkingtypes.h deklariert die Interface-Klasse nicht (die lebt in
// isteamnetworkingsockets.h) — fuer uns genuegt die Vorwaertsdeklaration, weil
// wir nur Zeiger darauf fuehren.
class ISteamNetworkingSockets;

typedef ISteamNetworkingSockets *(*fn_SteamNetworkingSockets_v009)();

typedef bool (*fn_Init)(const SteamNetworkingIdentity *,
                        SteamNetworkingErrMsg *);
typedef void (*fn_IdentityClear)(SteamNetworkingIdentity *);
typedef bool (*fn_IdentitySetGenericString)(SteamNetworkingIdentity *,
                                            const char *);
typedef void *(*fn_UtilsAccessor)();
typedef void (*fn_SetDebugOutputFunction)(void *, int, void *);
typedef bool (*fn_SetGlobalConfigValueInt32)(void *, int, int);
typedef void (*fn_SetGlobalCallbackStatusChanged)(void *, void *);
typedef void (*fn_Kill)();
typedef HSteamListenSocket (*fn_CreateListenSocketIP)(
    ISteamNetworkingSockets *, const SteamNetworkingIPAddr *, int,
    const SteamNetworkingConfigValue_t *);
typedef bool (*fn_CloseListenSocket)(ISteamNetworkingSockets *,
                                     HSteamListenSocket);
typedef HSteamNetPollGroup (*fn_CreatePollGroup)(ISteamNetworkingSockets *);
typedef bool (*fn_DestroyPollGroup)(ISteamNetworkingSockets *,
                                   HSteamNetPollGroup);
typedef bool (*fn_SetConnectionPollGroup)(ISteamNetworkingSockets *,
                                          HSteamNetConnection,
                                          HSteamNetPollGroup);
typedef EResult (*fn_AcceptConnection)(ISteamNetworkingSockets *,
                                       HSteamNetConnection);
typedef void (*fn_CloseConnection)(ISteamNetworkingSockets *,
                                   HSteamNetConnection, int, const char *,
                                   bool);
typedef void (*fn_RunCallbacks)(ISteamNetworkingSockets *);
typedef int (*fn_ReceiveMessagesOnPollGroup)(ISteamNetworkingSockets *,
                                             HSteamNetPollGroup,
                                             SteamNetworkingMessage_t **, int);
typedef HSteamNetConnection (*fn_ConnectByIPAddress)(
    ISteamNetworkingSockets *, const SteamNetworkingIPAddr *, int,
    const SteamNetworkingConfigValue_t *);
typedef EResult (*fn_SendMessageToConnection)(ISteamNetworkingSockets *,
                                              HSteamNetConnection, const void *,
                                              uint32, int, int64 *);
typedef void (*fn_IPAddrSetIPv4)(SteamNetworkingIPAddr *, uint32, uint16);
typedef void (*fn_IdentityToString)(const SteamNetworkingIdentity *, char *,
                                    size_t);
typedef bool (*fn_SetConnConfigInt32)(void *, HSteamNetConnection, int, int32);
typedef void (*fn_IPAddrToString)(const SteamNetworkingIPAddr *, char *, size_t,
                                  bool);

fn_SteamNetworkingSockets_v009 pAccessor = nullptr;
fn_Init pInit = nullptr;
fn_IdentityClear pIdentityClear = nullptr;
fn_IdentitySetGenericString pIdentitySetGenericString = nullptr;
fn_UtilsAccessor pUtilsAccessor = nullptr;
fn_SetDebugOutputFunction pSetDebugOutputFunction = nullptr;
fn_SetGlobalConfigValueInt32 pSetGlobalConfigValueInt32 = nullptr;
fn_SetGlobalCallbackStatusChanged pSetGlobalCallbackStatusChanged = nullptr;
fn_Kill pKill = nullptr;
fn_CreateListenSocketIP pCreateListenSocketIP = nullptr;
fn_CloseListenSocket pCloseListenSocket = nullptr;
fn_CreatePollGroup pCreatePollGroup = nullptr;
// Optional (nicht in der Pflichtliste): fehlt der Export, bleiben die
// Poll-Gruppen bis zum Prozessende belegt (Issue #877).
fn_DestroyPollGroup pDestroyPollGroup = nullptr;
fn_SetConnectionPollGroup pSetConnectionPollGroup = nullptr;
fn_AcceptConnection pAcceptConnection = nullptr;
fn_CloseConnection pCloseConnection = nullptr;
fn_RunCallbacks pRunCallbacks = nullptr;
fn_ReceiveMessagesOnPollGroup pReceiveMessagesOnPollGroup = nullptr;
fn_ConnectByIPAddress pConnectByIPAddress = nullptr;
fn_SendMessageToConnection pSendMessageToConnection = nullptr;
fn_IPAddrSetIPv4 pIPAddrSetIPv4 = nullptr;
fn_IdentityToString pIdentityToString = nullptr;
fn_SetConnConfigInt32 pSetConnConfigInt32 = nullptr;
fn_IPAddrToString pIPAddrToString = nullptr;

ISteamNetworkingSockets *g_pInterface = nullptr;
int g_msgCount = 0;

// --- Relay (E3/E4) — Multi-Session (Issue #877) ------------------------------
// Terminierendes Relay: JEDER Client bekommt eine eigene Session (Client-Conn +
// optionaler Backend-Conn).  Nachrichten werden 1:1 weitergereicht.  Frueher
// lagen Verbindung/Queues/Historie in globalen Singletonen — ein zweiter Client
// ueberschrieb sie und die Sitzung des ersten kollabierte (Blocker fuer 1v1).
//
// Routing (E4): der GNS-*Identitaetsstring* des Clients kommt mit dem Connect
// (`str:<hex>`, stabil pro Installation) und ist damit sofort verfuegbar — damit
// laesst sich direkt beim Connect auf ein beliebiges Backend routen.  Der
// Spielname kommt erst nach der Handshake-Antwort; er wird daher aus dem
// durchgereichten Strom gelernt und loest bei Abweichung ein **Re-Route**
// (Replay des bis dahin Gesehenen auf das richtige Backend) aus.
struct Session {
  HSteamNetConnection clientConn = k_HSteamNetConnection_Invalid;
  HSteamNetConnection backendConn = k_HSteamNetConnection_Invalid;
  bool backendConnected = false;
  // Aktuelles Ziel als Endpoint (fuer Vergleiche beim Re-Route).
  rbroute::Endpoint backendTarget;
  // GNS-Identitaet (`str:<hex>`, stabil pro Installation) und die zuletzt
  // angewandte Namens-Regel (fuer die Re-Route-Erkennung).
  std::string identity;
  std::string lastRouteKey;
  // Historie aller Client->Server-Nachrichten (fuer Replay beim Re-Route) plus
  // Merker, wie viele davon schon an das *aktuelle* Backend gingen.
  std::vector<std::pair<std::string, int>> history;
  size_t historyBytes = 0;
  size_t historyForwarded = 0;
  // Sende-Queues + Backpressure pro Session: GNS liefert
  // `k_EResultLimitExceeded`, wenn der Sende-Puffer des Ziels voll ist (typisch:
  // 500-KB-Weltzustand vom lokalen Backend ueber eine langsame Client-Leitung).
  // Dann wird gepuffert und von der Quelle nur so lange gelesen, wie die Queue
  // der Gegenseite unter dem Soft-Limit liegt.
  std::deque<std::pair<std::string, int>> toClientQ;
  std::deque<std::pair<std::string, int>> toBackendQ;
  size_t toClientBytes = 0;
  size_t toBackendBytes = 0;
  // Eine Poll-Gruppe je Session und Richtung: GNS kennt nur
  // ReceiveMessagesOnPollGroup (kein *.OnConnection); globales Lesen wuerde die
  // Backpressure einer Session an alle anderen koppeln.
  HSteamNetPollGroup clientPoll = k_HSteamNetPollGroup_Invalid;
  HSteamNetPollGroup backendPoll = k_HSteamNetPollGroup_Invalid;
};

// Registry: die Client-Map *besitzt* die Session, die Backend-Map zeigt nur
// darauf.  Lookup beim Dispatching ueber `pMsg->m_conn` bzw. im Status-Callback.
std::map<HSteamNetConnection, std::unique_ptr<Session>> g_clientSessions;
std::map<HSteamNetConnection, Session *> g_backendSessions;

Session *sessionForClient(HSteamNetConnection conn) {
  std::map<HSteamNetConnection, std::unique_ptr<Session>>::iterator it =
      g_clientSessions.find(conn);
  return it == g_clientSessions.end() ? nullptr : it->second.get();
}

Session *sessionForBackend(HSteamNetConnection conn) {
  std::map<HSteamNetConnection, Session *>::iterator it =
      g_backendSessions.find(conn);
  return it == g_backendSessions.end() ? nullptr : it->second;
}

bool g_relayEnabled = false;
// Konfigurierter Default (--forward bzw. Default-Regel `*`).  Wird NIE durch ein
// Re-Route ueberschrieben — sonst wandert der Default zu einem anderen Backend.
rbroute::Endpoint g_defaultTarget;
// --dial: nur Verbindungstest zu einem Backend (ohne Client), fuer Diagnose.
rbroute::Endpoint g_dialTarget;

// Routen: Key = Identitaetsstring (`str:…`), Suffix-Wildcard (`*-dev`) oder
// Default (`*`) -> Backend.  Auswertung: exakt > laengster Suffix > Default.
//
// Bewusst KEIN gelerntes Identitaets->Backend-Caching mehr: die Identitaet ist
// stabil pro Installation, der Spielname aber nicht.  Ein Cache fuehrte dazu,
// dass ein Join OHNE Suffix nach einem frueheren `-staging`-Join wieder auf
// staging landete (Default wurde ignoriert; live belegt, Issue #843).
rbroute::Table g_routes;
const char *g_mapFile = nullptr;
// Grenzen pro Session: Obergrenze der Replay-Historie und Soft-Limit der
// Sende-Queue (Backpressure, siehe Session-Kommentar).
const size_t kMaxHistoryBytes = 8u * 1024 * 1024;
const size_t kQueueSoftLimit = 2u * 1024 * 1024;

// --- Logging -----------------------------------------------------------------

int g_unencrypted = 0;
int g_allow_without_auth = 2;
bool g_useIdentity = false;
// GNS bindet jedes (selbst-signierte) Zertifikat an GetAppID(); der Open-Source-
// Default ist 0, waehrend der Riftbreaker-Client 780310 sendet.  Wir patchen
// m_nAppID direkt im statischen Utils-Objekt (Offset 0x8, per Disasm verifiziert:
// vtable-Slot 26 = `mov eax,[rcx+8]; ret`).
int g_appid = 780310;

// --- Operator-UI + Steuer-API (Issue #857) -----------------------------------
// PoC: statt automatisch auf den Default zu routen, kann der Relay einen Client
// HALTEN und einen Operator in einer kleinen Web-UI entscheiden lassen
// (PROD/DEV/STAGING). Der GNS-Zustand bleibt single-threaded: der HTTP-Thread
// liest einen mutex-geschuetzten Snapshot und schreibt Befehle in eine Queue,
// die die Hauptschleife abarbeitet (Muster server/dll/rbbridge.c).
//
// `--hold`       unentschiedene Sessions halten (kein Backend-Aufbau)
// `--api-port N` HTTP-Listener der UI/API (Default nur an 127.0.0.1)
// `--target NAME=ip:port` (wiederholbar) — die Buttons der UI
bool g_hold = false;
int g_apiPort = 0;
std::string g_apiHost = "127.0.0.1";
std::vector<std::pair<std::string, rbroute::Endpoint>> g_targets;

// --- Parked-Anbindung fuer POST /solo (Issue #929) ----------------------------
// Der Relay ruft den Parked-Pool-Dienst (`POST /claim`) per WinSock-HTTP auf
// und pinnt die Identitaet auf den von dort gelieferten GNS-UDP-Endpoint. Der
// Token kommt AUS DER UMWELT (`RBB_PARKED_TOKEN`), nie aus argv (Prozessliste).
std::string g_parkedHost = "127.0.0.1";
int g_parkedPort = 8095;
// Default-Ziel ist der dev-Parked-Dienst; --parked-url ueberschreibt es.
bool g_parkedConfigured = true;
std::string g_parkedToken;
// Operator-Pin pro Identitaet — ueberlebt Reconnects (der Client schliesst nach
// ~20 s ohne Antwort selbst und verbindet neu). NUR vom Hauptloop beruehrt.
std::map<std::string, rbroute::Endpoint> g_pins;

// Anzeige-Snapshot (HTTP-Thread liest, Hauptloop schreibt) + Befehls-Queue.
struct SessionInfo {
  std::string identity;
  std::string ip;
  std::string name;
  std::string state;  // connected | held | waiting | routed | closed
  std::string target; // Endpoint des gewaehlten Backends (oder leer)
  size_t messages = 0;
  long long ageSeconds = 0;
  long long heldSeconds = 0;
  bool connected = false;
  bool pinned = false;
};
struct ApiCommand {
  // Was die Hauptschleife tun soll (die Registry `g_targets` gehoert NUR ihr).
  enum Kind {
    kRouteByName,    // Identitaet auf einen registrierten Backend-*Namen* pinnen
    kRouteByEndpoint,// Identitaet direkt auf einen Endpoint pinnen (/solo)
    kBackendAdd,     // Backend registrieren/aktualisieren
    kBackendDelete,  // Backend abmelden
  };
  Kind kind = kRouteByName;
  std::string identity;  // bei Route-Befehlen
  std::string target;    // Backend-*Name* (RouteByName) bzw. Name (Add/Delete)
  rbroute::Endpoint endpoint;  // bei Add / RouteByEndpoint
  bool hasEndpoint = false;
};
std::mutex g_apiMutex;
std::vector<SessionInfo> g_sessionsSnapshot;
// Kopie der Registry fuer den HTTP-Thread (nur unter g_apiMutex gelesen).
std::vector<std::pair<std::string, rbroute::Endpoint>> g_targetsSnapshot;
std::deque<ApiCommand> g_apiCommands;

// Laufende Session-Buchfuehrung (NUR Hauptloop). Ein Eintrag pro Identitaet,
// damit ein Reconnect denselben Spieler weiterfuehrt.
struct SessionRecord {
  std::string identity;
  std::string ip;
  std::string name;
  std::string targetStr;
  std::string state = "connected";
  size_t messages = 0;
  std::chrono::steady_clock::time_point firstSeen;
  std::chrono::steady_clock::time_point heldSince;
  bool connected = false;
  bool held = false;
};
std::map<std::string, SessionRecord> g_sessions;

// Logging serialisieren — logLine() wird jetzt auch aus dem HTTP-Thread gerufen.
std::mutex g_logMutex;

void logLine(const char *fmt, ...) {
  char stamp[32];
  time_t now = time(nullptr);
  struct tm *lt = localtime(&now);
  strftime(stamp, sizeof(stamp), "%H:%M:%S", lt);
  std::lock_guard<std::mutex> lock(g_logMutex);
  fprintf(stdout, "[%s] gns_probe: ", stamp);
  va_list args;
  va_start(args, fmt);
  vfprintf(stdout, fmt, args);
  va_end(args);
  fputc('\n', stdout);
  fflush(stdout);
}

std::string asciiPreview(const unsigned char *data, int len) {
  std::string out;
  for (int i = 0; i < len; ++i) {
    unsigned char c = data[i];
    out.push_back((c >= 32 && c < 127) ? static_cast<char>(c) : '.');
  }
  return out;
}

void hexDump(const unsigned char *data, int len) {
  const int perLine = 16;
  for (int off = 0; off < len; off += perLine) {
    int chunk = (len - off) < perLine ? (len - off) : perLine;
    char line[8 + 3 * 16 + 20];
    int written = snprintf(line, sizeof(line), "    %04x: ", off);
    for (int i = 0; i < perLine; ++i) {
      if (i < chunk) {
        written += snprintf(line + written, sizeof(line) - written, "%02x ",
                            data[off + i]);
      } else {
        written += snprintf(line + written, sizeof(line) - written, "   ");
      }
    }
    snprintf(line + written, sizeof(line) - written, " %s",
             asciiPreview(data + off, chunk).c_str());
    logLine("%s", line);
  }
}

// --- GNS-Callbacks ----------------------------------------------------------

std::string trim(const std::string &s) {
  size_t b = s.find_first_not_of(" \t\r\n");
  if (b == std::string::npos) {
    return "";
  }
  size_t e = s.find_last_not_of(" \t\r\n");
  return s.substr(b, e - b + 1);
}

// Routen-Datei: `<key> = <ip:port>` je Zeile; `key` ist ein exakter Key
// (Identitaet `str:…`, Spielname), eine Suffix-Wildcard (`*-dev`) oder der
// Default (`*`); `#` startet einen Kommentar.
void loadRoutes() {
  if (g_mapFile == nullptr) {
    return;
  }
  FILE *f = fopen(g_mapFile, "r");
  if (f == nullptr) {
    logLine("map-file '%s' nicht lesbar — fahre ohne Namensrouting fort",
            g_mapFile);
    return;
  }
  char line[600];
  int n = 0;
  while (fgets(line, sizeof(line), f) != nullptr) {
    char *hash = strchr(line, '#');
    if (hash != nullptr) {
      *hash = '\0';
    }
    char *eq = strchr(line, '=');
    if (eq == nullptr) {
      continue;
    }
    *eq = '\0';
    const std::string key = trim(line);
    rbroute::Endpoint target;
    if (key.empty() || !rbroute::parseEndpoint(trim(eq + 1), target)) {
      logLine("route ignoriert (ungenueftig): '%s'", trim(line).c_str());
      continue;
    }
    g_routes.add(key, target);
    ++n;
  }
  fclose(f);
  logLine("routen geladen: %d aus '%s'", n, g_mapFile);
  for (const rbroute::Rule &r : g_routes.rules()) {
    logLine("  route %-16s -> %s", r.key.c_str(), r.target.str().c_str());
  }
}

// Erste *spezifische* Regel finden, deren Key als Token im Payload steht
// (der Spielername steht als laengenpraefixierter Klartext im BINSER-Handshake).
// Der Default (`*`) zaehlt dabei nicht als Treffer. Ueber `matchedName` kommt
// der tatsaechlich passende Token zurueck (der gelernte Spielname fuer die UI).
const rbroute::Rule *routeFromPayload(const std::string &payload,
                                      std::string *matchedName = nullptr) {
  for (const std::string &token : rbroute::extractTokens(payload)) {
    const rbroute::Rule *rule = g_routes.matchSpecific(token);
    if (rule != nullptr) {
      if (matchedName != nullptr) {
        *matchedName = token;
      }
      return rule;
    }
  }
  return nullptr;
}

// Senden mit Backpressure: ist der GNS-Sende-Puffer des Ziels voll
// (k_EResultLimitExceeded), bleibt die Nachricht in der Queue und wird im
// naechsten Durchlauf erneut versucht.
bool flushQueue(std::deque<std::pair<std::string, int>> &q, size_t &bytes,
                HSteamNetConnection conn) {
  while (!q.empty()) {
    if (conn == k_HSteamNetConnection_Invalid) {
      return false;
    }
    const std::pair<std::string, int> &m = q.front();
    int64 out = 0;
    const EResult r = pSendMessageToConnection(
        g_pInterface, conn, m.first.data(),
        static_cast<uint32>(m.first.size()), m.second, &out);
    if (r == k_EResultOK) {
      bytes -= m.first.size();
      q.pop_front();
      continue;
    }
    if (r == k_EResultLimitExceeded) {
      return false; // Sendepuffer voll — spaeter erneut
    }
    logLine("send an conn=%u fehlgeschlagen (EResult=%d) — verworfen (%zu B)",
            conn, static_cast<int>(r), m.first.size());
    bytes -= m.first.size();
    q.pop_front();
  }
  return true;
}

// Alles, was der Client geschickt hat und dem *aktuellen* Backend noch nicht
// vorliegt, in die Backend-Queue schieben (Backlog bzw. Replay nach Re-Route).
void queueHistoryDelta(Session &s) {
  size_t n = 0;
  while (s.historyForwarded < s.history.size()) {
    const std::pair<std::string, int> &m = s.history[s.historyForwarded];
    s.toBackendQ.push_back(m);
    s.toBackendBytes += m.first.size();
    ++s.historyForwarded;
    ++n;
  }
  if (n > 0) {
    logLine("replay/backlog: %zu Nachricht(en) -> backend-queue (%zu offen)",
            n, s.toBackendQ.size());
  }
}

void startBackendConnect(Session &s) {
  if (pConnectByIPAddress == nullptr || pIPAddrSetIPv4 == nullptr) {
    logLine("relay: ConnectByIPAddress/SetIPv4-Export fehlt");
    return;
  }
  uint32 ip = 0;
  if (!rbroute::ipToU32(s.backendTarget.ip, ip)) {
    logLine("relay: ungueltige Backend-IP '%s'", s.backendTarget.ip.c_str());
    return;
  }
  if (s.backendPoll == k_HSteamNetPollGroup_Invalid) {
    s.backendPoll = pCreatePollGroup(g_pInterface);
  }
  SteamNetworkingIPAddr addr;
  memset(&addr, 0, sizeof(addr));
  pIPAddrSetIPv4(&addr, ip, s.backendTarget.port);
  s.backendConn = pConnectByIPAddress(g_pInterface, &addr, 0, nullptr);
  if (s.backendConn == k_HSteamNetConnection_Invalid) {
    logLine("backend-connect auf %s FEHLGESCHLAGEN",
            s.backendTarget.str().c_str());
    return;
  }
  g_backendSessions[s.backendConn] = &s;
  pSetConnectionPollGroup(g_pInterface, s.backendConn, s.backendPoll);
  if (pSetConnConfigInt32 != nullptr) {
    // Default ist 512 KiB — ein einzelner Weltzustand ist ~500 KiB, mit
    // vorangehenden Nachrichten laeuft der Puffer sonst sofort voll.
    pSetConnConfigInt32(pUtilsAccessor(), s.backendConn,
                        k_ESteamNetworkingConfig_SendBufferSize, 8 * 1024 * 1024);
  }
  logLine("backend-connect gestartet -> conn=%u (%s)", s.backendConn,
          s.backendTarget.str().c_str());
}

void routeTo(Session &s, const rbroute::Endpoint &target, const char *why) {
  logLine("ROUTE (%s) -> %s", why, target.str().c_str());
  if (s.backendConn != k_HSteamNetConnection_Invalid) {
    g_backendSessions.erase(s.backendConn);
    pCloseConnection(g_pInterface, s.backendConn, 0, nullptr, false);
    s.backendConn = k_HSteamNetConnection_Invalid;
    s.backendConnected = false;
  }
  s.backendTarget = target;
  // Alles Gesehene erneut an das (neue) Backend schicken — aber erst, wenn es
  // verbunden ist (siehe Callback), sonst flutet ein Replay den Handshake.
  s.toBackendQ.clear();
  s.toBackendBytes = 0;
  s.historyForwarded = 0;
  startBackendConnect(s);
}

// Neue Session fuer einen akzeptierten Client — frischer Zustand (Historie und
// Queues leer), damit das vorige Match nicht in das neue Backend flutet.
Session &createClientSession(HSteamNetConnection clientConn) {
  std::unique_ptr<Session> owned(new Session());
  owned->clientConn = clientConn;
  owned->clientPoll = pCreatePollGroup(g_pInterface);
  Session *s = owned.get();
  g_clientSessions[clientConn] = std::move(owned);
  return *s;
}

// Session eines beendeten Clients aufraeumen: Backend schliessen, Poll-Gruppen
// freigeben (Export optional — s. pDestroyPollGroup) und die Session loeschen.
void destroyClientSession(HSteamNetConnection clientConn) {
  std::map<HSteamNetConnection, std::unique_ptr<Session>>::iterator it =
      g_clientSessions.find(clientConn);
  if (it == g_clientSessions.end()) {
    return;
  }
  Session &s = *it->second;
  if (s.backendConn != k_HSteamNetConnection_Invalid) {
    g_backendSessions.erase(s.backendConn);
    pCloseConnection(g_pInterface, s.backendConn, 0, nullptr, false);
    s.backendConn = k_HSteamNetConnection_Invalid;
    s.backendConnected = false;
  }
  if (pDestroyPollGroup != nullptr) {
    if (s.clientPoll != k_HSteamNetPollGroup_Invalid) {
      pDestroyPollGroup(g_pInterface, s.clientPoll);
    }
    if (s.backendPoll != k_HSteamNetPollGroup_Invalid) {
      pDestroyPollGroup(g_pInterface, s.backendPoll);
    }
  }
  g_clientSessions.erase(it);
}

// --- Session-Buchfuehrung (Issue #857) ---------------------------------------

// Eintrag pro Identitaet anlegen/finden — ein Reconnect fuehrt denselben Spieler
// weiter (die Identitaet ist stabil pro Installation).
SessionRecord &sessionFor(const std::string &identity) {
  std::map<std::string, SessionRecord>::iterator it = g_sessions.find(identity);
  if (it == g_sessions.end()) {
    SessionRecord rec;
    rec.identity = identity;
    rec.firstSeen = std::chrono::steady_clock::now();
    it = g_sessions.emplace(identity, rec).first;
  }
  return it->second;
}

// Snapshot fuer den HTTP-Thread bauen (NUR Hauptloop ruft das).
void refreshSnapshot() {
  const std::chrono::steady_clock::time_point now =
      std::chrono::steady_clock::now();
  std::vector<SessionInfo> snap;
  snap.reserve(g_sessions.size());
  for (std::map<std::string, SessionRecord>::const_iterator it =
           g_sessions.begin();
       it != g_sessions.end(); ++it) {
    const SessionRecord &rec = it->second;
    SessionInfo s;
    s.identity = rec.identity;
    s.ip = rec.ip;
    s.name = rec.name;
    s.state = rec.state;
    s.target = rec.targetStr;
    s.messages = rec.messages;
    s.connected = rec.connected;
    s.pinned = g_pins.find(rec.identity) != g_pins.end();
    s.ageSeconds = std::chrono::duration_cast<std::chrono::seconds>(
                       now - rec.firstSeen)
                       .count();
    s.heldSeconds =
        rec.held
            ? std::chrono::duration_cast<std::chrono::seconds>(now -
                                                               rec.heldSince)
                  .count()
            : 0;
    snap.push_back(s);
  }
  std::lock_guard<std::mutex> lock(g_apiMutex);
  g_sessionsSnapshot.swap(snap);
}

// GNS' eigene Diagnose-Ausgabe (via SetDebugOutputFunction) — zeigt, warum ein
// Handshake-Schritt verworfen wird.
void onGnsDebug(int level, const char *msg) {
  logLine("GNS[%d] %s", level, msg);
}

void onConnectionStatusChanged(
    SteamNetConnectionStatusChangedCallback_t *pInfo) {
  const SteamNetConnectionInfo_t &info = pInfo->m_info;
  logLine("STATUS conn=%u state=%d '%s' endReason=%d endDebug='%s'",
          pInfo->m_hConn, static_cast<int>(info.m_eState),
          info.m_szConnectionDescription, info.m_eEndReason, info.m_szEndDebug);

  switch (info.m_eState) {
  case k_ESteamNetworkingConnectionState_Connecting: {
    if (sessionForBackend(pInfo->m_hConn) != nullptr) {
      logLine("backend: Connecting (ausgehend)");
      break;
    }
    if (pAcceptConnection(g_pInterface, pInfo->m_hConn) != k_EResultOK) {
      logLine("AcceptConnection FEHLGESCHLAGEN -> CloseConnection");
      pCloseConnection(g_pInterface, pInfo->m_hConn, 0, nullptr, false);
      break;
    }
    Session &s = createClientSession(pInfo->m_hConn);
    if (!pSetConnectionPollGroup(g_pInterface, pInfo->m_hConn, s.clientPoll)) {
      logLine("SetConnectionPollGroup FEHLGESCHLAGEN (weiter trotzdem)");
    }
    if (pSetConnConfigInt32 != nullptr) {
      pSetConnConfigInt32(pUtilsAccessor(), pInfo->m_hConn,
                          k_ESteamNetworkingConfig_SendBufferSize,
                          8 * 1024 * 1024);
    }
    logLine("E1: Verbindung AKZEPTIERT (Handshake laeuft) — %zu Session(s)",
            g_clientSessions.size());
    break;
  }
  case k_ESteamNetworkingConnectionState_Connected: {
    Session *backendSess = sessionForBackend(pInfo->m_hConn);
    if (backendSess != nullptr) {
      logLine("backend: Connected");
      backendSess->backendConnected = true;
      queueHistoryDelta(*backendSess);
      break;
    }
    logLine("E1 GRUEN: state=Connected — der Client akzeptiert einen fremden "
            "GNS-Server");
    Session *s = sessionForClient(pInfo->m_hConn);
    if (s == nullptr || !g_relayEnabled) {
      break;
    }
    // Identitaet kommt sofort mit dem Connect und ist stabil pro Installation
    // -> damit koennen wir direkt routen, ohne auf den Spielnamen zu warten.
    char ident[256] = {0};
    if (pIdentityToString != nullptr) {
      pIdentityToString(&info.m_identityRemote, ident, sizeof(ident));
    }
    char remote[64] = {0};
    if (pIPAddrToString != nullptr) {
      pIPAddrToString(&info.m_addrRemote, remote, sizeof(remote), true);
    }
    s->identity = ident;
    s->lastRouteKey.clear();
    logLine("client-identitaet: '%s' (%s) — %zu parallele Session(s)",
            s->identity.c_str(), remote, g_clientSessions.size());

    // Session-Buchfuehrung (fuer die Operator-UI, Issue #857).
    SessionRecord &rec = sessionFor(s->identity);
    rec.ip = remote;
    rec.connected = true;
    rec.held = false;
    rec.state = "connected";

    const std::map<std::string, rbroute::Endpoint>::iterator pin =
        g_pins.find(s->identity);
    const rbroute::Rule *rule = g_routes.matchSpecific(s->identity);
    if (pin != g_pins.end()) {
      // Operator hat diese Identitaet bereits festgelegt -> ueberlebt Reconnect.
      rec.state = "routed";
      rec.targetStr = pin->second.str();
      routeTo(*s, pin->second, "operator-pin");
    } else if (rule != nullptr) {
      rec.state = "routed";
      rec.targetStr = rule->target.str();
      routeTo(*s, rule->target, "identitaet (explizite Regel)");
    } else if (g_hold) {
      // Halten: KEIN Backend-Aufbau. Der Client bleibt im Loading, seine
      // Nachrichten laufen in die Historie; der Operator entscheidet spaeter.
      rec.held = true;
      rec.heldSince = std::chrono::steady_clock::now();
      rec.state = "held";
      s->backendTarget = rbroute::Endpoint{};
      logLine("HOLD: halte '%s' (%s) — warte auf Operator (Ziel-Buttons: %zu)",
              s->identity.c_str(), remote, g_targets.size());
    } else {
      rec.state = "routed";
      rec.targetStr = g_defaultTarget.str();
      routeTo(*s, g_defaultTarget, "default (bis der Name ihn ggf. umroutet)");
    }
    break;
  }
  case k_ESteamNetworkingConnectionState_ClosedByPeer:
  case k_ESteamNetworkingConnectionState_ProblemDetectedLocally: {
    Session *backendSess = sessionForBackend(pInfo->m_hConn);
    if (backendSess != nullptr) {
      logLine("backend beendet (state=%d) — Client bleibt, Session kann neu "
              "routen",
              static_cast<int>(info.m_eState));
      g_backendSessions.erase(pInfo->m_hConn);
      backendSess->backendConn = k_HSteamNetConnection_Invalid;
      backendSess->backendConnected = false;
      pCloseConnection(g_pInterface, pInfo->m_hConn, 0, nullptr, false);
      break;
    }
    Session *s = sessionForClient(pInfo->m_hConn);
    if (s == nullptr) {
      pCloseConnection(g_pInterface, pInfo->m_hConn, 0, nullptr, false);
      break;
    }
    logLine("client beendet (state=%d) — Session aufraeumen",
            static_cast<int>(info.m_eState));
    // Halte-Dauer messen (Kernfrage des PoC: wie lange toleriert der Client
    // das Warten?) und die Session fuer die UI als wartend/geschlossen zeigen.
    if (!s->identity.empty()) {
      SessionRecord &rec = sessionFor(s->identity);
      rec.connected = false;
      if (rec.held) {
        const long long held =
            std::chrono::duration_cast<std::chrono::seconds>(
                std::chrono::steady_clock::now() - rec.heldSince)
                .count();
        logLine("HOLD: '%s' nach %llds getrennt (Client-Timeout) — wartet "
                "auf Reconnect (Pin ueberlebt)",
                s->identity.c_str(), held);
        rec.state = "waiting";
      } else {
        rec.state = "closed";
      }
    }
    destroyClientSession(pInfo->m_hConn);
    pCloseConnection(g_pInterface, pInfo->m_hConn, 0, nullptr, false);
    break;
  }
  default:
    break;
  }
}

// Nachrichten einer Richtung einsammeln und in die Queue der Gegenseite legen.
// Die Poll-Gruppe gehoert genau einer Session — `fromClient` = true: Client ->
// Backend, false: Backend -> Client.
void drainFrom(Session &s, HSteamNetPollGroup group, bool fromClient) {
  for (;;) {
    SteamNetworkingMessage_t *pMsg = nullptr;
    const int count =
        pReceiveMessagesOnPollGroup(g_pInterface, group, &pMsg, 1);
    if (count <= 0 || pMsg == nullptr) {
      break;
    }
    ++g_msgCount;
    const unsigned char *data =
        static_cast<const unsigned char *>(pMsg->m_pData);
    const size_t size = static_cast<size_t>(pMsg->m_cbSize);
    const int flags = pMsg->m_nFlags;
    const unsigned int conn = pMsg->m_conn;
    logLine("MSG #%d conn=%u size=%zu flags=0x%x %s", g_msgCount, conn, size,
            flags, fromClient ? "[C->S]" : "[S->C]");
    if (size <= 256) {
      hexDump(data, pMsg->m_cbSize);
    } else {
      logLine("     (gekuerzt) %s ...",
              asciiPreview(data, 48).c_str());
    }
    std::string payload(reinterpret_cast<const char *>(data), size);
    pMsg->Release();

    if (!g_relayEnabled) {
      continue;
    }
    if (fromClient) {
      // Historie fuer Replay (Backlog UND spaeteres Re-Route) der Session fuehren.
      if (s.historyBytes + size <= kMaxHistoryBytes) {
        s.history.emplace_back(payload, flags);
        s.historyBytes += size;
      } else {
        logLine("  -> HISTORIE VOLL — nicht replay-faehig (%zu B)", size);
      }
      // Session-Buchfuehrung fuer die UI (Issue #857): Nachrichten zaehlen.
      std::map<std::string, SessionRecord>::iterator rec =
          g_sessions.find(s.identity);
      if (rec != g_sessions.end()) {
        ++rec->second.messages;
      }
      // Spielname lernen und bei Bedarf auf das richtige Backend umziehen.
      std::string learnedName;
      const rbroute::Rule *rule = routeFromPayload(payload, &learnedName);
      if (rec != g_sessions.end() && !learnedName.empty() &&
          learnedName.compare(0, 4, "str:") != 0 &&
          learnedName.compare(0, 8, "steamid:") != 0 &&
          rec->second.name.empty()) {
        rec->second.name = learnedName;
      }
      if (rule != nullptr && rule->key != s.lastRouteKey) {
        s.lastRouteKey = rule->key;
        if (!(rule->target == s.backendTarget)) {
          logLine("NAME ROUTE: '%s' -> %s (re-route)", rule->key.c_str(),
                  rule->target.str().c_str());
          routeTo(s, rule->target, "name");
        } else {
          logLine("NAME ROUTE: '%s' -> schon richtiges backend %s",
                  rule->key.c_str(), rule->target.str().c_str());
        }
        if (rec != g_sessions.end()) {
          rec->second.state = "routed";
          rec->second.held = false;
          rec->second.targetStr = rule->target.str();
        }
      }
      queueHistoryDelta(s);
    } else {
      s.toClientQ.emplace_back(payload, flags);
      s.toClientBytes += size;
    }
    // Quelle anhalten, wenn die Gegenseite schon genug Daten hat.
    if (s.toClientBytes >= kQueueSoftLimit ||
        s.toBackendBytes >= kQueueSoftLimit) {
      break;
    }
  }
}

// --- Steuer-API + Web-UI (Issue #857) ---------------------------------------
//
// Ein einzelner HTTP-Thread bedient `/`, `/sessions`, `/targets` und
// `/route`. Er teilt sich mit der GNS-Hauptschleife NUR ueber den
// mutex-geschuetzten Snapshot (lesen) und die Command-Queue (schreiben) — der
// GNS-Zustand bleibt single-threaded (Muster server/dll/rbbridge.c).
//
// Bewusst ohne Auth und standardmaessig nur an 127.0.0.1 gebunden: wer die API
// erreicht, darf routen. Zugriff von aussen per SSH-Tunnel.

// Backend registrieren/aktualisieren (Name ist der Schluessel). NUR Hauptloop.
void upsertTarget(const std::string &name, const rbroute::Endpoint &endpoint) {
  for (std::size_t i = 0; i < g_targets.size(); ++i) {
    if (g_targets[i].first == name) {
      g_targets[i].second = endpoint;
      return;
    }
  }
  g_targets.emplace_back(name, endpoint);
}

// Backend abmelden. `g_pins` bleiben bewusst unangetastet (bestehende Pins
// zeigen weiter auf den Endpoint, auch wenn der Name wegfällt). NUR Hauptloop.
bool removeTarget(const std::string &name) {
  for (std::vector<std::pair<std::string, rbroute::Endpoint>>::iterator it =
           g_targets.begin();
       it != g_targets.end(); ++it) {
    if (it->first == name) {
      g_targets.erase(it);
      return true;
    }
  }
  return false;
}

// Identitaet auf einen Endpoint pinnen und alle laufenden Sessions umziehen
// (identische Semantik wie das fruehere /route, aber mit aufgeloestem Ziel).
void pinIdentityTo(const std::string &identity, const rbroute::Endpoint &target,
                   const char *why) {
  g_pins[identity] = target;
  logLine("API: pin '%s' -> %s", identity.c_str(), target.str().c_str());
  for (std::map<HSteamNetConnection, std::unique_ptr<Session>>::iterator st =
           g_clientSessions.begin();
       st != g_clientSessions.end(); ++st) {
    Session &s = *st->second;
    if (s.identity != identity ||
        s.clientConn == k_HSteamNetConnection_Invalid) {
      continue;
    }
    routeTo(s, target, why);
    SessionRecord &rec = sessionFor(identity);
    rec.state = "routed";
    rec.held = false;
    rec.targetStr = target.str();
  }
}

// Registry-Kopie fuer den HTTP-Thread (/targets liest nur diesen Snapshot).
void refreshTargetsSnapshot() {
  std::vector<std::pair<std::string, rbroute::Endpoint>> copy = g_targets;
  std::lock_guard<std::mutex> lock(g_apiMutex);
  g_targetsSnapshot.swap(copy);
}

void processApiCommands() {
  std::deque<ApiCommand> cmds;
  {
    std::lock_guard<std::mutex> lock(g_apiMutex);
    if (g_apiCommands.empty()) {
      return;
    }
    cmds.swap(g_apiCommands);
  }
  for (std::deque<ApiCommand>::const_iterator it = cmds.begin();
       it != cmds.end(); ++it) {
    switch (it->kind) {
    case ApiCommand::kBackendAdd:
      upsertTarget(it->target, it->endpoint);
      logLine("API: backend '%s' -> %s (registriert)", it->target.c_str(),
              it->endpoint.str().c_str());
      break;
    case ApiCommand::kBackendDelete:
      if (removeTarget(it->target)) {
        logLine("API: backend '%s' entfernt", it->target.c_str());
      } else {
        logLine("API: backend '%s' nicht vorhanden — ignoriert",
                it->target.c_str());
      }
      break;
    case ApiCommand::kRouteByEndpoint:
      // /solo: Ziel kommt aufgeloest vom Parked-Dienst (GNS-UDP-Endpoint).
      pinIdentityTo(it->identity, it->endpoint, "solo (parked)");
      break;
    case ApiCommand::kRouteByName: {
      rbroute::Endpoint target;
      bool found = false;
      for (std::size_t i = 0; i < g_targets.size(); ++i) {
        if (g_targets[i].first == it->target) {
          target = g_targets[i].second;
          found = true;
          break;
        }
      }
      if (!found) {
        logLine("API: unbekanntes Ziel '%s' — ignoriert", it->target.c_str());
        continue;
      }
      pinIdentityTo(it->identity, target, "operator (web-ui)");
      break;
    }
    }
  }
  refreshTargetsSnapshot();
}

std::string buildSessionsJson() {
  std::vector<SessionInfo> snap;
  {
    std::lock_guard<std::mutex> lock(g_apiMutex);
    snap = g_sessionsSnapshot;
  }
  std::string json = "[";
  bool first = true;
  for (std::vector<SessionInfo>::const_iterator it = snap.begin();
       it != snap.end(); ++it) {
    if (!first) {
      json += ",";
    }
    first = false;
    json += "{\"identity\":\"" + rbapi::jsonEscape(it->identity) + "\"";
    json += ",\"ip\":\"" + rbapi::jsonEscape(it->ip) + "\"";
    json += ",\"name\":\"" + rbapi::jsonEscape(it->name) + "\"";
    json += ",\"state\":\"" + rbapi::jsonEscape(it->state) + "\"";
    json += ",\"target\":\"" + rbapi::jsonEscape(it->target) + "\"";
    json += std::string(",\"connected\":") + (it->connected ? "true" : "false");
    json += std::string(",\"pinned\":") + (it->pinned ? "true" : "false");
    json += ",\"messages\":" + std::to_string(it->messages);
    json += ",\"age_seconds\":" + std::to_string(it->ageSeconds);
    json += ",\"held_seconds\":" + std::to_string(it->heldSeconds);
    json += "}";
  }
  json += "]";
  return json;
}

std::string buildTargetsJson() {
  std::vector<std::pair<std::string, rbroute::Endpoint>> snap;
  {
    std::lock_guard<std::mutex> lock(g_apiMutex);
    snap = g_targetsSnapshot;
  }
  std::string json = "[";
  bool first = true;
  for (std::size_t i = 0; i < snap.size(); ++i) {
    if (!first) {
      json += ",";
    }
    first = false;
    json += "{\"name\":\"" + rbapi::jsonEscape(snap[i].first) + "\"";
    json += ",\"endpoint\":\"" + rbapi::jsonEscape(snap[i].second.str()) +
            "\"}";
  }
  json += "]";
  return json;
}

// Single-File-UI (eingebettet, kein Auth — PoC). Pollt /sessions und postet
// die Zielwahl an /route.
const char kUiHtml[] = R"HTML(<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Riftbreaker Proxy - Lobby</title>
<style>
  :root {
    color-scheme: dark;
    --bg:#0e1013; --panel:#171a1f; --panel2:#1d2127; --line:#272c34;
    --fg:#e7eaee; --muted:#8b939f; --accent:#4ea1ff;
    --held:#f2c14e; --waiting:#f08a8a; --routed:#5fd39a;
  }
  * { box-sizing: border-box; }
  body { margin:0; min-height:100vh; color:var(--fg);
    background: radial-gradient(1200px 600px at 70% -10%, #1a2230 0%, var(--bg) 55%);
    font: 15px/1.45 ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }
  header { display:flex; align-items:center; gap:14px; padding:18px 28px;
    border-bottom:1px solid var(--line); position:sticky; top:0; z-index:5;
    background:rgba(14,16,19,.85); backdrop-filter:blur(8px); }
  h1 { font-size:17px; margin:0; font-weight:650; letter-spacing:.2px; }
  .dot { width:9px; height:9px; border-radius:50%; background:var(--routed);
    box-shadow:0 0 0 4px rgba(95,211,154,.15); }
  .dot.off { background:var(--waiting); box-shadow:0 0 0 4px rgba(240,138,138,.15); }
  .sub { color:var(--muted); font-size:13px; }
  .grow { flex:1; }
  .stats { display:flex; gap:18px; font-size:13px; color:var(--muted); }
  .stats b { color:var(--fg); font-weight:650; }
  main { padding:24px 28px 60px; max-width:1200px; margin:0 auto; }
  .grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(340px, 1fr)); gap:16px; }
  .card { border:1px solid var(--line); border-radius:14px; padding:16px 16px 14px;
    background:linear-gradient(180deg, var(--panel), #14171b);
    box-shadow:0 8px 24px rgba(0,0,0,.25); }
  .card.held { border-color:rgba(242,193,78,.45); }
  .card.waiting { opacity:.85; }
  .row { display:flex; align-items:center; gap:10px; margin-bottom:8px; }
  .who { flex:1; font-size:14px; font-weight:600; overflow:hidden;
    text-overflow:ellipsis; white-space:nowrap;
    font-family:ui-monospace, SFMono-Regular, Menlo, monospace; }
  .badge { font-size:11px; letter-spacing:.06em; text-transform:uppercase;
    padding:3px 9px; border-radius:999px; white-space:nowrap;
    background:var(--panel2); color:var(--muted); }
  .badge.held { background:rgba(242,193,78,.16); color:var(--held); }
  .badge.waiting { background:rgba(240,138,138,.16); color:var(--waiting); }
  .badge.routed { background:rgba(95,211,154,.16); color:var(--routed); }
  .meta { display:flex; flex-wrap:wrap; gap:6px 16px; margin-bottom:12px;
    color:var(--muted); font-size:12.5px; }
  .meta b { color:var(--fg); font-weight:600; }
  .actions { display:flex; gap:8px; flex-wrap:wrap; }
  button { flex:1; min-width:88px; padding:10px 12px; cursor:pointer;
    font:600 13px/1 inherit; color:#dbe6f5; border:1px solid #2f3a49;
    border-radius:10px; background:#1b2129; transition:background .12s, border-color .12s; }
  button:hover { background:#232c38; border-color:var(--accent); }
  button:active { transform:translateY(1px); }
  button:disabled { opacity:.45; cursor:default; }
  .empty { text-align:center; padding:80px 20px; color:var(--muted);
    border:1px dashed var(--line); border-radius:14px; }
  .tick { font-variant-numeric:tabular-nums; }
</style>
</head>
<body>
<header>
  <span class="dot" id="dot"></span>
  <h1>Riftbreaker Proxy - Lobby</h1>
  <span class="sub">Wartende Spieler einem Server zuweisen</span>
  <span class="grow"></span>
  <div class="stats">
    <span>wartend <b id="n-wait">0</b></span>
    <span>verbunden <b id="n-conn">0</b></span>
    <span>geroutet <b id="n-routed">0</b></span>
  </div>
</header>
<main>
  <div class="grid" id="grid"><div class="empty">lade...</div></div>
</main>
<script>
let TARGETS = [];
const ORDER = { held: 0, waiting: 1, connected: 2, closed: 3, routed: 4 };
const LABEL = { held: "wartet", waiting: "getrennt", connected: "verbunden", closed: "getrennt", routed: "geroutet" };
const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
};

async function loadTargets() {
  try { TARGETS = await (await fetch("/targets")).json(); } catch (e) { TARGETS = []; }
}

async function route(identity, target, btn) {
  btn.disabled = true;
  try {
    await fetch("/route", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ identitaet: identity, target: target }) });
  } catch (e) {}
  setTimeout(loadSessions, 250);
}

function card(s) {
  const c = el("div", "card " + s.state);
  const top = el("div", "row");
  top.appendChild(el("span", "who", s.name || s.identity));
  const badge = el("span", "badge " + s.state, LABEL[s.state] || s.state);
  if (s.pinned) badge.textContent += " - pin";
  top.appendChild(badge);
  c.appendChild(top);

  const m1 = el("div", "meta");
  const a = el("span"); a.append("ID ", el("b", null, s.identity)); m1.appendChild(a);
  const b = el("span"); b.append("IP ", el("b", null, s.ip || "-")); m1.appendChild(b);
  c.appendChild(m1);

  const m2 = el("div", "meta");
  const f = el("span"); f.append("Frames ", el("b", null, String(s.messages))); m2.appendChild(f);
  const age = el("span"); age.append("seit ", el("b", "tick", (s.age_seconds || 0) + "s")); m2.appendChild(age);
  if (s.state === "held" || s.state === "waiting") {
    const h = el("span"); h.append("haelt ", el("b", "tick", (s.held_seconds || 0) + "s")); m2.appendChild(h);
  }
  if (s.target) { const t = el("span"); t.append("Ziel ", el("b", null, s.target)); m2.appendChild(t); }
  c.appendChild(m2);

  const act = el("div", "actions");
  for (const t of TARGETS) {
    const btn = el("button", null, t.name);
    btn.title = t.endpoint;
    btn.onclick = () => route(s.identity, t.name, btn);
    act.appendChild(btn);
  }
  if (!TARGETS.length) act.appendChild(el("span", "sub", "keine --target gesetzt"));
  c.appendChild(act);
  return c;
}

async function loadSessions() {
  let rows;
  try { rows = await (await fetch("/sessions")).json(); }
  catch (e) { $("dot").classList.add("off"); return; }
  $("dot").classList.remove("off");
  rows.sort((x, y) => (ORDER[x.state] ?? 9) - (ORDER[y.state] ?? 9));

  $("n-wait").textContent = rows.filter(r => r.state === "held" || r.state === "waiting").length;
  $("n-conn").textContent = rows.filter(r => r.connected).length;
  $("n-routed").textContent = rows.filter(r => r.state === "routed").length;

  const g = $("grid");
  g.innerHTML = "";
  if (!rows.length) { g.appendChild(el("div", "empty", "niemand verbunden - warte auf Joins")); return; }
  for (const s of rows) g.appendChild(card(s));
}

loadTargets().then(loadSessions);
setInterval(loadSessions, 1500);
</script>
</body>
</html>
)HTML";

void httpSendAll(SOCKET s, const std::string &data) {
  std::size_t off = 0;
  while (off < data.size()) {
    const int n = send(s, data.data() + off,
                       static_cast<int>(data.size() - off), 0);
    if (n <= 0) {
      break;
    }
    off += static_cast<std::size_t>(n);
  }
}

void httpRespond(SOCKET s, int code, const char *status, const char *ctype,
                 const std::string &body) {
  std::string head = "HTTP/1.1 " + std::to_string(code) + " " + status + "\r\n";
  head += "Content-Type: " + std::string(ctype) + "\r\n";
  head += "Content-Length: " + std::to_string(body.size()) + "\r\n";
  head += "Cache-Control: no-store\r\nConnection: close\r\n\r\n";
  httpSendAll(s, head);
  httpSendAll(s, body);
}

void httpRespondJson(SOCKET s, int code, const char *status,
                     const std::string &body) {
  httpRespond(s, code, status, "application/json; charset=utf-8", body);
}

// --- Outbound-HTTP Relay -> Parked (Issue #929) ------------------------------
//
// Blockierender WinSock-Client MIT connect-/recv-Timeout. Er laeuft im
// HTTP-Request-Thread (jeder Request hat einen eigenen Thread) — der
// GNS-Hauptloop wird dadurch NICHT blockiert. Status == -1 signalisiert einen
// Verbindungs-/Timeout-Fehler (der /solo-Retry-Pfad behandelt das als transient).
struct OutboundResult {
  int status = -1;
  std::string body;
};

OutboundResult outboundHttpPost(const std::string &host, int port,
                                const std::string &path,
                                const std::string &payload,
                                const std::string &bearer,
                                int connectTimeoutMs, int recvTimeoutMs) {
  OutboundResult result;
  SOCKET sock = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
  if (sock == INVALID_SOCKET) {
    return result;
  }
  sockaddr_in addr;
  memset(&addr, 0, sizeof(addr));
  addr.sin_family = AF_INET;
  addr.sin_port = htons(static_cast<u_short>(port));
  if (inet_pton(AF_INET, host.c_str(), &addr.sin_addr) != 1) {
    closesocket(sock);
    return result;
  }

  // Nicht-blockierender connect + select-Timeout (haengt sonst bei blackhole).
  u_long nonBlocking = 1;
  ioctlsocket(sock, FIONBIO, &nonBlocking);
  int rc = connect(sock, reinterpret_cast<sockaddr *>(&addr), sizeof(addr));
  if (rc == SOCKET_ERROR) {
    if (WSAGetLastError() != WSAEWOULDBLOCK) {
      closesocket(sock);
      return result;
    }
    fd_set wr;
    FD_ZERO(&wr);
    FD_SET(sock, &wr);
    timeval tv;
    tv.tv_sec = connectTimeoutMs / 1000;
    tv.tv_usec = (connectTimeoutMs % 1000) * 1000;
    rc = select(0, nullptr, &wr, nullptr, &tv);
    if (rc <= 0) {
      closesocket(sock);
      return result;
    }
    int soErr = 0;
    int soErrLen = sizeof(soErr);
    if (getsockopt(sock, SOL_SOCKET, SO_ERROR,
                   reinterpret_cast<char *>(&soErr), &soErrLen) != 0 ||
        soErr != 0) {
      closesocket(sock);
      return result;
    }
  }
  nonBlocking = 0;
  ioctlsocket(sock, FIONBIO, &nonBlocking);
  DWORD recvTimeout = static_cast<DWORD>(recvTimeoutMs);
  setsockopt(sock, SOL_SOCKET, SO_RCVTIMEO,
             reinterpret_cast<const char *>(&recvTimeout),
             sizeof(recvTimeout));
  DWORD sendTimeout = static_cast<DWORD>(recvTimeoutMs);
  setsockopt(sock, SOL_SOCKET, SO_SNDTIMEO,
             reinterpret_cast<const char *>(&sendTimeout),
             sizeof(sendTimeout));

  std::string req = "POST " + path + " HTTP/1.1\r\n";
  req += "Host: " + host + ":" + std::to_string(port) + "\r\n";
  req += "Content-Type: application/json\r\n";
  req += "Content-Length: " + std::to_string(payload.size()) + "\r\n";
  if (!bearer.empty()) {
    req += "Authorization: Bearer " + bearer + "\r\n";
  }
  req += "Connection: close\r\n\r\n";
  req += payload;

  std::size_t off = 0;
  while (off < req.size()) {
    const int n = send(sock, req.data() + off,
                       static_cast<int>(req.size() - off), 0);
    if (n <= 0) {
      closesocket(sock);
      return result;
    }
    off += static_cast<std::size_t>(n);
  }

  std::string raw;
  char buf[4096];
  for (;;) {
    const int n = recv(sock, buf, sizeof(buf), 0);
    if (n <= 0) {
      break;
    }
    raw.append(buf, static_cast<std::size_t>(n));
    if (raw.size() > (1u << 20)) {
      break;
    }
  }
  closesocket(sock);

  int code = 0;
  std::string body;
  if (!rbapi::parseHttpResponse(raw, code, body)) {
    return result;  // Status bleibt -1 -> transienter Fehler
  }
  result.status = code;
  result.body = body;
  return result;
}

// POST /solo: Parked `POST /claim` aufrufen (Bearer), Ziel von dort holen und
// die Identitaet per Command-Queue darauf pinnen. Retry/Backoff nur HIER im
// Request-Thread (transiente Fehler: Backend faehrt noch hoch).
void handleSolo(SOCKET s, const std::string &requestBody) {
  std::string identity;
  std::string env;
  if (!rbapi::jsonStringField(requestBody, "identitaet", identity) ||
      identity.empty()) {
    httpRespondJson(s, 400, "Bad Request",
                    "{\"ok\":false,\"reason\":\"bad_request\",\"detail\":\"identitaet fehlt\"}");
    return;
  }
  rbapi::jsonStringField(requestBody, "env", env);

  if (!g_parkedConfigured) {
    httpRespondJson(s, 503, "Service Unavailable",
                    "{\"ok\":false,\"reason\":\"parked_unconfigured\",\"retry\":false}");
    return;
  }

  // Claim + Retry/Backoff + Fehler-Mapping liegen als reine, host-testbare
  // Funktion in api_util.h (runSoloClaim). Der Callback ist der Outbound-POST
  // (WinSock, im Request-Thread — der GNS-Hauptloop bleibt unberuehrt).
  const rbapi::SoloOutcome outcome = rbapi::runSoloClaim(
      env,
      [](const std::string &path, const std::string &payload) -> rbapi::HttpResp {
        const OutboundResult r = outboundHttpPost(g_parkedHost, g_parkedPort,
                                                  path, payload, g_parkedToken,
                                                  2000, 2000);
        return rbapi::HttpResp{r.status, r.body};
      },
      4, [](int ms) { Sleep(static_cast<DWORD>(ms)); });

  if (outcome.pinIdentity) {
    {
      std::lock_guard<std::mutex> lock(g_apiMutex);
      ApiCommand cmd;
      cmd.kind = ApiCommand::kRouteByEndpoint;
      cmd.identity = identity;
      rbroute::parseEndpoint(outcome.endpoint, cmd.endpoint);
      cmd.hasEndpoint = true;
      g_apiCommands.push_back(cmd);
    }
    logLine("API: solo '%s' -> %s (instance=%s)", identity.c_str(),
            outcome.endpoint.c_str(), outcome.instance.c_str());
    std::string out = "{\"ok\":true,\"identitaet\":\"" +
                      rbapi::jsonEscape(identity) + "\",\"target\":\"" +
                      rbapi::jsonEscape(outcome.endpoint) +
                      "\",\"instance\":\"" +
                      rbapi::jsonEscape(outcome.instance) + "\"}";
    httpRespondJson(s, 200, "OK", out);
    return;
  }

  // Budget erschoepft und letzter Fehler war transient -> Backend startet noch.
  if (outcome.reason == "backend_starting") {
    logLine("solo: claim-Budget erschoepft — 503 backend_starting");
    httpRespondJson(s, 503, "Service Unavailable", outcome.body);
    return;
  }
  httpRespondJson(s, outcome.httpCode,
                  outcome.httpCode == 409 ? "Conflict" : "Error", outcome.body);
}

void httpHandle(SOCKET s) {
  DWORD timeout = 3000;
  setsockopt(s, SOL_SOCKET, SO_RCVTIMEO, reinterpret_cast<const char *>(&timeout),
             sizeof(timeout));

  std::string req;
  char buf[4096];
  for (;;) {
    const int n = recv(s, buf, sizeof(buf), 0);
    if (n <= 0) {
      break;
    }
    req.append(buf, static_cast<std::size_t>(n));
    if (req.find("\r\n\r\n") != std::string::npos) {
      break;
    }
    if (req.size() > (1u << 20)) {
      break;
    }
  }

  const std::size_t sp1 = req.find(' ');
  if (sp1 == std::string::npos) {
    httpRespond(s, 400, "Bad Request", "text/plain; charset=utf-8",
                "bad request\n");
    closesocket(s);
    return;
  }
  const std::size_t sp2 = req.find(' ', sp1 + 1);
  const std::string method = req.substr(0, sp1);
  std::string path = (sp2 == std::string::npos)
                         ? req.substr(sp1 + 1)
                         : req.substr(sp1 + 1, sp2 - sp1 - 1);
  std::string queryString;
  const std::size_t query = path.find('?');
  if (query != std::string::npos) {
    queryString = path.substr(query + 1);
    path = path.substr(0, query);
  }

  // Content-Length (case-insensitiv) bestimmen und den Body vollstaendig lesen.
  std::string lower = req;
  for (std::size_t i = 0; i < lower.size(); ++i) {
    if (lower[i] >= 'A' && lower[i] <= 'Z') {
      lower[i] = static_cast<char>(lower[i] - 'A' + 'a');
    }
  }
  std::size_t contentLength = 0;
  const std::size_t clPos = lower.find("content-length:");
  if (clPos != std::string::npos) {
    contentLength =
        static_cast<std::size_t>(strtoul(req.c_str() + clPos + 15, nullptr, 10));
  }
  const std::size_t hdrEnd = req.find("\r\n\r\n");
  std::string body = (hdrEnd == std::string::npos) ? std::string()
                                                   : req.substr(hdrEnd + 4);
  while (body.size() < contentLength) {
    const int n = recv(s, buf, sizeof(buf), 0);
    if (n <= 0) {
      break;
    }
    body.append(buf, static_cast<std::size_t>(n));
  }

  if (method == "GET" && path == "/") {
    httpRespond(s, 200, "OK", "text/html; charset=utf-8", kUiHtml);
  } else if (method == "GET" && path == "/sessions") {
    httpRespondJson(s, 200, "OK", buildSessionsJson());
  } else if (method == "GET" && path == "/targets") {
    httpRespondJson(s, 200, "OK", buildTargetsJson());
  } else if (method == "POST" && path == "/route") {
    std::string identity;
    std::string target;
    if (!rbapi::jsonStringField(body, "identitaet", identity) ||
        !rbapi::jsonStringField(body, "target", target)) {
      httpRespondJson(s, 400, "Bad Request",
                      "{\"ok\":false,\"error\":\"identitaet/target fehlt\"}");
    } else {
      {
        std::lock_guard<std::mutex> lock(g_apiMutex);
        ApiCommand cmd;
        cmd.kind = ApiCommand::kRouteByName;
        cmd.identity = identity;
        cmd.target = target;
        g_apiCommands.push_back(cmd);
      }
      logLine("API: route '%s' -> '%s' angefordert", identity.c_str(),
              target.c_str());
      httpRespondJson(s, 200, "OK", "{\"ok\":true}");
    }
  } else if (method == "POST" && path == "/backends") {
    // Dynamische Backend-Registry (Issue #929): {name, endpoint}.
    std::string name;
    std::string endpointStr;
    if (!rbapi::jsonStringField(body, "name", name) || name.empty()) {
      httpRespondJson(s, 400, "Bad Request",
                      "{\"ok\":false,\"reason\":\"bad_request\",\"detail\":\"name fehlt\"}");
    } else if (!rbapi::jsonStringField(body, "endpoint", endpointStr)) {
      httpRespondJson(s, 400, "Bad Request",
                      "{\"ok\":false,\"reason\":\"bad_request\",\"detail\":\"endpoint fehlt\"}");
    } else {
      rbroute::Endpoint endpoint;
      if (!rbroute::parseEndpoint(endpointStr, endpoint)) {
        httpRespondJson(s, 400, "Bad Request",
                        "{\"ok\":false,\"reason\":\"bad_request\",\"detail\":\"endpoint ist kein ip:port\"}");
      } else {
        {
          std::lock_guard<std::mutex> lock(g_apiMutex);
          ApiCommand cmd;
          cmd.kind = ApiCommand::kBackendAdd;
          cmd.target = name;
          cmd.endpoint = endpoint;
          cmd.hasEndpoint = true;
          g_apiCommands.push_back(cmd);
        }
        logLine("API: backend '%s' -> %s angefordert", name.c_str(),
                endpoint.str().c_str());
        httpRespondJson(s, 200, "OK", "{\"ok\":true}");
      }
    }
  } else if (method == "DELETE" && path == "/backends") {
    std::string name;
    if (!rbapi::parseQueryParam(queryString, "name", name) || name.empty()) {
      httpRespondJson(s, 400, "Bad Request",
                      "{\"ok\":false,\"reason\":\"bad_request\",\"detail\":\"name fehlt\"}");
    } else {
      bool known = false;
      {
        std::lock_guard<std::mutex> lock(g_apiMutex);
        for (std::size_t i = 0; i < g_targetsSnapshot.size(); ++i) {
          if (g_targetsSnapshot[i].first == name) {
            known = true;
            break;
          }
        }
      }
      if (!known) {
        httpRespondJson(s, 404, "Not Found",
                        "{\"ok\":false,\"reason\":\"not_found\"}");
      } else {
        {
          std::lock_guard<std::mutex> lock(g_apiMutex);
          ApiCommand cmd;
          cmd.kind = ApiCommand::kBackendDelete;
          cmd.target = name;
          g_apiCommands.push_back(cmd);
        }
        logLine("API: backend '%s' Abmeldung angefordert", name.c_str());
        httpRespondJson(s, 200, "OK", "{\"ok\":true}");
      }
    }
  } else if (method == "POST" && path == "/solo") {
    handleSolo(s, body);
  } else {
    httpRespond(s, 404, "Not Found", "text/plain; charset=utf-8",
                "not found\n");
  }
  closesocket(s);
}

void httpServerLoop() {
  WSADATA wsa;
  if (WSAStartup(MAKEWORD(2, 2), &wsa) != 0) {
    logLine("API: WSAStartup fehlgeschlagen — UI/API aus");
    return;
  }
  SOCKET listenSocket = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
  if (listenSocket == INVALID_SOCKET) {
    logLine("API: socket() fehlgeschlagen (err=%d)", WSAGetLastError());
    WSACleanup();
    return;
  }
  int yes = 1;
  setsockopt(listenSocket, SOL_SOCKET, SO_REUSEADDR,
             reinterpret_cast<const char *>(&yes), sizeof(yes));
  sockaddr_in addr;
  memset(&addr, 0, sizeof(addr));
  addr.sin_family = AF_INET;
  addr.sin_port = htons(static_cast<u_short>(g_apiPort));
  if (inet_pton(AF_INET, g_apiHost.c_str(), &addr.sin_addr) != 1) {
    logLine("API: ungueltiger --api-host '%s'", g_apiHost.c_str());
    closesocket(listenSocket);
    WSACleanup();
    return;
  }
  if (bind(listenSocket, reinterpret_cast<sockaddr *>(&addr), sizeof(addr)) ==
      SOCKET_ERROR) {
    logLine("API: bind %s:%d fehlgeschlagen (err=%d)", g_apiHost.c_str(),
            g_apiPort, WSAGetLastError());
    closesocket(listenSocket);
    WSACleanup();
    return;
  }
  if (listen(listenSocket, SOMAXCONN) == SOCKET_ERROR) {
    logLine("API: listen fehlgeschlagen (err=%d)", WSAGetLastError());
    closesocket(listenSocket);
    WSACleanup();
    return;
  }
  logLine("API/UI hoert auf http://%s:%d (kein Auth — nur lokal binden)",
          g_apiHost.c_str(), g_apiPort);
  for (;;) {
    SOCKET client = accept(listenSocket, nullptr, nullptr);
    if (client == INVALID_SOCKET) {
      Sleep(50);
      continue;
    }
    std::thread(httpHandle, client).detach();
  }
}

// --- DLL-Auflösung -----------------------------------------------------------

bool resolveDll(const char *explicitPath) {
  HMODULE lib = nullptr;
  if (explicitPath != nullptr) {
    lib = LoadLibraryA(explicitPath);
    logLine("LoadLibraryA('%s') -> %p", explicitPath, static_cast<void *>(lib));
  }
  if (lib == nullptr) {
    lib = LoadLibraryA("GameNetworkingSockets.dll");
    logLine("LoadLibraryA('GameNetworkingSockets.dll') -> %p",
            static_cast<void *>(lib));
  }
  if (lib == nullptr) {
    logLine("DLL nicht ladbar (GetLastError=%lu)", GetLastError());
    return false;
  }

  pAccessor = reinterpret_cast<fn_SteamNetworkingSockets_v009>(
      GetProcAddress(lib, "SteamAPI_SteamNetworkingSockets_v009"));
  pInit = reinterpret_cast<fn_Init>(
      GetProcAddress(lib, "GameNetworkingSockets_Init"));
  pIdentityClear = reinterpret_cast<fn_IdentityClear>(
      GetProcAddress(lib, "SteamAPI_SteamNetworkingIdentity_Clear"));
  pIdentitySetGenericString = reinterpret_cast<fn_IdentitySetGenericString>(
      GetProcAddress(lib, "SteamAPI_SteamNetworkingIdentity_SetGenericString"));
  pUtilsAccessor = reinterpret_cast<fn_UtilsAccessor>(
      GetProcAddress(lib, "SteamAPI_SteamNetworkingUtils_v003"));
  pSetDebugOutputFunction =
      reinterpret_cast<fn_SetDebugOutputFunction>(GetProcAddress(
          lib, "SteamAPI_ISteamNetworkingUtils_SetDebugOutputFunction"));
  pSetGlobalConfigValueInt32 =
      reinterpret_cast<fn_SetGlobalConfigValueInt32>(GetProcAddress(
          lib, "SteamAPI_ISteamNetworkingUtils_SetGlobalConfigValueInt32"));
  pSetGlobalCallbackStatusChanged =
      reinterpret_cast<fn_SetGlobalCallbackStatusChanged>(GetProcAddress(
          lib, "SteamAPI_ISteamNetworkingUtils_SetGlobalCallback_"
               "SteamNetConnectionStatusChanged"));
  pKill = reinterpret_cast<fn_Kill>(
      GetProcAddress(lib, "GameNetworkingSockets_Kill"));
  pCreateListenSocketIP =
      reinterpret_cast<fn_CreateListenSocketIP>(GetProcAddress(
          lib, "SteamAPI_ISteamNetworkingSockets_CreateListenSocketIP"));
  pCloseListenSocket = reinterpret_cast<fn_CloseListenSocket>(GetProcAddress(
      lib, "SteamAPI_ISteamNetworkingSockets_CloseListenSocket"));
  pCreatePollGroup = reinterpret_cast<fn_CreatePollGroup>(
      GetProcAddress(lib, "SteamAPI_ISteamNetworkingSockets_CreatePollGroup"));
  pDestroyPollGroup = reinterpret_cast<fn_DestroyPollGroup>(
      GetProcAddress(lib, "SteamAPI_ISteamNetworkingSockets_DestroyPollGroup"));
  pSetConnectionPollGroup =
      reinterpret_cast<fn_SetConnectionPollGroup>(GetProcAddress(
          lib, "SteamAPI_ISteamNetworkingSockets_SetConnectionPollGroup"));
  pAcceptConnection = reinterpret_cast<fn_AcceptConnection>(
      GetProcAddress(lib, "SteamAPI_ISteamNetworkingSockets_AcceptConnection"));
  pCloseConnection = reinterpret_cast<fn_CloseConnection>(
      GetProcAddress(lib, "SteamAPI_ISteamNetworkingSockets_CloseConnection"));
  pRunCallbacks = reinterpret_cast<fn_RunCallbacks>(
      GetProcAddress(lib, "SteamAPI_ISteamNetworkingSockets_RunCallbacks"));
  pReceiveMessagesOnPollGroup =
      reinterpret_cast<fn_ReceiveMessagesOnPollGroup>(GetProcAddress(
          lib, "SteamAPI_ISteamNetworkingSockets_ReceiveMessagesOnPollGroup"));
  pConnectByIPAddress = reinterpret_cast<fn_ConnectByIPAddress>(GetProcAddress(
      lib, "SteamAPI_ISteamNetworkingSockets_ConnectByIPAddress"));
  pSendMessageToConnection =
      reinterpret_cast<fn_SendMessageToConnection>(GetProcAddress(
          lib, "SteamAPI_ISteamNetworkingSockets_SendMessageToConnection"));
  pIPAddrSetIPv4 = reinterpret_cast<fn_IPAddrSetIPv4>(
      GetProcAddress(lib, "SteamAPI_SteamNetworkingIPAddr_SetIPv4"));
  pIdentityToString = reinterpret_cast<fn_IdentityToString>(GetProcAddress(
      lib, "SteamAPI_SteamNetworkingIdentity_ToString"));
  pSetConnConfigInt32 = reinterpret_cast<fn_SetConnConfigInt32>(GetProcAddress(
      lib, "SteamAPI_ISteamNetworkingUtils_SetConnectionConfigValueInt32"));
  pIPAddrToString = reinterpret_cast<fn_IPAddrToString>(
      GetProcAddress(lib, "SteamAPI_SteamNetworkingIPAddr_ToString"));

  const bool ok =
      pAccessor && pInit && pKill && pCreateListenSocketIP && pIdentityClear &&
      pIdentitySetGenericString && pUtilsAccessor && pSetDebugOutputFunction &&
      pSetGlobalConfigValueInt32 && pSetGlobalCallbackStatusChanged &&
      pCreatePollGroup && pSetConnectionPollGroup && pAcceptConnection &&
      pCloseConnection && pRunCallbacks && pReceiveMessagesOnPollGroup &&
      pConnectByIPAddress && pSendMessageToConnection && pIPAddrSetIPv4 &&
      pIdentityToString && pSetConnConfigInt32 && pIPAddrToString;
  if (!ok) {
    logLine("mindestens ein Export fehlt (Accessor=%p Init=%p Listen=%p)",
            reinterpret_cast<void *>(pAccessor),
            reinterpret_cast<void *>(pInit),
            reinterpret_cast<void *>(pCreateListenSocketIP));
    return false;
  }
  logLine("alle noetigen Exports aufgeloest");
  // WICHTIG: den Accessor NICHT hier aufrufen — im Standalone-Build liefert
  // SteamNetworkingSockets() erst NACH GameNetworkingSockets_Init ein
  // Interface.
  return true;
}

} // namespace

int main(int argc, char **argv) {
  uint16 nPort = 6321;
  const char *dllPath = nullptr;

  for (int i = 1; i < argc; ++i) {
    if (strcmp(argv[i], "--port") == 0 && i + 1 < argc) {
      nPort = static_cast<uint16>(atoi(argv[++i]));
    } else if (strcmp(argv[i], "--dll") == 0 && i + 1 < argc) {
      dllPath = argv[++i];
    } else if (strcmp(argv[i], "--identity") == 0) {
      g_useIdentity = true;
    } else if (strcmp(argv[i], "--unencrypted") == 0 && i + 1 < argc) {
      g_unencrypted = atoi(argv[++i]);
    } else if (strcmp(argv[i], "--allow-without-auth") == 0 && i + 1 < argc) {
      g_allow_without_auth = atoi(argv[++i]);
    } else if (strcmp(argv[i], "--appid") == 0 && i + 1 < argc) {
      g_appid = atoi(argv[++i]);
    } else if (strcmp(argv[i], "--forward") == 0 && i + 1 < argc) {
      const char *spec = argv[++i];
      if (!rbroute::parseEndpoint(spec, g_defaultTarget)) {
        fprintf(stderr, "--forward braucht IPv4:port, bekam '%s'\n", spec);
        return 2;
      }
    } else if (strcmp(argv[i], "--map-file") == 0 && i + 1 < argc) {
      g_mapFile = argv[++i];
    } else if (strcmp(argv[i], "--dial") == 0 && i + 1 < argc) {
      const char *spec = argv[++i];
      if (!rbroute::parseEndpoint(spec, g_dialTarget)) {
        fprintf(stderr, "--dial braucht IPv4:port, bekam '%s'\n", spec);
        return 2;
      }
    } else if (strcmp(argv[i], "--hold") == 0) {
      g_hold = true;
    } else if (strcmp(argv[i], "--api-port") == 0 && i + 1 < argc) {
      g_apiPort = atoi(argv[++i]);
    } else if (strcmp(argv[i], "--api-host") == 0 && i + 1 < argc) {
      g_apiHost = argv[++i];
    } else if (strcmp(argv[i], "--target") == 0 && i + 1 < argc) {
      const char *spec = argv[++i];
      std::string name;
      rbroute::Endpoint endpoint;
      if (!rbapi::parseTargetSpec(spec, name, endpoint)) {
        fprintf(stderr, "--target braucht NAME=IPv4:port, bekam '%s'\n", spec);
        return 2;
      }
      g_targets.emplace_back(name, endpoint);
    } else if (strcmp(argv[i], "--parked-url") == 0 && i + 1 < argc) {
      // Parked-Pool-Dienst fuer POST /solo (Issue #929). Token NUR per Env
      // (RBB_PARKED_TOKEN), damit er nicht in der Prozessliste steht.
      const char *spec = argv[++i];
      std::string host;
      int port = 0;
      if (!rbapi::parseUrlHostPort(spec, host, port)) {
        fprintf(stderr, "--parked-url braucht http://host:port, bekam '%s'\n",
                spec);
        return 2;
      }
      g_parkedHost = host;
      g_parkedPort = port;
      g_parkedConfigured = true;
    }
  }

  const char *parkedToken = getenv("RBB_PARKED_TOKEN");
  if (parkedToken != nullptr) {
    g_parkedToken = parkedToken;
  }
  logLine("parked fuer /solo: %s:%d (token=%s)", g_parkedHost.c_str(),
          g_parkedPort, g_parkedToken.empty() ? "kein" : "gesetzt");

  logLine("gns_probe (Spike #831, E1/E2) — port=%u", nPort);
  if (!resolveDll(dllPath)) {
    return 1;
  }

  SteamNetworkingErrMsg errMsg;
  memset(errMsg, 0, sizeof(errMsg));
  // Identity: Default KEINE (wie das Upstream-Beispiel example_chat.cpp, das in
  // genau diesem Open-Source-Build nachweislich funktioniert). Mit --identity
  // stattdessen einen generic-string setzen.
  if (g_useIdentity) {
    SteamNetworkingIdentity identity;
    pIdentityClear(&identity);
    const bool identityOk = pIdentitySetGenericString(&identity, "riftbreaker-probe");
    logLine("identity gesetzt (generic-string) ok=%d", identityOk ? 1 : 0);
    if (!pInit(&identity, &errMsg)) {
      logLine("GameNetworkingSockets_Init FEHLGESCHLAGEN: %s", errMsg);
      return 1;
    }
  } else {
    logLine("identity: KEINE (Upstream-Default)");
    if (!pInit(nullptr, &errMsg)) {
      logLine("GameNetworkingSockets_Init FEHLGESCHLAGEN: %s", errMsg);
      return 1;
    }
  }
  logLine("GNS initialisiert");

  // IP_AllowWithoutAuth ist DREISTUFIG (0=nur mit Cert, 1=?, 2=ohne Auth erlaubt) und im
  // Open-Source-Build per Default 2. Wir setzen explizit 2 — mit 1 triggert GNS
  // AuthenticationNeeded() und lehnt das appid-gebundene Client-Zertifikat ab
  // ("Cert is not authorized for appid 0, only 780310").
  // Unencrypted=1: das Spiel faehrt GNS unverschluesselt (Klartext-Mitschnitte).
  void *pUtils = pUtilsAccessor();
  logLine("utils-interface = %p", pUtils);
  if (pUtils != nullptr) {
    // Patche m_nAppID (AppId_t @ +0x8) — sonst scheitert CheckCertAppID mit
    // "Cert is not authorized for appid 0, only 780310".
    auto *pAppID = reinterpret_cast<uint32 *>(static_cast<char *>(pUtils) + 8);
    logLine("m_nAppID vorher = %u -> setze %d", *pAppID, g_appid);
    *pAppID = static_cast<uint32>(g_appid);
    pSetDebugOutputFunction(pUtils, 5 /* Msg */,
                            reinterpret_cast<void *>(onGnsDebug));
    const bool gUnenc = pSetGlobalConfigValueInt32(
        pUtils, k_ESteamNetworkingConfig_Unencrypted, g_unencrypted);
    const bool gNoAuth = pSetGlobalConfigValueInt32(
        pUtils, k_ESteamNetworkingConfig_IP_AllowWithoutAuth, g_allow_without_auth);
    logLine("global config gesetzt: Unencrypted=%d(ok=%d) IP_AllowWithoutAuth=%d(ok=%d)",
            g_unencrypted, gUnenc ? 1 : 0, g_allow_without_auth, gNoAuth ? 1 : 0);
    pSetGlobalCallbackStatusChanged(
        pUtils, reinterpret_cast<void *>(onConnectionStatusChanged));
    logLine("globaler Status-Callback registriert");
  }

  g_pInterface = pAccessor();
  logLine("Interface-Pointer = %p", reinterpret_cast<void *>(g_pInterface));
  if (g_pInterface == nullptr) {
    logLine("Accessor lieferte nullptr — auch nach Init (unerwartet)");
    return 1;
  }

  if (g_dialTarget.port != 0) {
    // Nur Verbindungstest (kein Listen-Socket) — sonst kollidiert der Test mit
    // dem laufenden Relay auf 6321.
    logLine("DIAL-TEST -> %s", g_dialTarget.str().c_str());
    // Eigene Wegwerf-Session ohne Client — nur Verbindungstest (Issue #831).
    Session dialSess;
    dialSess.backendTarget = g_dialTarget;
    startBackendConnect(dialSess);
    for (int i = 0; i < 200 && !dialSess.backendConnected; ++i) {
      pRunCallbacks(g_pInterface);
      std::this_thread::sleep_for(std::chrono::milliseconds(50));
    }
    logLine("DIAL %s",
            dialSess.backendConnected ? "OK (Connected)"
                                      : "FEHLGESCHLAGEN (Timeout)");
    pKill();
    return dialSess.backendConnected ? 0 : 1;
  }

  SteamNetworkingIPAddr serverLocalAddr;
  serverLocalAddr.Clear();
  serverLocalAddr.m_port = nPort;

  // Unencrypted (34) + IP_AllowWithoutAuth (23) MUESSEN auf unserer Seite
  // gesetzt sein: ohne sie versucht GNS den authentifizierten Pfad und weist
  // ab, weil wir kein Server-Zertifikat haben (Live-Befund E1: Client sendet
  // 512 -> wir 26 -> Client wiederholt 512, kein Status-Callback). Das Spiel
  // selbst faehrt GNS laut Mitschnitten unverschluesselt, d.h. der Client setzt
  // Unencrypted ebenfalls.
  SteamNetworkingConfigValue_t opt[3];
  opt[0].SetPtr(k_ESteamNetworkingConfig_Callback_ConnectionStatusChanged,
                (void *)onConnectionStatusChanged);
  opt[1].SetInt32(k_ESteamNetworkingConfig_IP_AllowWithoutAuth, g_allow_without_auth);
  opt[2].SetInt32(k_ESteamNetworkingConfig_Unencrypted, g_unencrypted);
  logLine("listen-optionen: Callback + IP_AllowWithoutAuth=%d + Unencrypted=%d",
          g_allow_without_auth, g_unencrypted);

  HSteamListenSocket hListen =
      pCreateListenSocketIP(g_pInterface, &serverLocalAddr, 3, opt);
  if (hListen == k_HSteamListenSocket_Invalid) {
    logLine("CreateListenSocketIP auf Port %u FEHLGESCHLAGEN", nPort);
    return 1;
  }
  // Die Poll-Gruppen entstehen pro Session (Issue #877) — je Richtung eine,
  // sonst koppelt globales Lesen die Backpressure aller Sessions. Der Probe-
  // Aufruf haelt das alte Fail-Fast: kann gar keine Poll-Gruppe erzeugt werden,
  // wuerde sonst jede Session still nichts relayen.
  const HSteamNetPollGroup probePoll = pCreatePollGroup(g_pInterface);
  if (probePoll == k_HSteamNetPollGroup_Invalid) {
    logLine("CreatePollGroup FEHLGESCHLAGEN (Export/Interface pruefen)");
    return 1;
  }
  if (pDestroyPollGroup != nullptr) {
    pDestroyPollGroup(g_pInterface, probePoll);
  }
  logLine("lauscht als GNS-Server auf 0.0.0.0:%u — warte auf Handshake", nPort);
  loadRoutes();
  const bool wantRelay =
      g_mapFile != nullptr || g_defaultTarget.port != 0 || g_hold || g_apiPort != 0;
  if (wantRelay) {
    g_relayEnabled = true;
    if (g_defaultTarget.port == 0 && !g_hold) {
      // Kein --forward: Default-Regel (`*`) aus der Routen-Datei nehmen, damit
      // der Client ueberhaupt eine Handshake-Antwort bekommt und den Namen
      // nachliefern kann. Im HOLD-Modus gibt es bewusst keinen Default.
      const rbroute::Rule *fallback = g_routes.defaultRule();
      if (fallback != nullptr) {
        g_defaultTarget = fallback->target;
        logLine("kein --forward gesetzt — Default = Regel '*' -> %s",
                g_defaultTarget.str().c_str());
      } else {
        logLine("WARNUNG: kein --forward und keine Default-Regel '*' — Joins "
                "ohne passende Route koennen nicht bedient werden");
      }
    }
    logLine("RELAY-MODUS: %s | default %s | routen=%zu | identitaet(exakt)+name",
            g_hold ? "HOLD" : "auto",
            g_defaultTarget.port != 0 ? g_defaultTarget.str().c_str()
                                      : "(keiner)",
            g_routes.size());
    if (g_hold && g_targets.empty()) {
      logLine("WARNUNG: --hold ohne --target — niemand kann einen wartenden "
              "Spieler routen");
    }
  }
  // Steuer-API + Web-UI (Issue #857): eigener HTTP-Thread, Standardbind nur
  // 127.0.0.1 (kein Auth). Faellt der Bind aus, laeuft der Relay trotzdem.
  if (g_apiPort > 0) {
    // Registry-Snapshot initial fuellen, BEVOR der HTTP-Thread /targets liest.
    refreshTargetsSnapshot();
    std::thread(httpServerLoop).detach();
  }
  logLine("(E1: Status Connected erwarten | E2: Klartext-Nachrichten mit "
          "Spielernamen)");

  std::chrono::steady_clock::time_point lastSnapshot{};
  while (true) {
    pRunCallbacks(g_pInterface);
    if (!g_relayEnabled) {
      for (std::map<HSteamNetConnection, std::unique_ptr<Session>>::iterator it =
               g_clientSessions.begin();
           it != g_clientSessions.end(); ++it) {
        if (it->second->clientPoll == k_HSteamNetPollGroup_Invalid) {
          continue;
        }
        drainFrom(*it->second, it->second->clientPoll, true);
      }
    } else {
      // Jede Session unabhaengig bedienen: Backpressure bleibt pro Session
      // (nur lesen, solange die Queue der Gegenseite Luft hat), damit eine
      // langsame Leitung die andere Session nicht ausbremst.
      for (std::map<HSteamNetConnection, std::unique_ptr<Session>>::iterator it =
               g_clientSessions.begin();
           it != g_clientSessions.end(); ++it) {
        Session &s = *it->second;
        if (s.toClientBytes < kQueueSoftLimit &&
            s.backendPoll != k_HSteamNetPollGroup_Invalid) {
          drainFrom(s, s.backendPoll, false);
        }
        if (s.toBackendBytes < kQueueSoftLimit &&
            s.clientPoll != k_HSteamNetPollGroup_Invalid) {
          drainFrom(s, s.clientPoll, true);
        }
        flushQueue(s.toClientQ, s.toClientBytes, s.clientConn);
        flushQueue(s.toBackendQ, s.toBackendBytes, s.backendConn);
      }
    }
    if (g_apiPort > 0) {
      // Befehle des HTTP-Threads abarbeiten und den Anzeige-Snapshot, den
      // /sessions liest, gedrosselt aktualisieren.
      processApiCommands();
      const std::chrono::steady_clock::time_point now =
          std::chrono::steady_clock::now();
      if (now - lastSnapshot >= std::chrono::milliseconds(250)) {
        lastSnapshot = now;
        refreshSnapshot();
      }
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(2));
  }

  pCloseListenSocket(g_pInterface, hListen);
  pKill();
  return 0;
}
