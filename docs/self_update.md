# Self-Update — wie Pis automatisch neue Releases ziehen

Konzipiert + implementiert 2026-05-24 (Initial v0.1.0 → kritische
Fixes v0.1.1-v0.1.4). Diese Doku spiegelt den Stand ab v0.1.4 wider.
Für historische Begründungen siehe commit-history.

## TL;DR

1. **Du auf Workstation:** `./scripts/release.sh v0.1.5` → frontend
   wird gebaut, static/ committed, `_version.py` aktualisiert,
   Tag erstellt, gepusht.
2. **Pi (ft8 + ft8-2)** holt sich den neuen Tag binnen ~10 min via
   `ft8-self-update.timer`. Pre-flight checks, panic-stop, checkout,
   sync system-files (units + sudoers), restart, health-probe.
3. **Wenn was schief geht:** automatischer Rollback auf vorherigen
   Commit-Hash, ntfy 🟡.
4. **Wenn auch Rollback schief geht:** ntfy 🔴, manueller Eingriff.

## Komponenten

| Pfad | Rolle |
|---|---|
| `scripts/release.sh` | Workstation-Side: Sanity-Checks + Frontend-Build + commit static/ + `_version.py` + tag + push |
| `scripts/self-update.sh` | Pi-Side: poll + idle-check + panic + checkout + unit-sync + restart + health-probe + rollback |
| `deploy/systemd/ft8-self-update.service` | systemd-Wrapper für self-update.sh, Type=oneshot, User=sebastian |
| `deploy/systemd/ft8-self-update.timer` | Boot+10min+04:00 poll-Trigger |
| `deploy/sudoers.d/ft8-self-update` | NOPASSWD scope: systemctl restart/start/is-active/daemon-reload + install + visudo |
| `backend/ft8_appliance/_version.py` | Single source of truth für installierte Version (auto-generiert von release.sh) |
| `backend/ft8_appliance/web/routes/system.py` | `/api/system/version` (read) + `/api/system/self-update` (POST trigger) |
| `frontend/src/components/SystemUpdateCard.svelte` | UI auf Konfig-Seite mit "Jetzt updaten"-Button |

## Versions-Schema

- Semver-Tags `vMAJOR.MINOR.PATCH` (z.B. `v0.1.0`, `v1.0.0`)
- Tags werden **immer** über `scripts/release.sh` gesetzt — nie direkt
  mit `git tag`. release.sh baut das Frontend mit, sonst hätte Pi nach
  Self-Update altes Frontend mit neuem Backend → weiße Seite.
- Pi vergleicht via `sort -V` und updated nur "vorwärts" — kein
  automatisches Downgrade.

## Pi-Side Flow (`self-update.sh`)

Pro Lauf, in dieser Reihenfolge:

1. `flock /tmp/ft8-self-update.lock` — nur eine Instanz gleichzeitig
2. `git fetch --tags --prune` — bei Network-Fail still exit 0
3. Latest semver tag bestimmen, mit `git describe` vergleichen
4. Wenn keine neuere Version: still exit 0
5. **Idle-Check** via `GET /api/status`: `state==IDLE && !current_qso_call && !rig.ptt`. Wenn nicht idle: skip + ntfy 🟡 + exit 0
6. Rollback-Target festhalten: `$(git rev-parse HEAD)`
7. `git checkout $LATEST_TAG`
8. Wenn `pyproject.toml` sich geändert hat: `pip install -e .[hardware]`
9. Wenn `vendor/ft8_lib/` da: `make` (no-op wenn unverändert)
10. `_build_ft8` für die cffi-Extension (no-op wenn unverändert)
11. **System-File-Sync** (siehe nächster Abschnitt)
12. **Panic** via `POST /api/control/panic` + 2s Sleep
13. `sudo systemctl restart ft8-controller`
14. **Health-Probe** via `GET /api/system/version` 200 OK (3 retries × 4s, 12s initial wait)
15. Bei Fail: Rollback (checkout zurück + pip + restart + Health-Probe)
16. Erfolg: ntfy 🟢

## System-File-Sync (kritisch)

Self-Update synchronisiert nicht nur den Code im git-Workdir, sondern
auch die installierten Versionen von:

- `/etc/sudoers.d/ft8-self-update`
- `/etc/systemd/system/ft8-controller.service`
- `/etc/systemd/system/ft8-self-update.service`
- `/etc/systemd/system/ft8-self-update.timer`
- `/etc/systemd/system/ft8-ap-fallback.service`
- `/etc/systemd/system/ft8-rigctld.service`

