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

#include <windows.h>

#include <chrono>
#include <cstdarg>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <deque>
#include <map>
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

ISteamNetworkingSockets *g_pInterface = nullptr;
HSteamNetPollGroup g_hPollGroup = k_HSteamNetPollGroup_Invalid;
int g_msgCount = 0;

// --- Relay (E3/E4) -----------------------------------------------------------
// Terminierendes Relay: der Client haengt an g_clientConn, ein neuer GNS-Client
// am Backend an g_backendConn.  Nachrichten werden 1:1 weitergereicht.
//
// Routing (E4): der GNS-*Identitaetsstring* des Clients kommt mit dem Connect
// (`str:<hex>`, stabil pro Installation) und ist damit sofort verfuegbar — damit
// laesst sich direkt beim Connect auf ein beliebiges Backend routen.  Der
// Spielname kommt erst nach der Handshake-Antwort; er wird daher aus dem
// durchgereichten Strom gelernt und loest bei Abweichung ein **Re-Route**
// (Replay des bis dahin Gesehenen auf das richtige Backend) aus.
HSteamNetConnection g_clientConn = k_HSteamNetConnection_Invalid;
HSteamNetConnection g_backendConn = k_HSteamNetConnection_Invalid;
bool g_backendConnected = false;
bool g_relayEnabled = false;
uint32 g_backendIP = 0;
uint16 g_backendPort = 0;
// Konfigurierter Default (--forward bzw. erste Route).  Wird NIE durch ein
// Re-Route ueberschrieben — sonst wandert der Default zu einem anderen Backend.
uint32 g_defaultIP = 0;
uint16 g_defaultPort = 0;
// --dial: nur Verbindungstest zu einem Backend (ohne Client), fuer Diagnose.
uint32 g_dialIP = 0;
uint16 g_dialPort = 0;

// Routen: Key = Identitaetsstring (`str:…`) ODER Spielname -> Backend.
std::map<std::string, std::pair<uint32, uint16>> g_routes;
// Gelernt zur Laufzeit: Identitaet -> Backend (aus dem Spielnamen).  Damit ist
// der zweite Join clean und ohne Replay.
std::map<std::string, std::pair<uint32, uint16>> g_learnedIdentity;
const char *g_mapFile = nullptr;
std::string g_clientIdentity;
std::string g_lastName;

// Historie aller Client->Server-Nachrichten (fuer Replay beim Re-Route) plus
// Merker, wie viele davon schon an das *aktuelle* Backend gingen.
std::vector<std::pair<std::string, int>> g_history;
size_t g_historyBytes = 0;
size_t g_historyForwarded = 0;
const size_t kMaxHistoryBytes = 8u * 1024 * 1024;

// Sende-Queues + Backpressure.  GNS liefert `k_EResultLimitExceeded`, wenn der
// Sende-Puffer des Ziels voll ist (typisch: 500-KB-Weltzustand vom lokalen
// Backend ueber eine langsame Client-Leitung).  Dann wird gepuffert und von der
// Quelle nur so lange gelesen, wie die Queue der Gegenseite unter dem Limit ist.
std::deque<std::pair<std::string, int>> g_toClientQ;
std::deque<std::pair<std::string, int>> g_toBackendQ;
size_t g_toClientBytes = 0;
size_t g_toBackendBytes = 0;
const size_t kQueueSoftLimit = 2u * 1024 * 1024;
HSteamNetPollGroup g_backendPoll = k_HSteamNetPollGroup_Invalid;

// --- Logging -----------------------------------------------------------------

int g_unencrypted = 0;
int g_allow_without_auth = 2;
bool g_useIdentity = false;
// GNS bindet jedes (selbst-signierte) Zertifikat an GetAppID(); der Open-Source-
// Default ist 0, waehrend der Riftbreaker-Client 780310 sendet.  Wir patchen
// m_nAppID direkt im statischen Utils-Objekt (Offset 0x8, per Disasm verifiziert:
// vtable-Slot 26 = `mov eax,[rcx+8]; ret`).
int g_appid = 780310;

