# Self-Update — wie Pis automatisch neue Releases ziehen

Konzipiert + implementiert: 2026-05-24 (siehe commit-history für
exakten Stand).

## TL;DR

1. **Du auf Workstation:** `./scripts/release.sh v0.1.1` → frontend
   wird gebaut, static/ committed, Tag erstellt, gepusht.
2. **Pi (ft8 + ft8-2)** holt sich den neuen Tag binnen ~10 min via
   `ft8-self-update.timer`, prüft ob idle, checkt aus, restartet
   `ft8-controller`, healthchecked, sendet ntfy.
3. **Wenn was schief geht:** Rollback auf vorherigen Tag, ntfy 🟡.
4. **Wenn auch Rollback schief geht:** ntfy 🔴, manueller Eingriff.

## Komponenten

| Pfad | Rolle |
|---|---|
| `scripts/release.sh` | Workstation-Side: Frontend build + commit + tag + push |
| `scripts/self-update.sh` | Pi-Side: poll + checkout + restart + healthcheck + rollback |
| `deploy/systemd/ft8-self-update.service` | systemd-Wrapper für self-update.sh |
| `deploy/systemd/ft8-self-update.timer` | Boot+10min+04:00 poll-Trigger |
| `deploy/sudoers.d/ft8-self-update` | NOPASSWD für genau 4 systemctl-Befehle |
| `backend/ft8_appliance/_version.py` | Single source of truth für installierte Version |
| `backend/ft8_appliance/web/routes/system.py` | `/api/system/version` + `/api/system/self-update` |
| `frontend/src/components/SystemUpdateCard.svelte` | UI auf Konfig-Seite |

## Versions-Schema

- Semver-Tags: `vMAJOR.MINOR.PATCH` (z.B. `v0.1.0`, `v0.1.1`, `v1.0.0`)
- Tags werden **immer** über `scripts/release.sh` gesetzt — nie direkt
  mit `git tag`. Sonst kann das Frontend out-of-sync mit dem Backend
  sein (alt-builds, weiße Seite).
- Pi vergleicht semver via `sort -V` und updatet nur "vorwärts" — kein
  automatisches Downgrade wenn jemand einen Tag löscht/zurückrolliert.

## Restart-Safety

Self-Update macht NIE einen Restart mitten in einem QSO:

1. Vor jedem Update: `curl http://127.0.0.1:8000/api/status` →
   `state` muss `IDLE` sein UND `current_qso_call` leer UND
   `rig.ptt != true`.
2. Wenn nicht idle: **skip**, ntfy 🟡, exit 0. Timer feuert in 10 min
   wieder.
3. Wenn idle: durchziehen.
4. Edge-Case: Wenn das Backend nicht antwortet (Controller crashed),
   wird trotzdem geupdated — vermutlich pullt man dann gerade einen Fix.

## Rollback-Logik

Wenn nach `git checkout $NEUER_TAG` + `pip install` + `systemctl restart`:

1. 12 s warten, dann `curl /api/healthcheck`. Erwartet `overall` in
   `{"green","yellow"}`.
2. 3 retries mit 4s Abstand.
3. Wenn alle 3 fail: `git checkout $ALTER_HASH` + pip + restart →
   ntfy 🟡 "rolled back".
4. Wenn auch Rollback failt: ntfy 🔴 + exit 1 (systemd loggt Fehler).

Rollback-Target ist der Commit-Hash vor dem Update — kein expliziter
Tag/Branch. Saubere Disconnect-Strategie auch bei force-push-Edge-Cases.

## ntfy-Channels

Self-Update nutzt eigene Topics, getrennt von den Operator-QSO-Pushes:

- `ft8-system-ft8` — für ft8 (Office-Pi am IC-7300)
- `ft8-system-ft8-2` — für ft8-2 (Standby-Pi)

Du musst beide **einmal** in deiner ntfy-App abonnieren. Server ist
`https://ntfy.sh` (gleich wie für die QSO-Topics).

Format der Pushes:
- 🟢 `Update v0.1.0 → v0.1.1 ok (8s)` — alles gut
- 🟡 `Update v0.1.1 verfügbar, skip (state=QSO_REPORT)` — kein Restart wegen aktivem QSO
- 🟡 `Update v0.1.1 health-check failed → rolled back zu v0.1.0` — Rollback war erfolgreich
- 🔴 `Update UND Rollback failed — manueller Eingriff: ssh ft8 'sudo systemctl status ft8-controller'` — kaputt

## Frontend / UI

Konfig-Seite hat oben eine **SystemUpdateCard**:

- Zeigt installierte Version + latest bekannte
- Button **"Jetzt updaten"** falls neue Version verfügbar — sonst
  **"Update-Check erzwingen"** (= manueller Trigger des Timers).
- Live-Anzeige während Update läuft (Poll alle 3s).
- Hinweis-Banner falls Pi noch eine rsync-Installation ist (kein `.git`).

API:
- `GET /api/system/version` → Pydantic-Response mit allen Feldern (siehe
  `backend/ft8_appliance/web/routes/system.py:VersionInfo`).
- `POST /api/system/self-update` → 202 Accepted (oder 409 wenn Pi kein
  git, oder 500 wenn sudoers-Snippet kaputt).

## Einmalige Migration: rsync-Pi → git-Pi

Pis (ft8 + ft8-2) waren bisher rsync-Kopien. Migration auf git-clone:

