# Hochgericht FT8 Appliance

Headless FT8-Station auf Raspberry Pi 5 für den Icom IC-705, bedienbar per Android-Browser. Bewusst minimal — kein WSJT-X/Z im Hintergrund, eigenes Setup mit Auto-CQ, Auto-Reply, Live-Map und narrensicherer Bedienung für portablen Urlaubsbetrieb.

Operator: **DK9XR**

## Architektur

Vollständiges Lastenheft: [architecture.md](./architecture.md)

## Repo-Struktur

```
backend/         Python 3.12 + FastAPI Controller
frontend/        Svelte 5 + Vite SPA
vendor/ft8_lib/  C-Library für FT8 Decode/Encode (git submodule)
deploy/          systemd, NetworkManager, dnsmasq, hostapd, chrony, install.sh
data/            cty.dat (Offline-DXCC), Map-Tiles, ADIF
docs/            Diagramme, Notizen
scripts/         Helper-Skripte (Fetch, Build, Deploy)
```

## Quick-Start (Dev auf Workstation, ohne Pi-Hardware)

```bash
# Backend
cd backend
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"
pytest

# Frontend
cd frontend
npm install
npm run dev   # http://localhost:5173

# ft8_lib bauen (einmalig)
git submodule update --init --recursive
cd vendor/ft8_lib && make
```

## Bring-up auf Pi (initial — danach via self-update)

```bash
# Auf dem Pi (User sebastian, NOPASSWD-sudo)
git clone git@github.com:simonsorcerer23/ft8-raspi.git /home/sebastian/ft8-appliance
cd /home/sebastian/ft8-appliance && sudo ./deploy/install.sh
```

Spätere Updates erfolgen automatisch via `ft8-self-update.timer`, der die
neueste getaggte Release-Version aus dem GitHub-Repo holt. Siehe
[`docs/self_update.md`](docs/self_update.md).

Eine neue Release schneidet man auf der Workstation mit:

```bash
./scripts/release.sh v0.1.1
```

Das Script baut das Frontend, committed `backend/.../web/static/`, taggt
und pusht — die Pis holen sich's beim nächsten Timer-Tick (max ~10 min)
oder sofort via Button auf der Konfig-Seite.

## Status

In aktiver Entwicklung. Aktuelle Phase: Shake-Down, beide Pis (`ft8`, `ft8-2`) laufen.
