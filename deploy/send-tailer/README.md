# send-tailer — env-agnostischer Send-Ingress (Issue #713)

Tailt die Dedi-Logdatei `exor_logs.txt` und übersetzt die serverseitigen
Mod-Chat-Zeilen des Markt-Buttons in Send-Orders:

```
[RBBATTLE] button_chat sent: -send wave1        →  POST /order {"name":"wave1"}
[RBBATTLE:...] event=chat_send ... text=-send wave9  →  POST /order {"name":"wave9"}
```

## Warum ein Tailer?

Der Markt-Button sendet Chat **serverseitig** via `QueueEvent("PlayerChatRequest", …)`.
Das läuft nicht durch den Netz-Handler `OnNetPlayerChatRequest`, an dem der
C++-Chat-Detour (#549) hängt. Der Chat-Text steht aber im Log — dieser Sidecar
liest ihn dort und reicht die Order an die Bridge (`POST /order`) weiter.

Er ist das env-agnostische Gegenstück zu `deploy/session-recorder` (gleicher
`LogTailer`, gleiche `default_log_paths`). Kein C++/RE, kein Button-Umbau.

## Ablauf (mit Bridge + Scheduler)

1. `send_tailer.py` POSTet `{"name":"wave1"}` an die Bridge `/order`.
2. Bridge: `lookup_order_spec` → `enqueue_order` (fire in 5 min).
3. Scheduler: bezahlt SOFORT (`try_spend`) und feuert die Welle nach 5 min
   **einmal**.

## Test

```bash
cd deploy/send-tailer
python3 -m unittest test_send_tailer -v
```

Hermetisch: `parse_send_order` + der POST-Pfad laufen gegen einen Fake-Poster —
kein Netz, kein Spiel, kein DOM.
