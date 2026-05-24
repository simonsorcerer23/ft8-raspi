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

## Bring-up auf Pi (sobald Hardware da)

```bash
# Vom Workstation aus
rsync -av --exclude='.venv' --exclude='node_modules' . pi@ft8.local:/opt/ft8-appliance/

# Auf dem Pi
ssh pi@ft8.local
cd /opt/ft8-appliance && sudo ./deploy/install.sh
```

## Status

In aktiver Entwicklung, vor Hardware-Eintreffen. Bring-up-Phasen siehe `architecture.md` §9.2.
