#!/usr/bin/env python3
"""RIFT BATTLE — Telegram-Feed-Bridge (SP-/Duell-Events → Telegram-Topic, Issue #44).

Pollt den Tournament-Server (`GET /events?since=<cursor>`) und postet neue
Referee-Events als Nachrichten in ein Telegram-Topic (Bot-API `sendMessage`,
`message_thread_id`). Reine Standardbibliothek (urllib, HTTPS via System-TLS) —
keine Dependencies. Konsistent mit dem Poll-Modell der Bridge-Seite
(`poller_example.py`): kein Echtzeit-Zwang, der Cursor `seq` verhindert
Event-Verluste.

Env:
  TOURNAMENT_EVENTS_URL  GET /events Endpoint (Default http://127.0.0.1:8080/events)
  TELEGRAM_BOT_TOKEN     Bot-Token (ohne Präfix "bot")
  TELEGRAM_CHAT_ID       Ziel-Chat (z. B. "@channel" oder numerische ID)
  TELEGRAM_TOPIC_ID      Optional: Message-Thread/Topic-ID (Supergroup mit Topics)
  POLL_INTERVAL          Sekunden zwischen Polls (Default 2.0)

Selbsttest (ohne Netz, prüft Formatierung + Payload-Aufbau):
  python3 telegram_feed.py --self-test
"""

import json
import os
import sys
import time
import urllib.request

EVENTS_URL = os.environ.get("TOURNAMENT_EVENTS_URL", "http://127.0.0.1:8080/events")
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
TOPIC_ID = os.environ.get("TELEGRAM_TOPIC_ID", "") or None
INTERVAL = float(os.environ.get("POLL_INTERVAL", "2.0"))

# Lesbare Labels je Feed-`kind` (Runde, Send/Boost, HQ-HP, Match-Ende, …).
KIND_LABEL = {
    "go": "GO",
    "sp": "SP-MODE",
    "register": "REGISTER",
    "ready": "READY",
    "send": "SEND",
    "wave": "WELLE",
    "reveal": "REVEAL",
    "hq": "HQ",
    "finish": "FINISH",
    "match_end": "MATCH-ENDE",
    "rematch": "REMATCH",
}


def format_event(event: dict) -> str:
    """Feed-Eintrag → Telegram-Einzeiler. `msg` ist bereits menschlich lesbar
    (Deutsch) — wir stellen nur das Event-Label voran."""
    kind = str(event.get("kind", "event"))
    msg = str(event.get("msg", ""))
    label = KIND_LABEL.get(kind, kind.upper())
    return f"[{label}] {msg}"


def build_send_message_url(token: str) -> str:
    return f"https://api.telegram.org/bot{token}/sendMessage"


def build_payload(text: str, chat_id: str, topic_id: str | None = None) -> dict:
    """sendMessage-Body; `message_thread_id` nur, wenn ein Topic gesetzt ist."""
    body = {"chat_id": chat_id, "text": text}
    if topic_id:
        body["message_thread_id"] = topic_id
    return body


def send_telegram(text: str) -> bool:
    if not BOT_TOKEN or not CHAT_ID:
        print(f"[telegram] übersprungen (kein TELEGRAM_BOT_TOKEN/CHAT_ID): {text}", flush=True)
        return False
    url = build_send_message_url(BOT_TOKEN)
    data = json.dumps(build_payload(text, CHAT_ID, TOPIC_ID)).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"content-type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8", "replace")
        ok = json.loads(body).get("ok", False) if body else False
        if not ok:
            print(f"[telegram] sendMessage nicht ok: {body[:200]}", flush=True)
        return bool(ok)
    except Exception as exc:  # noqa: BLE001 — Poller läuft weiter
        print(f"[telegram] send-Fehler: {exc}", flush=True)
        return False


def get_events(since: int) -> tuple[list[dict], int]:
    url = f"{EVENTS_URL}?since={since}" if since else EVENTS_URL
    with urllib.request.urlopen(url, timeout=5) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data.get("events", []), int(data.get("last_seq", since))


def main() -> None:
    if not BOT_TOKEN or not CHAT_ID:
        print(
            "[telegram] WARNUNG: TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID fehlen — "
            "Events werden nur geloggt, nicht gesendet.",
            flush=True,
        )
    print(f"[telegram] Feed-Bridge startet gegen {EVENTS_URL} (Intervall {INTERVAL}s)", flush=True)

    cursor = 0
    while True:
        try:
            events, last_seq = get_events(cursor)
        except Exception as exc:  # noqa: BLE001 — Poller läuft weiter
            print(f"[telegram] poll-Fehler: {exc}", flush=True)
            time.sleep(INTERVAL)
            continue

        for event in events:
            if not send_telegram(format_event(event)):
                # Fehlgeschlagene/übersprungene Sendung nicht erneut versuchen.
                pass
            cursor = max(cursor, int(event.get("seq", cursor)))

        # Cursor auch bei leerem Feed nachziehen (kein Nachholen alter Events).
        cursor = max(cursor, last_seq)
        time.sleep(INTERVAL)


def self_test() -> int:
    """Ohne Netz: Formatierung + Payload-Aufbau prüfen (Syntax-/Unit-Check)."""
    assert format_event({"kind": "send", "msg": "Send A → B: 4 Einheiten (Runde 1)"}) == \
        "[SEND] Send A → B: 4 Einheiten (Runde 1)"
    assert format_event({"kind": "match_end", "msg": "Match beendet — nächster Spieler kann joinen"}) == \
        "[MATCH-ENDE] Match beendet — nächster Spieler kann joinen"
    assert format_event({"kind": "unbekannt", "msg": "x"}) == "[UNBEKANNT] x"

    p = build_payload("hallo", "123", None)
    assert p == {"chat_id": "123", "text": "hallo"}
    p = build_payload("hallo", "123", "42")
    assert p == {"chat_id": "123", "text": "hallo", "message_thread_id": "42"}

    assert build_send_message_url("abc") == "https://api.telegram.org/botabc/sendMessage"
    print("telegram_feed self-test: OK")
    return 0


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        sys.exit(self_test())
    main()