Logik in `sync_system_file()`: `cmp -s` zwischen repo-File und
installiertem File. Wenn unterschiedlich: `sudo install`, danach
`daemon-reload` einmalig (wenn mindestens ein File neu war).

**Sudoers ist Sonder-Fall:** Bevor wir das neue sudoers-Snippet
installieren, validieren wir die REPO-Version per
`sudo visudo -c -f <repo-path>`. Broken sudoers würde sonst zum
permanenten Lockout führen (kein NOPASSWD mehr → kein future
self-update mehr).

**Bootstrap-Problem:** Wenn das alte sudoers-Snippet auf dem Pi den
NEUEN install-Befehl noch nicht NOPASSWD erlaubt, schlägt sudo fehl.
Self-update.sh loggt das, schickt eine `⚙`-ntfy mit dem manuellen
Bootstrap-Befehl, und macht trotzdem mit dem systemctl restart weiter
(Best-Effort). Wenn die neue sudoers-Datei einen weiter eingeschränkten
oder weiter geöffneten Scope braucht, **manuell ein einmaliges
`scp + sudo install`** auf den Pi machen bevor man das Tag cuttet.

## Restart-Safety: Panic vor Restart

Vor `systemctl restart ft8-controller` ruft self-update.sh:

```
POST /api/control/panic
```

Das ruft im orchestrator `handle_panic()` auf:
1. State Machine in stop-state versetzen (`on_user_stop()`)
2. Pending TX-Actions drainen
3. `await self.rig.set_ptt(False)` — physisch PTT am rig deassert

**Wichtig:** `handle_panic` persistiert KEIN `boot_mode` (anders als
`handle_stop`). Nach restart liest der neue orchestrator `boot_mode` aus
der config — wenn vorher `hunt` aktiv war, resumed Hunt automatisch.

