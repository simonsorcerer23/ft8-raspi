# FT8 Raspi Appliance

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Headless FT8/FT4 station controller running on a Raspberry Pi 5. Sits between
an Icom IC-705 / IC-7300 and the world, controlled entirely from a phone
browser. **Replaces WSJT-X** for portable / unattended-overseer use, with
features WSJT-X does not provide out of the box.

Operators: **DK9XR** (primary), **DO3XR** (secondary, multi-op).

---

## 📸 Screenshots

> Aufgenommen im eingebauten **Demo-Modus** — alle Rufzeichen/Daten sind rein
> fiktiv (Simulator), keine echten Dritt-Stationen.

![Funk-Ansicht: Rig-Status, Decode-Liste, Tagesstatistik](docs/screenshots/funk.png)

<details>
<summary><b>Karte &amp; Logbuch</b></summary>

### Weltkarte — Decodes, Coverage-Envelope, Gray-Line, Locator-Raster
![Karte](docs/screenshots/map.png)

### Logbuch — mit DXCC-, Kontinent- und Marinefunker-Filter
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

- **19-tier configurable picker** (drag-and-drop priority): pile-up
  avoidance, tail-end pickup, grayline boost, soft-blacklist learning from
  own QSO history, band-conditions awareness, buddy-seen
  (worked-on-other-band), DXCC rarity, 5BWAS, VUCC grid awards, …
- **Tail-End-Hunter** — automatic detection of `RR73`/`73` closings, picks
  the freed-up station within milliseconds. WSJT-X cannot do this.
- **Pile-Up-Avoidance** — when ≥5 unique callers on ±50 Hz of a station, we
  skip it. Better for the band, better for the QSO rate.
- **Multi-Operator** — two profiles (e.g. you + family), each with their own
  QRZ / Club Log credentials, separate log-views, license-aware power caps.
- **Auto-Logbook** — QSOs auto-upload to **QRZ.com** + **Club Log** in the
  background, offline-tolerant, idempotent. Local SQLite remains the source
  of truth.
- **Watchlist + ntfy push** for DXpeditions / wanted DX, auto-imported from
  the **NG3K ADXO** schedule.
- **Blitzortung lightning warning** — live WS stream, ntfy push when a
  strike lands inside a configurable radius.
- **License-aware safety** — Power cap, band lockout, SWR watchdog with
  live PTT-cut, ALC PI-loop instead of bang-bang.
- **CEPT / overseas operation** — GPS country detection via real border
  polygons (point-in-polygon, not crude rectangles), suggests the correct
  CEPT call-sign prefix, and knows where German **Klasse A vs Klasse E**
  may operate without a guest licence (DARC primary-source country list).
- **Password-protected API** — token auth on every endpoint (set a
  memorable login password from the config page); localhost is trusted so
  on-Pi tooling / self-update keep working. The ntfy lockscreen buttons use
  a separate, narrowly-scoped token.
- **Crash-safe data** — atomic config writes with `.bak` + fsync, WAL
  SQLite with busy-timeout, **QSO-log spill-to-file + alert** if a DB write
  ever fails (a completed contact is never silently lost), daily DB backup,
  telemetry retention, and secrets redacted from the API responses.
- **Self-update** — Pi pulls tagged releases from GitHub every 10 min,
  health-checks after restart, auto-rollback on failure.

## Architecture

Full spec: [architecture.md](./architecture.md)

```
backend/         Python 3.12 + FastAPI controller, ft8_lib via cffi
frontend/        Svelte 5 + Vite single-page app (mobile-first)
vendor/ft8_lib/  Kārlis Goba's FT8/FT4 codec (git submodule, MIT)
deploy/          systemd units, NetworkManager, hostapd, chrony, install.sh
data/            cty.dat (offline DXCC), map tiles, marinefunker, dxcc_rarity
docs/            Diagrams, notes, audit logs
scripts/         release.sh, self-update.sh, pi-check.sh, dev_run.py
```

## Quick-start — workstation (no Pi needed)

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

## Initial bring-up on a Pi

```bash
ssh pi@<host>
git clone https://github.com/simonsorcerer23/ft8-raspi.git /home/sebastian/ft8-appliance
cd /home/sebastian/ft8-appliance
sudo ./deploy/install.sh
```

Subsequent releases roll out automatically via `ft8-self-update.timer`.
Cut a new release on the workstation with:

```bash
./scripts/release.sh v0.41.0
```

This also updates [CHANGELOG.md](./CHANGELOG.md) (generated from the commit
log) and writes the change list into the tag annotation. The full version
history lives in [CHANGELOG.md](./CHANGELOG.md).

## Hardware

- **SBC:** Raspberry Pi 5 (4 GB sufficient, 8 GB nicer for bigger logs)
- **Storage:** NVMe SSD recommended for the QSO database
- **Radio:** Icom IC-705 or IC-7300 via single USB cable (CAT + audio)
  through `rigctld`. QMX/QMX+ has experimental support.
- **Audio:** Onboard USB CODEC of the rig (no extra sound card)
- **GPS:** Optional, helps with time + grid locator when portable

## Credentials & privacy

External services (QRZ, Club Log, ntfy, HamQTH, …) require per-operator
credentials. **Credentials live only in `/etc/ft8-appliance/config.yaml`
on the Pi** (`0600`), never in this repository or in version control. The
API redacts all secrets from its responses, and the web UI is gated by a
login password. See [CREDITS.md](./CREDITS.md) for the full list of
integrated services.

## License

MIT — see [LICENSE](./LICENSE). Third-party components are credited in
[CREDITS.md](./CREDITS.md).

## Status

Active development. Two Pis (`ft8`, `ft8-2`) in field-shake-down. Built
and used by a father-son team of amateur radio operators in Germany.

---

# Deutsch (Kurzfassung)

Headless FT8/FT4-Steuerung auf Raspberry Pi 5 für IC-705 / IC-7300. Sitzt
zwischen Rig und Welt, Bedienung komplett übers Handy (passwortgeschützt).
**Ersetzt WSJT-X** für portablen / unbeaufsichtigten Betrieb mit Features,
die WSJT-X nicht out of the box hat — 19-Tier-Picker mit Pile-Up-Avoidance,
Tail-End-Hunter, Watchlist, Auto-Upload zu QRZ + Club Log, Gewitter-Warnung,
lizenzabhängige Sicherheits-Caps, CEPT-/Ausland-Erkennung (GPS → Land →
Klasse-A/E-Regeln + Präfix-Vorschlag), bruchsicheres QSO-Log (Spill +
tägliches Backup) und Selbst-Update.

73 de DK9XR & DO3XR