void logLine(const char *fmt, ...) {
  char stamp[32];
  time_t now = time(nullptr);
  struct tm *lt = localtime(&now);
  strftime(stamp, sizeof(stamp), "%H:%M:%S", lt);
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

// --- GNS-Callbacks -----------------------------------------------------------

std::string ipStr(uint32 ip) {
  char buf[32];
  snprintf(buf, sizeof(buf), "%u.%u.%u.%u", (ip >> 24) & 255, (ip >> 16) & 255,
           (ip >> 8) & 255, ip & 255);
  return buf;
}

bool parseEndpoint(const char *spec, uint32 &ipOut, uint16 &portOut) {
  unsigned a = 0, b = 0, c = 0, d = 0, port = 0;
  if (sscanf(spec, "%u.%u.%u.%u:%u", &a, &b, &c, &d, &port) != 5 ||
      a > 255 || b > 255 || c > 255 || d > 255 || port == 0 || port > 65535) {
    return false;
  }
  ipOut = (a << 24) | (b << 16) | (c << 8) | d;
  portOut = static_cast<uint16>(port);
  return true;
}

std::string trim(const std::string &s) {
  size_t b = s.find_first_not_of(" \t\r\n");
  if (b == std::string::npos) {
    return "";
  }
  size_t e = s.find_last_not_of(" \t\r\n");
  return s.substr(b, e - b + 1);
}

// Routen-Datei: `<key>=<ip:port>` je Zeile; `key` ist ein Identitaetsstring
// (`str:…`) oder ein Spielername; `#` startet einen Kommentar.
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
    uint32 ip = 0;
    uint16 port = 0;
    if (key.empty() || !parseEndpoint(trim(eq + 1).c_str(), ip, port)) {
      continue;
    }
    g_routes[key] = std::make_pair(ip, port);
    ++n;
  }
  fclose(f);
  logLine("routen geladen: %d aus '%s'", n, g_mapFile);
}

