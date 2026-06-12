# AGENTS.md

Codex-Einstiegspunkt fuer dieses Repo. Halte diesen Text schlank; Details
stehen in den verlinkten Projektdateien.

## Kommunikation

- Antworte standardmaessig auf Deutsch, knapp und technisch konkret.
- Behauptungen des Users immer gegen Projektdateien, APIs, Logs oder den
  Datenbankzustand pruefen. Status nie aus Chat-Historie ableiten.
- Wenn du unsicher bist: erst Code/Doku lesen, dann urteilen.

## Session-Start

Lies zu Beginn einer neuen Arbeit mindestens:

1. `README.md` oder `README.de.md` fuer Zweck, Stack und Quickstart.
2. `architecture.md` fuer Ist-/Soll-Architektur, Sicherheits- und
   Betriebsregeln.
3. Den oberen Teil von `CHANGELOG.md` fuer die juengsten Aenderungen.
4. `backend/pyproject.toml` und `frontend/package.json` fuer Tooling.
5. Die relevanten Spezialdokus, wenn der Task sie beruehrt:
   - Betrieb/Pi-Status: `docs/operations.md`, `scripts/pi-check.sh`
   - Self-Update/Release: `docs/self_update.md`, `scripts/release.sh`,
     `scripts/self-update.sh`
   - Hunting-Picker: `docs/hunt_priority.md`
   - QSO-State-Machine: `docs/wsjtx_qso_state_audit.md`
   - TX-Leistung/Sicherheit: `docs/tx_power_safety.md`
   - Decoder/FT4/FT8: `docs/decoder_evolution.md`
   - CEPT/Auslandsbetrieb: `docs/cept_laenderliste.md`

Aktuellen Status aus den oben genannten Projektquellen sowie aus Tests,
APIs, Logs oder DB-Zustand ableiten. Dauerhafte Statusdateien gibt es
derzeit nicht. Falls spaeter `status.md`, `memory.md`, `TODO.md` oder
aehnliche Dateien auftauchen: vor jeder Arbeit lesen und nach relevanten
Status-/Wissensaenderungen aktualisieren.

## Projektbild

- Backend: Python 3.12, FastAPI, SQLAlchemy/SQLite, CFFI zu `vendor/ft8_lib`.
- Frontend: Svelte 5 + Vite; Build schreibt nach
  `backend/ft8_appliance/web/static/` und diese Release-Assets sind
  absichtlich im Repo.
- Produktivstart: systemd startet `uvicorn ft8_appliance.web.app:app`;
  `backend/ft8_appliance/main.py` ist nicht der produktive Einstiegspunkt.
- Kernlogik sitzt in `backend/ft8_appliance/runtime/orchestrator.py` und
  `backend/ft8_appliance/statemachine/machine.py`.

## Workflows und Checks

Backend:

```bash
cd backend
.venv/bin/pytest
```

Release-/Crash-Gates:

```bash
./scripts/typecheck.sh
./scripts/check_frontend_api.sh
```

Frontend:

```bash
cd frontend
npm run build
```

`npm run check` erwartet derzeit `frontend/jsconfig.json`; wenn diese Datei
fehlt, ist der Check selbst nicht lauffaehig. Nicht mit einem
Anwendungsfehler verwechseln.

Workstation-Demo ohne Pi:

```bash
python scripts/dev_run.py
```

Pi-Status nur aus Live-Quellen ableiten:

```bash
ssh ft8 'bash -s' < scripts/pi-check.sh
```

## Regeln beim Aendern

- Keine Anwendungscode-Aenderungen ohne Task-Bezug. Keine fremden
  Worktree-Aenderungen zuruecksetzen.
- Bei Backend-Aenderungen fokussierte Tests aus `backend/tests/` auswaehlen;
  bei State-Machine/Safety lieber die ganze Backend-Suite laufen lassen.
- Bei Frontend-Backend-Zugriffen nie rohes `fetch()` oder `EventSource`
  ausserhalb der zentralen Layer verwenden; `scripts/check_frontend_api.sh`
  ist dafuer das Gate.
- i18n ernst nehmen: UI und Backend-Texte sind DE/EN. Neue sichtbare
  Strings muessen in die passenden Kataloge und Gates.
- Release nicht manuell taggen. `scripts/release.sh vX.Y.Z` baut Frontend,
  aktualisiert `_version.py`, `CHANGELOG.md`, committed static assets und
  taggt.

## Sicherheit und Betrieb

- Das System steuert echte Sender-Hardware. TX-, PTT-, SWR-, ALC-,
  Lizenz-, Band- und Zeit-Guards nicht umgehen.
- Secrets gehoeren nicht ins Repo. Produktive Credentials liegen in
  `/etc/ft8-appliance/config.yaml`; API-Antworten muessen Secrets redakten.
- SQLite-QSO-Log ist die Source of Truth. Keine riskanten DB-Operationen
  ohne Backup-/Rollback-Verstaendnis.
- Operative Recovery auf dem Pi nur vorschlagen oder nach expliziter
  Bestaetigung ausfuehren. Destruktive Befehle und Service-Restarts nicht
  aus Chat-Annahmen ableiten.