```bash
# Auf der Workstation: SSH-Deploy-Key auf Pi erzeugen
ssh ft8 'ssh-keygen -t ed25519 -f ~/.ssh/ft8_deploy -N "" -C "ft8-self-update@$(hostname)"'
ssh ft8 'cat ~/.ssh/ft8_deploy.pub'  # → in github.com/.../settings/keys einfügen, Read-only

# ~/.ssh/config auf Pi anpassen damit git github.com via Deploy-Key kontaktiert
ssh ft8 'cat >> ~/.ssh/config <<EOF
Host github.com
  HostName github.com
  User git
  IdentityFile ~/.ssh/ft8_deploy
  IdentitiesOnly yes
EOF'

# Migration auf Pi (manuell, EINMALIG, mit Service-Stop)
ssh ft8 'sudo systemctl stop ft8-controller'
ssh ft8 'mv ~/ft8-appliance ~/ft8-appliance.rsync-backup-$(date +%Y%m%d)'
ssh ft8 'git clone git@github.com:simonsorcerer23/ft8-raspi.git ~/ft8-appliance'

# Persistente Daten zurückrücken: venv neu, tiles + db bleiben in /var/lib
ssh ft8 'sudo mv /opt/ft8-appliance/tiles /var/lib/ft8-appliance/tiles || true'

ssh ft8 'cd ~/ft8-appliance/backend && python3 -m venv .venv \
  && .venv/bin/pip install -e .[hardware] \
  && .venv/bin/python -m ft8_appliance.decode._build_ft8'
ssh ft8 'cd ~/ft8-appliance/vendor/ft8_lib \
  && make CFLAGS="-O3 -DHAVE_STPCPY -I. -fPIC"'

# Updated service-unit + sudoers + timer installieren
ssh ft8 'sudo install -m 644 ~/ft8-appliance/deploy/systemd/ft8-controller.service /etc/systemd/system/'
ssh ft8 'sudo install -m 644 ~/ft8-appliance/deploy/systemd/ft8-self-update.service /etc/systemd/system/'
ssh ft8 'sudo install -m 644 ~/ft8-appliance/deploy/systemd/ft8-self-update.timer /etc/systemd/system/'
ssh ft8 'sudo install -m 440 ~/ft8-appliance/deploy/sudoers.d/ft8-self-update /etc/sudoers.d/'
ssh ft8 'sudo visudo -c -f /etc/sudoers.d/ft8-self-update'   # validation

ssh ft8 'sudo systemctl daemon-reload \
  && sudo systemctl start ft8-controller \
  && sudo systemctl enable --now ft8-self-update.timer'

# Smoke-Test
ssh ft8 'curl -s http://127.0.0.1:8000/api/system/version | jq'
ssh ft8 'systemctl list-timers ft8-self-update.timer --no-pager'
```

Gleicher Block für `ft8-2`. Backup-Verzeichnis (`~/ft8-appliance.rsync-backup-*`)
kann nach 1 Woche manuell weg.

## Test-Plan nach Initial-Setup

1. **Du:** `./scripts/release.sh v0.1.0` auf Workstation → push.
2. **Ich (oder du):** auf ft8 manuell `sudo systemctl start ft8-self-update.service`
   triggern, dann `journalctl -u ft8-self-update -f` beobachten.
3. ntfy-Push 🟢 sollte ankommen.
4. UI: Konfig-Seite → SystemUpdateCard zeigt `v0.1.0` als installiert,
   `update_available=false`.
5. Du machst `./scripts/release.sh v0.1.1` → wartest 10 min ODER
   klickst "Jetzt updaten" im UI → ntfy 🟢.

## Failure-Modi & Recovery

| Symptom | Wahrscheinliche Ursache | Recovery |
|---|---|---|
| ntfy 🟡 "skip (state=...)" alle 10 min | Pi macht permanent QSOs | OK, ist Design. Update wartet auf nächsten Idle-Slot. |
| ntfy 🟡 "rolled back" | Neuer Release hat Bug, Rollback griff | Bug fixen, neue Version cutten. Pi läuft inzwischen weiter auf alter Version. |
| ntfy 🔴 "Update UND Rollback failed" | Schwerwiegender Schaden | `ssh ft8 'sudo systemctl status ft8-controller'`, ggf. `git checkout <bekannt-gut-Tag>` manuell + restart. |
| `update_in_progress=true` länger als 10 min | systemd-Service hängt | `ssh ft8 'sudo journalctl -u ft8-self-update -n 200 --no-pager'` — TimeoutStartSec=600 würde es eh killen. |
| UI zeigt `repo_is_git=false` | Pi ist noch rsync-Installation | Migration durchführen (siehe oben). |
| `latest_version` ändert sich nie | git fetch failed (Deploy-Key kaputt?) | `ssh ft8 'cd ~/ft8-appliance && git fetch'` manuell. Letzter Fetch-Zeitpunkt steht im UI. |

## Was Self-Update NICHT macht

- Es macht **keine** DB-Migrationen (haben wir aktuell auch keine).
  Wenn das kommt, brauchen wir eine "migration-aware"-Variante. Heute
  ist Rollback safe weil rein code-basiert.
- Es updated **nicht** das OS oder apt-pakete. Das bleibt manuell
  (`sudo apt update && sudo apt upgrade` von dir).
- Es taggt **nicht** automatisch (Sebastian-Mandat: nur explizite,
  gewollte Releases gehen raus).
- Es push'd **nicht** zurück (Deploy-Key ist Read-only — auch wenn
  Pi kompromittiert wäre, könnte er nichts ins Repo schreiben).