// Laengsten registrierten *Spielnamen* finden, der als ASCII im Payload steht
// (Identitaets-Keys `str:…` sind keine Namen und werden ausgelassen).
std::string pickName(const std::string &payload) {
  std::string best;
  for (const auto &kv : g_routes) {
    if (kv.first.rfind("str:", 0) == 0 || kv.first.size() < 3) {
      continue;
    }
    if (kv.first.size() > best.size() &&
        payload.find(kv.first) != std::string::npos) {
      best = kv.first;
    }
  }
  return best;
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
void queueHistoryDelta() {
  size_t n = 0;
  while (g_historyForwarded < g_history.size()) {
    const std::pair<std::string, int> &m = g_history[g_historyForwarded];
    g_toBackendQ.push_back(m);
    g_toBackendBytes += m.first.size();
    ++g_historyForwarded;
    ++n;
  }
  if (n > 0) {
    logLine("replay/backlog: %zu Nachricht(en) -> backend-queue (%zu offen)",
            n, g_toBackendQ.size());
  }
}

void startBackendConnect() {
  if (pConnectByIPAddress == nullptr || pIPAddrSetIPv4 == nullptr) {
    logLine("relay: ConnectByIPAddress/SetIPv4-Export fehlt");
    return;
  }
  SteamNetworkingIPAddr addr;
  memset(&addr, 0, sizeof(addr));
  pIPAddrSetIPv4(&addr, g_backendIP, g_backendPort);
  g_backendConn = pConnectByIPAddress(g_pInterface, &addr, 0, nullptr);
  if (g_backendConn == k_HSteamNetConnection_Invalid) {
    logLine("backend-connect auf %s:%u FEHLGESCHLAGEN",
            ipStr(g_backendIP).c_str(), g_backendPort);
    return;
  }
  pSetConnectionPollGroup(g_pInterface, g_backendConn, g_backendPoll);
  if (pSetConnConfigInt32 != nullptr) {
    // Default ist 512 KiB — ein einzelner Weltzustand ist ~500 KiB, mit
    // vorangehenden Nachrichten laeuft der Puffer sonst sofort voll.
    pSetConnConfigInt32(pUtilsAccessor(), g_backendConn,
                        k_ESteamNetworkingConfig_SendBufferSize, 8 * 1024 * 1024);
  }
  logLine("backend-connect gestartet -> conn=%u (%s:%u)", g_backendConn,
          ipStr(g_backendIP).c_str(), g_backendPort);
}

void routeTo(uint32 ip, uint16 port, const char *why) {
  logLine("ROUTE (%s) -> %s:%u", why, ipStr(ip).c_str(), port);
  if (g_backendConn != k_HSteamNetConnection_Invalid) {
    pCloseConnection(g_pInterface, g_backendConn, 0, nullptr, false);
    g_backendConn = k_HSteamNetConnection_Invalid;
    g_backendConnected = false;
  }
  g_backendIP = ip;
  g_backendPort = port;
  // Alles Gesehene erneut an das (neue) Backend schicken — aber erst, wenn es
  // verbunden ist (siehe Callback), sonst flutet ein Replay den Handshake.
  g_toBackendQ.clear();
  g_toBackendBytes = 0;
  g_historyForwarded = 0;
  startBackendConnect();
}

// Pro Client aufraeumen — sonst wird die Historie des vorigen Matches beim
// naechsten Join ans neue Backend geflutet.
void resetClientState() {
  g_history.clear();
  g_historyBytes = 0;
  g_historyForwarded = 0;
  g_toClientQ.clear();
  g_toClientBytes = 0;
  g_toBackendQ.clear();
  g_toBackendBytes = 0;
  g_lastName.clear();
  g_clientIdentity.clear();
  g_backendConnected = false;
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
  case k_ESteamNetworkingConnectionState_Connecting:
    if (pInfo->m_hConn == g_backendConn) {
      logLine("backend: Connecting (ausgehend)");
      break;
    }
    if (pAcceptConnection(g_pInterface, pInfo->m_hConn) != k_EResultOK) {
      logLine("AcceptConnection FEHLGESCHLAGEN -> CloseConnection");
      pCloseConnection(g_pInterface, pInfo->m_hConn, 0, nullptr, false);
      break;
    }
    g_clientConn = pInfo->m_hConn;
    resetClientState();
    if (!pSetConnectionPollGroup(g_pInterface, pInfo->m_hConn, g_hPollGroup)) {
      logLine("SetConnectionPollGroup FEHLGESCHLAGEN (weiter trotzdem)");
    }
    if (pSetConnConfigInt32 != nullptr) {
      pSetConnConfigInt32(pUtilsAccessor(), g_clientConn,
                          k_ESteamNetworkingConfig_SendBufferSize,
                          8 * 1024 * 1024);
    }
    logLine("E1: Verbindung AKZEPTIERT (Handshake laeuft)");
    break;
  case k_ESteamNetworkingConnectionState_Connected:
    if (pInfo->m_hConn == g_backendConn) {
      logLine("backend: Connected");
      g_backendConnected = true;
      queueHistoryDelta();
      break;
    }
    logLine("E1 GRUEN: state=Connected — der Client akzeptiert einen fremden "
            "GNS-Server");
    if (g_relayEnabled) {
      // Identitaet kommt sofort mit dem Connect und ist stabil pro Installation
      // -> damit koennen wir direkt routen, ohne auf den Spielnamen zu warten.
      char ident[256] = {0};
      if (pIdentityToString != nullptr) {
        pIdentityToString(&info.m_identityRemote, ident, sizeof(ident));
      }
      g_clientIdentity = ident;
      logLine("client-identitaet: '%s'", g_clientIdentity.c_str());
      const auto fixed = g_routes.find(g_clientIdentity);
      const auto learned = g_learnedIdentity.find(g_clientIdentity);
      if (fixed != g_routes.end()) {
        routeTo(fixed->second.first, fixed->second.second, "identitaet (fix)");
      } else if (learned != g_learnedIdentity.end()) {
        routeTo(learned->second.first, learned->second.second,
                "identitaet (gelernt)");
      } else {
        routeTo(g_defaultIP, g_defaultPort, "default (identitaet unbekannt)");
      }
    }
    break;
  case k_ESteamNetworkingConnectionState_ClosedByPeer:
  case k_ESteamNetworkingConnectionState_ProblemDetectedLocally:
    if (pInfo->m_hConn == g_backendConn) {
      logLine("backend beendet (state=%d) — client schliessen",
              static_cast<int>(info.m_eState));
      g_backendConn = k_HSteamNetConnection_Invalid;
      g_backendConnected = false;
    } else {
      logLine("client beendet (state=%d) — aufraeumen",
              static_cast<int>(info.m_eState));
      g_clientConn = k_HSteamNetConnection_Invalid;
      if (g_backendConn != k_HSteamNetConnection_Invalid) {
        pCloseConnection(g_pInterface, g_backendConn, 0, nullptr, false);
        g_backendConn = k_HSteamNetConnection_Invalid;
        g_backendConnected = false;
      }
    }
    pCloseConnection(g_pInterface, pInfo->m_hConn, 0, nullptr, false);
    break;
  default:
    break;
  }
}

