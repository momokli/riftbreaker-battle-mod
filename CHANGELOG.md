Version: 1.0.1
Date: 23. 09. 2026

  Features:
    - Session-Trace — die DLL loggt pro Request `cmd` + `session`; die Bridge haengt die Session-ID zentral an jeden an die DLL gesendeten Command (#392).

  Bugfixes:
    - `get_state`-Crash behoben (DEP, jump-to-garbage in `dbg`->`fprintf`): stderr wird jetzt per Handle-Write ohne writable-`.data`-CRT-Zeiger geschrieben (#688).
    - `/health` meldet Pipe-Ausfall ehrlich (Readiness aus der persistenten Pipe statt Zweit-Connect) und der Pipe-Server heilt sich selbst (#902).

  CI:
    - boot-test pollt den Boot-Health statt hartcodiertem `sleep 30` (#893).
    - shellcheck als gepinntes Static-Binary statt apt-Installation (#894).
    - `build`-Job laeuft parallel zu `test` statt `needs: [changes, test]` (#896).
    - Required-Check deploy-check-local wieder gruen (deploy-wiring-Vertrag statt entfernter Park-Verdrahtung) (#337).
    - Path-Filter-Gate abgeschlossen: DLL-Änderung -> Boot-Test, Mod/WebUI -> leicht (#393).

  Intern:
    - Epic 1.0.1 "Solid & schnell" abgeschlossen — CI wieder verlaesslich gruen und deutlich kuerzer, Deploy-Gates luegen nicht mehr (#724).