**Warum überhaupt:** Ohne panic hatten wir 2026-05-24 einen
PTT-Cascade-Bug. Self-update restartete Millisekunden vor einem
Slot-Übergang während ein TX-Burst gerade lief. Idle-Check sah IDLE
(state machine hatte das zurückgesetzt), aber das Signal war physisch
noch auf dem rig. Rig blieb mit PTT-on hängen, orchestrator weg. Neuer
Prozess sah PTT > 18s → force-off → dauerte zu lange → systemd-Watchdog
kill (siehe „Watchdog NICHT aktiv" unten) → SIGABRT-Cascade.

2 s Sleep nach panic-ack gibt rigctld Zeit, den deassert physisch an
das CAT-Interface zu schicken.

## Rollback-Logik

Wenn Health-Probe nach Update fehlschlägt (3 retries, 4s Abstand):

1. `git checkout $ROLLBACK_REF` (Commit-Hash, kein Tag)
2. `NEEDS_PIP=1` forcen (deps könnten geupgraded sein)
3. install_and_restart erneut für den alten Stand
4. ntfy 🟡 "rolled back zu $CURRENT_DESC"

Wenn auch Rollback fehlschlägt: ntfy 🔴 + exit 1 (systemd loggt).

**Was Rollback NICHT macht:** DB-Migrationen rückgängig (haben wir
heute auch keine). Wenn das kommt, brauchen wir migration-aware
Rollback (oder „nur vorwärts"-Politik).

## Health-Probe: `/api/system/version` 200 OK

Self-update.sh prüft NICHT `/api/healthcheck` für die Pickup-Validierung.
Grund: `healthcheck.overall` geht auf `red` sobald die rig-section
fail meldet (rig.freq_hz == None). Auf rig-losen Pis (ft8-2 als
Standby) ist das der BASELINE-Zustand, NICHT ein Regression-Signal.

`/api/system/version` 200 OK = Controller läuft, FastAPI antwortet,
Routes geladen. Genug Liveness-Signal.

Echte Regressions-Detection (kein Decode in X min, kein QSO über Tag
hinweg etc.) bleibt Sache der Mode-/Decode-Watchdogs im orchestrator.

## ntfy-Channels

- `ft8-system-ft8` (für ft8, Office-Pi)
- `ft8-system-ft8-2` (für ft8-2, Standby)

Beide einmal in der ntfy-App abonnieren. Server `https://ntfy.sh`.

Push-Formate:
- 🟢 `Update v0.1.3 → v0.1.4 ok (16s)`
- 🟡 `Update v0.1.4 verfügbar, skip (state=QSO_REPORT)`
- 🟡 `Update v0.1.4 health-check failed → rolled back zu v0.1.3`
- 🔴 `Update UND Rollback failed — manueller Eingriff: ssh ft8 ...`
- ⚙ `Self-Update: sudoers-Snippet hat sich geändert. Einmalig manuell installieren: ssh ft8 'sudo install -m 440 ~/...'`

## Frontend / UI

SystemUpdateCard auf der Konfig-Seite (oberhalb der eigentlichen
Konfig-Felder, lädt asynchron — ist auch sichtbar wenn die Konfig
selbst noch nicht geladen ist):

- Zeigt installierte Version + latest bekannte
- Button **"Jetzt updaten"** falls neue Version → `POST /api/system/self-update`
- Sonst Button **"Update-Check erzwingen"** (manueller Timer-Trigger)
- Live-Anzeige während Update läuft (Poll alle 3s statt 30s)
- Warnung-Banner falls Pi noch eine rsync-Installation ist (`repo_is_git=false`)

## Sudoers-Scope

`/etc/sudoers.d/ft8-self-update` erlaubt User `sebastian` **NOPASSWD**:

**systemctl** (eng begrenzt auf 2 Services):
- `systemctl restart ft8-controller[.service]`
- `systemctl start ft8-controller[.service]`
- `systemctl is-active ft8-controller[.service]`
- `systemctl start ft8-self-update[.service]`
- `systemctl daemon-reload`

**install** (exakte src→dst-Pärchen, keine Wildcards):
- jede unit-Datei + die sudoers-Datei selbst

**visudo** (für Pre-Install-Validation):
- `visudo -c -f` auf repo-pfad und auf installiertem pfad

Nichts sonst. Kein `sudo rm`, kein `sudo apt`, kein `sudo systemctl
restart <beliebig>`. Minimal-Scope, leicht zu auditieren.

## Einmalige Migration: rsync-Pi → git-Pi

Wurde 2026-05-24 für ft8 + ft8-2 durchgeführt. Vorgehen pro Pi:

```bash
# Auf der Workstation
ssh <pi> 'ssh-keygen -t ed25519 -f ~/.ssh/ft8_deploy -N "" -C "ft8-self-update@$(hostname)"'
ssh <pi> 'cat ~/.ssh/ft8_deploy.pub'   # → github.com Repo-Settings → Deploy keys (Read-only)

ssh <pi> 'cat >> ~/.ssh/config <<EOF
Host github.com
  HostName github.com
  User git
  IdentityFile ~/.ssh/ft8_deploy
  IdentitiesOnly yes
EOF
chmod 600 ~/.ssh/config'

# Pi (Service-Stop ~30s)
ssh <pi> 'sudo systemctl stop ft8-controller
          mv ~/ft8-appliance ~/ft8-appliance.rsync-backup-$(date +%Y%m%d-%H%M%S)
          git clone git@github.com:simonsorcerer23/ft8-raspi.git ~/ft8-appliance
          # tiles aus altem Pfad retten falls vorhanden (separater Persistenz-Mountpoint)
          sudo mv /opt/ft8-appliance/tiles /var/lib/ft8-appliance/tiles || true'

# Build (Reihenfolge wichtig — cffi linkt gegen libft8.a)
ssh <pi> 'cd ~/ft8-appliance/vendor/ft8_lib && make CFLAGS="-O3 -DHAVE_STPCPY -I. -fPIC"'
ssh <pi> 'cd ~/ft8-appliance/backend && python3 -m venv .venv \
          && .venv/bin/pip install -e .[hardware] \
          && .venv/bin/python -m ft8_appliance.decode._build_ft8'

# System-Files installieren
ssh <pi> 'sudo install -m 644 ~/ft8-appliance/deploy/systemd/ft8-controller.service    /etc/systemd/system/
          sudo install -m 644 ~/ft8-appliance/deploy/systemd/ft8-self-update.service   /etc/systemd/system/
          sudo install -m 644 ~/ft8-appliance/deploy/systemd/ft8-self-update.timer     /etc/systemd/system/
          sudo install -m 440 ~/ft8-appliance/deploy/sudoers.d/ft8-self-update         /etc/sudoers.d/ft8-self-update
          sudo visudo -c -f /etc/sudoers.d/ft8-self-update
          sudo systemctl daemon-reload
          sudo systemctl start ft8-controller
          sudo systemctl enable --now ft8-self-update.timer'

# Smoke-Test
ssh <pi> 'curl -s http://127.0.0.1:8000/api/system/version | jq'
```

Persistente Pfade (`/etc/ft8-appliance/config.yaml`,
`/var/lib/ft8-appliance/qso.sqlite`, `/var/lib/ft8-appliance/tiles/`)
bleiben unberührt — alles außerhalb des git-Workdirs.

## Failure-Modi & Recovery

| Symptom | Ursache | Recovery |
|---|---|---|
| ntfy 🟡 "skip (state=...)" alle 10 min | Pi macht permanent QSOs | OK by design. Update wartet auf Idle. |
| ntfy 🟡 "rolled back" | Neuer Release hat Bug | Bug fixen, neuen Tag cutten. Pi läuft weiter auf altem. |
| ntfy 🔴 "Update UND Rollback failed" | Schwerer Schaden | `ssh <pi> 'sudo systemctl status ft8-controller'` + `journalctl`, ggf. manuell git checkout + restart |
| ntfy ⚙ "sudoers hat sich geändert" | Neuer release hat erweiterten sudoers-Scope, alter erlaubt's nicht | Einmalig den im Push genannten Befehl ausführen |
| `update_in_progress=true` länger als 10 min | self-update.service hängt | `journalctl -u ft8-self-update -n 200`. TimeoutStartSec=600 würde killen. |
| `repo_is_git=false` in UI | Pi noch rsync-Installation | Migration durchführen (oben) |
| `latest_version` ändert sich nie | git fetch failed | `ssh <pi> 'cd ~/ft8-appliance && git fetch'` manuell. Letzter Fetch-Zeitpunkt steht im UI. |
| Beide Pis im SIGABRT-Restart-Loop ~30s | systemd-Watchdog ohne sd_notify-Wiring | siehe „Watchdog NICHT aktiv" |

## Watchdog NICHT aktiv (Stand v0.1.4)

`ft8-controller.service` hat **kein** `WatchdogSec` mehr. Bis v0.1.3
war's drin (`WatchdogSec=30`, `NotifyAccess=main`), aber das war eine
**Copy-Paste-Falle** — im orchestrator-Code wird nirgends
`sd_notify("WATCHDOG=1")` aufgerufen. Pre-Migration hatte das installierte
unit-File die Zeile nicht (rsync-Snapshot von einer alten Version), die
Migration hat sie blind mit-deployed. Pis liefen ~80 min lang in einer
SIGABRT-Cascade alle ~30s, hätte ich beim Pi-Check nicht bemerkt.

Status: **Liveness wird derzeit nur via `Restart=always` + `RestartSec=5`
abgesichert.** Wenn der Prozess crasht, kommt systemd in 5s wieder hoch.
Wenn er hängt (nicht crasht, aber antwortet nicht), läuft er hängend
weiter — kein liveness-watchdog.

Das ist OK für jetzt. Wenn wir's wieder einbauen wollen → siehe nächster
Abschnitt.

## Wie wir den echten Watchdog wieder einbauen

Plan in 5 Schritten — bewusst NICHT in v0.1.4 mit-erledigt damit der
PTT-Cascade-Fix sauber isoliert ist. Sebastian entscheidet wann.

### 1. Dependency
`backend/pyproject.toml` um `sdnotify` ergänzen:
```toml
dependencies = [
    ...
    "sdnotify>=0.3.2",
]
```

`sdnotify` ist Pure-Python, 200 Zeilen, keine native deps. Hört auf
`NOTIFY_SOCKET` env-var — auf nicht-systemd-Hosts (dev-Workstation,
Tests) wird's silent no-op.

### 2. Notifier in orchestrator instanziieren
In `Orchestrator.__init__` oder `start()`:
```python
import sdnotify
self._sd = sdnotify.SystemdNotifier()  # liest NOTIFY_SOCKET
```

### 3. READY=1 nach erfolgreicher Initialisierung
In `start()` ganz am Ende (nach allen `await self._wire_*`-Calls,
nach decode-source-init, nach erstem GPS-Read):
```python
self._sd.notify("READY=1")
log.info("sd_notify: READY")
```

systemd wird `Type=notify` erwarten, also Service-Unit muss
`Type=notify` setzen (statt `Type=simple`). Das bedeutet auch: systemd
wartet bis READY=1 vor dem "active"-Status. uvicorn-Startup dauert
~3-5s, das ist OK.

### 4. Heartbeat-Loop
Eigene asyncio-Task in `start()`:
```python
asyncio.create_task(self._sd_heartbeat_loop(), name="sd-heartbeat")
```

```python
async def _sd_heartbeat_loop(self) -> None:
    """Notifiy systemd jede ~10s (1/3 von WatchdogSec=30, Standard).

    Wenn dieser Loop hängt, killt systemd den Prozess — genau das was
    wir wollen (liveness-detection). Daher: NICHT in try/except packen,
    nicht „defensive". Wenn was unten in der Pipeline asyncio blockt,
    soll's hier auch hängen → systemd löst.
    """
    while True:
        await asyncio.sleep(10)
        self._sd.notify("WATCHDOG=1")
```

**Wichtig:** kein try/except außen rum. Genau dann wenn der heartbeat
selbst ein Problem hat (zB. event-loop hängt), MUSS systemd killen.

### 5. Service-Unit reactivieren
`deploy/systemd/ft8-controller.service`:
```
[Service]
Type=notify          # (war: simple)
NotifyAccess=main
WatchdogSec=30
```

**Sebastian's UI/Restart-Risiko:** wenn Watchdog ausreißt, restartet
Pi mehrfach. Wenn man mit `WatchdogSec=30` und `OnFailure`-actions wild
wird, kann eine Endlos-Restart-Schleife entstehen. Mitigation:
`StartLimitIntervalSec=120` + `StartLimitBurst=5` setzen — nach 5
Restarts in 120s gibt systemd auf und setzt Service auf failed. Dann
ntfy via mode-watchdog (decode-watchdog feuert wenn der Pi länger als
mode_watchdog_min ohne Decodes ist).

### 6. Testen vorm Release
1. Lokal: `uvicorn ...` ohne NOTIFY_SOCKET → muss problemlos starten
   (sdnotify silent no-op)
2. Auf einem Pi VOR Tag-Cut manuell deployen (scp + systemd reload) und
   30 min beobachten — KEINE Watchdog-Kills im Journal erwartet
3. Stress-Test: bewusst eine `time.sleep(60)` in einer asyncio-Task
   einbauen, deployen → Watchdog SOLL killen → reverten
4. Dann erst Tag cutten

### 7. Tag-Bundle
Watchdog wieder zusammen mit ggf. anderen Themen in einen Release wie
`v0.2.0` (Minor-Bump weil neues Dependency + Verhaltens-Änderung).
Watchdog selbst ist kein Breaking Change für den User, aber semver-
korrekt ist's ein Feature.

## Was Self-Update NICHT macht

- **Keine DB-Migrationen** (haben wir auch keine). Rollback ist heute
  code-only safe. Wenn migrations kommen, brauchen wir migration-aware
  Variante oder „nur vorwärts"-Politik.
- **Kein OS/apt-Update**. Manuell (`sudo apt update && sudo apt upgrade`).
- **Kein Auto-Tag**. Sebastian-Mandat: nur explizite gewollte Releases.
- **Kein Push zurück**. Deploy-Key ist Read-only — selbst wenn Pi
  kompromittiert wäre, kein Schreiben ins Repo möglich.
- **Kein npm/node auf dem Pi**. Frontend wird auf Workstation gebaut
  und als `backend/.../web/static/` ins Repo committed (per
  `release.sh`).

## Lessons learned (v0.1.0 → v0.1.4)

| # | Bug entdeckt in | Fix in | Lesson |
|---|---|---|---|
| 1 | cffi-Race in install.sh (pip vor ft8_lib) | v0.1.0 | Build-Order checken, „(already up-to-date)" lügt manchmal |
| 2 | Health-Probe overall=red baseline auf rig-losen Pis | v0.1.1 | Liveness ≠ Quality. Probe muss reine Existence-Frage stellen, nicht aggregierten Status |
| 3 | PTT-Cascade durch Restart mit physisch asserted PTT | v0.1.2 | Idle = state machine AND physical state. graceful pre-restart via API. |
| 4 | Unit-files / sudoers wurden nie synced | v0.1.2 | self-update muss vollständig sein, nicht nur Code |
| 5 | "Hochgericht" Branding im service-Unit-Description | v0.1.3 | systemd-Description ist sichtbar in `systemctl status`, mit-aufräumen |
| 6 | WatchdogSec=30 ohne sd_notify-Wiring | v0.1.4 | Copy-paste von Service-Direktiven IMMER gegen Code-Realität checken |

Half-Fix-Reflex-Lehre: Bug 6 hatte ich beim Service-Unit-Schreiben
direkt drin und niemand hat's gesehen bis zum Pi-Check. Beim
Phase-Abschluss systemd-Direktiven gegen die Realität des Codes
prüfen — sonst kostet's später eine SIGABRT-Cascade.