// Nachrichten einer Richtung einsammeln und in die Queue der Gegenseite legen.
// `fromClient` = true: Client -> Backend, false: Backend -> Client.
void drainFrom(HSteamNetPollGroup group, bool fromClient) {
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
      // Historie fuer Replay (Backlog UND spaeteres Re-Route) fuehren.
      if (g_historyBytes + size <= kMaxHistoryBytes) {
        g_history.emplace_back(payload, flags);
        g_historyBytes += size;
      } else {
        logLine("  -> HISTORIE VOLL — nicht replay-faehig (%zu B)", size);
      }
      // Spielname lernen und bei Bedarf auf das richtige Backend umziehen.
      const std::string name = pickName(payload);
      if (!name.empty() && name != g_lastName) {
        g_lastName = name;
        const auto it = g_routes.find(name);
        if (it != g_routes.end()) {
          if (it->second.first != g_backendIP ||
              it->second.second != g_backendPort) {
            logLine("NAME GELERNT: '%s' -> %s:%u (re-route)", name.c_str(),
                    ipStr(it->second.first).c_str(), it->second.second);
            routeTo(it->second.first, it->second.second, "name");
          } else {
            logLine("NAME GELERNT: '%s' -> schon richtiges backend",
                    name.c_str());
          }
          // Identitaet -> Backend merken: der zweite Join ist damit instant und
          // braucht kein Replay mehr.
          if (!g_clientIdentity.empty()) {
            g_learnedIdentity[g_clientIdentity] = it->second;
            logLine("  identity-cache: %s -> %s:%u", g_clientIdentity.c_str(),
                    ipStr(it->second.first).c_str(), it->second.second);
          }
        }
      }
      queueHistoryDelta();
    } else {
      g_toClientQ.emplace_back(payload, flags);
      g_toClientBytes += size;
    }
    // Quelle anhalten, wenn die Gegenseite schon genug Daten hat.
    if (g_toClientBytes >= kQueueSoftLimit ||
        g_toBackendBytes >= kQueueSoftLimit) {
      break;
    }
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

  const bool ok =
      pAccessor && pInit && pKill && pCreateListenSocketIP && pIdentityClear &&
      pIdentitySetGenericString && pUtilsAccessor && pSetDebugOutputFunction &&
      pSetGlobalConfigValueInt32 && pSetGlobalCallbackStatusChanged &&
      pCreatePollGroup && pSetConnectionPollGroup && pAcceptConnection &&
      pCloseConnection && pRunCallbacks && pReceiveMessagesOnPollGroup &&
      pConnectByIPAddress && pSendMessageToConnection && pIPAddrSetIPv4 &&
      pIdentityToString && pSetConnConfigInt32;
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
      if (!parseEndpoint(spec, g_backendIP, g_backendPort)) {
        fprintf(stderr, "--forward braucht IPv4:port, bekam '%s'\n", spec);
        return 2;
      }
    } else if (strcmp(argv[i], "--map-file") == 0 && i + 1 < argc) {
      g_mapFile = argv[++i];
    } else if (strcmp(argv[i], "--dial") == 0 && i + 1 < argc) {
      const char *spec = argv[++i];
      if (!parseEndpoint(spec, g_dialIP, g_dialPort)) {
        fprintf(stderr, "--dial braucht IPv4:port, bekam '%s'\n", spec);
        return 2;
      }
    }
  }

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

  if (g_dialIP != 0) {
    // Nur Verbindungstest (kein Listen-Socket) — sonst kollidiert der Test mit
    // dem laufenden Relay auf 6321.
    logLine("DIAL-TEST -> %s:%u", ipStr(g_dialIP).c_str(), g_dialPort);
    g_backendPoll = pCreatePollGroup(g_pInterface);
    g_backendIP = g_dialIP;
    g_backendPort = g_dialPort;
    startBackendConnect();
    for (int i = 0; i < 200 && !g_backendConnected; ++i) {
      pRunCallbacks(g_pInterface);
      std::this_thread::sleep_for(std::chrono::milliseconds(50));
    }
    logLine("DIAL %s",
            g_backendConnected ? "OK (Connected)" : "FEHLGESCHLAGEN (Timeout)");
    pKill();
    return g_backendConnected ? 0 : 1;
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
  g_hPollGroup = pCreatePollGroup(g_pInterface);
  if (g_hPollGroup == k_HSteamNetPollGroup_Invalid) {
    logLine("CreatePollGroup FEHLGESCHLAGEN");
    return 1;
  }
  g_backendPoll = pCreatePollGroup(g_pInterface);
  if (g_backendPoll == k_HSteamNetPollGroup_Invalid) {
    logLine("CreatePollGroup (backend) FEHLGESCHLAGEN");
    return 1;
  }
  logLine("lauscht als GNS-Server auf 0.0.0.0:%u — warte auf Handshake", nPort);
  loadRoutes();
  if (g_mapFile != nullptr || g_backendIP != 0) {
    g_relayEnabled = true;
    if (g_backendIP == 0 && !g_routes.empty()) {
      // Kein explizites Default angegeben: erste Route als Default nehmen, damit
      // der Client ueberhaupt eine Handshake-Antwort bekommt und den Namen
      // nachliefern kann.
      g_backendIP = g_routes.begin()->second.first;
      g_backendPort = g_routes.begin()->second.second;
      logLine("kein --forward gesetzt — Default = erste Route '%s' -> %s:%u",
              g_routes.begin()->first.c_str(), ipStr(g_backendIP).c_str(),
              g_backendPort);
    }
    g_defaultIP = g_backendIP;
    g_defaultPort = g_backendPort;
    logLine("RELAY-MODUS: default-backend %s:%u | routen=%zu | identitaet(+cache)+name",
            ipStr(g_defaultIP).c_str(), g_defaultPort, g_routes.size());
  }
  logLine("(E1: Status Connected erwarten | E2: Klartext-Nachrichten mit "
          "Spielernamen)");

  while (true) {
    pRunCallbacks(g_pInterface);
    if (!g_relayEnabled) {
      drainFrom(g_hPollGroup, true);
    } else {
      // Backpressure: nur lesen, solange die Queue der Gegenseite noch Luft hat.
      if (g_toClientBytes < kQueueSoftLimit) {
        drainFrom(g_backendPoll, false);
      }
      if (g_toBackendBytes < kQueueSoftLimit) {
        drainFrom(g_hPollGroup, true);
      }
      flushQueue(g_toClientQ, g_toClientBytes, g_clientConn);
      flushQueue(g_toBackendQ, g_toBackendBytes, g_backendConn);
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(2));
  }

  pCloseListenSocket(g_pInterface, hListen);
  pKill();
  return 0;
}
