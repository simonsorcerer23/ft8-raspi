# FT8 Raspi Appliance

[🇬🇧 English](README.md) · **🇩🇪 Deutsch**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Headless FT8/FT4-Stationssteuerung auf einem Raspberry Pi 5. Sitzt zwischen
einem Icom IC-705 / IC-7300 und der Welt, komplett über den Handy-Browser
bedient. **Ersetzt WSJT-X** für portablen / unbeaufsichtigten Betrieb — mit
Funktionen, die WSJT-X out of the box nicht bietet.

Operatoren: **DK9XR** (primär), **DO3XR** (sekundär, Multi-Op).

---

## 📸 Screenshots

> Aufgenommen im eingebauten **Demo-Modus** — alle Rufzeichen/Daten sind rein
> fiktiv (Simulator), keine echten Dritt-Stationen.

![Funk-Ansicht: Rig-Status, Decode-Liste, Tagesstatistik](docs/screenshots/funk.png)

<details>
<summary><b>Karte &amp; Logbuch</b></summary>

### Weltkarte — Decodes, Coverage-Envelope, Gray-Line, Locator-Raster
![Karte](docs/screenshots/map.png)

### Logbuch — mit DXCC-/Kontinent-/Marinefunker-Filter
![Logbuch](docs/screenshots/log.png)

</details>

<details>
<summary><b>DX-Jagd</b> — Watchlist · Reputation · DXpedition · Blacklist · Empfänger</summary>

### Watchlist — Wunsch-DX/DXpeditionen mit ntfy-Alarm
![Watchlist](docs/screenshots/watchlist.png)

### Reputation — Soft-Blacklist nach Stationsverhalten
![Reputation](docs/screenshots/reputation.png)

### DXpedition — NG3K-Kalender-Integration
![DXpedition](docs/screenshots/DXpedition.png)

### Blacklist — manuell gesperrte Rufzeichen
![Blacklist](docs/screenshots/blacklist.png)

### Empfänger — wer hat uns gehört (PSK-Reporter)
![Empfänger](docs/screenshots/psk.png)

</details>

<details>
<summary><b>Statistik &amp; Konfiguration</b></summary>

### Statistik &amp; Steuerung — SWR-Trend, beste Zeiten, Pi-Status, TX-Controls
![Statistik](docs/screenshots/stats.png)

### Hunt-Priorität — die 19 frei sortierbaren Picker-Stufen
![Hunt-Priorität](docs/screenshots/config_3.png)

### Operatoren &amp; Logbücher — Multi-Op, QRZ/ClubLog, Demo-Schalter
![Konfiguration: Operatoren](docs/screenshots/config_1.png)

### Bänder &amp; Antennen
![Konfiguration: Bänder](docs/screenshots/config_2.png)

### Integrationen — QRZ/ClubLog/PSK/Blitzortung, ALC, ntfy
![Konfiguration: Integrationen](docs/screenshots/config_4.png)

</details>

---

## Highlights

- **19-stufiger konfigurierbarer Picker** (Priorität per Drag-and-drop):
  Pile-Up-Vermeidung, Tail-End-Pickup, Grayline-Boost, Soft-Blacklist die aus
  der eigenen QSO-Historie lernt, Band-Conditions-Bewusstsein, Buddy-Seen
  (auf anderem Band gearbeitet), DXCC-Seltenheit, 5BWAS, VUCC-Grid-Awards, …
- **Tail-End-Hunter** — erkennt `RR73`/`73`-Abschlüsse automatisch und greift
  die freiwerdende Station in Millisekunden ab. Das kann WSJT-X nicht.
- **Pile-Up-Vermeidung** — bei ≥5 verschiedenen Anrufern auf ±50 Hz wird die
  Station übersprungen. Besser fürs Band, besser für die QSO-Rate.
- **Multi-Operator** — zwei Profile (z.B. du + Familie), jeweils mit eigenen
  QRZ-/Club-Log-Zugängen, getrennten Log-Ansichten, lizenzabhängigen
  Leistungs-Caps.
- **Auto-Logbuch** — QSOs werden im Hintergrund automatisch zu **QRZ.com** +
  **Club Log** hochgeladen, offline-tolerant, idempotent. Lokales SQLite
  bleibt die Quelle der Wahrheit.
- **Watchlist + ntfy-Push** für DXpeditionen / Wunsch-DX, automatisch aus dem
  **NG3K-ADXO**-Kalender importiert.
- **Blitzortung-Gewitterwarnung** — Live-WS-Stream, ntfy-Push wenn ein
  Einschlag innerhalb eines konfigurierbaren Radius landet.
