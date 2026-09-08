#!/usr/bin/env bash
#
# fake-log.sh — simuliert die [RBBATTLE]-Zeilen des Spiels (Baustein 03/05),
# damit der Relay (07) ohne Spiel getestet werden kann.
#
# Aufruf:  bash fake-log.sh [ziel-datei]     (Default: fake_exor_logs.txt)
# Beenden: Strg+C
#
# Geschrieben werden Events, die der Tournament-Server akzeptiert:
#   score_update (Punktestand), wave_sent (Welle zum Gegner) und
#   zwischendurch eine Zeile, die der Server NICHT kennt (bridge_test) —
#   der Relay soll sie überspringen (loggt "nicht server-faehig").

set -u
LOG="${1:-fake_exor_logs.txt}"
echo "[fake-log] schreibe [RBBATTLE]-Zeilen nach: $LOG  (Strg+C = Ende)"
: > "$LOG"
n=0
while true; do
  n=$((n + 1))
  echo "[RBBATTLE] event=score_update score=$((n * 100)) wave=$(( (n % 3) + 1 ))" >> "$LOG"
  sleep 2
  echo "[RBBATTLE] event=wave_sent level=$(( (n % 3) + 1 )) cost=$(( n * 10 ))" >> "$LOG"
  sleep 2
  if [ $((n % 3)) -eq 0 ]; then
    echo "[RBBATTLE] event=bridge_test status=done" >> "$LOG"
    sleep 1
  fi
done