- **Lizenzabhängige Sicherheit** — Leistungs-Cap, Band-Sperre, SWR-Watchdog
  mit Live-PTT-Abschaltung, ALC-PI-Regelschleife statt Bang-Bang.
- **CEPT / Auslandsbetrieb** — GPS-Länder-Erkennung über echte Grenz-Polygone
  (Point-in-Polygon, keine groben Rechtecke), schlägt das korrekte
  CEPT-Präfix vor und weiß, wo deutsche **Klasse A vs. Klasse E** ohne
  Gastlizenz funken dürfen (DARC-Primärquellen-Länderliste).
- **Passwortgeschützte API** — Token-Auth auf jedem Endpoint (merkbares
  Login-Passwort auf der Konfig-Seite setzbar); localhost ist vertraut, damit
  On-Pi-Tooling / Self-Update weiterlaufen. Die ntfy-Sperrbildschirm-Buttons
  nutzen ein separates, eng begrenztes Token.
- **Bruchsichere Daten** — atomare Config-Writes mit `.bak` + fsync, WAL-SQLite
  mit Busy-Timeout, **QSO-Log-Spill-to-File + Alarm** falls ein DB-Write je
  fehlschlägt (ein abgeschlossenes QSO geht nie still verloren), tägliches
  DB-Backup, Telemetrie-Retention, Secrets aus den API-Antworten redactet.
- **Self-Update** — der Pi holt sich getaggte Releases alle 10 min von GitHub,
  Health-Check nach Neustart, Auto-Rollback bei Fehler.

## Architektur

Vollständige Spezifikation: [architecture.md](./architecture.md)

```
backend/         Python 3.12 + FastAPI-Controller, ft8_lib via cffi
frontend/        Svelte 5 + Vite Single-Page-App (mobile-first)
vendor/ft8_lib/  Kārlis Gobas FT8/FT4-Codec (git-Submodul, MIT)
deploy/          systemd-Units, NetworkManager, hostapd, chrony, install.sh
data/            cty.dat (offline DXCC), Map-Tiles, marinefunker, dxcc_rarity
docs/            Diagramme, Notizen, Audit-Logs
scripts/         release.sh, self-update.sh, pi-check.sh, dev_run.py
```

## Schnellstart — Workstation (kein Pi nötig)

```bash
git submodule update --init --recursive
cd vendor/ft8_lib && make && cd ../..

cd backend
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"
pytest

cd ../frontend
npm install
npm run dev   # http://localhost:5173
```

## Erst-Inbetriebnahme auf einem Pi

```bash
ssh pi@<host>
git clone https://github.com/simonsorcerer23/ft8-raspi.git /home/sebastian/ft8-appliance
cd /home/sebastian/ft8-appliance
sudo ./deploy/install.sh
```

Folge-Releases werden automatisch über `ft8-self-update.timer` ausgerollt.
Ein neues Release auf der Workstation schneidest du mit:

```bash
./scripts/release.sh v0.41.0
```

Das aktualisiert auch [CHANGELOG.md](./CHANGELOG.md) (aus dem Commit-Log
generiert) und schreibt die Änderungsliste in die Tag-Annotation. Die volle
Versionshistorie steht in [CHANGELOG.md](./CHANGELOG.md).

## Hardware

- **SBC:** Raspberry Pi 5 (4 GB reichen, 8 GB schöner für größere Logs)
- **Storage:** NVMe-SSD empfohlen für die QSO-Datenbank
- **Funkgerät:** Icom IC-705 oder IC-7300 über ein einziges USB-Kabel
  (CAT + Audio) via `rigctld`. QMX/QMX+ experimentell unterstützt.
- **Audio:** Onboard-USB-CODEC des Rigs (keine extra Soundkarte)
- **GPS:** Optional, hilft bei Zeit + Locator im portablen Betrieb

## Zugänge & Datenschutz

Externe Dienste (QRZ, Club Log, ntfy, HamQTH, …) brauchen pro-Operator-
Zugänge. **Zugangsdaten liegen ausschließlich in
`/etc/ft8-appliance/config.yaml` auf dem Pi** (`0600`), nie in diesem Repo
oder in der Versionskontrolle. Die API redactet alle Secrets aus ihren
Antworten, und das Web-UI ist per Login-Passwort geschützt. Die vollständige
Liste der integrierten Dienste steht in [CREDITS.md](./CREDITS.md).

## Lizenz

MIT — siehe [LICENSE](./LICENSE). Drittkomponenten sind in
[CREDITS.md](./CREDITS.md) genannt.

## Status

Aktive Entwicklung. Zwei Pis (`ft8`, `ft8-2`) im Feld-Test. Gebaut und
genutzt von einem Vater-Sohn-Team von Funkamateuren in Deutschland.

73 de DK9XR & DO3XR
