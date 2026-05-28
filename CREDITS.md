# Credits & Third-Party Components

This project would not be possible without the following open-source projects
and data sources. Each is attributed below with its individual license terms.

## Vendored Code

### ft8_lib — FT8/FT4 codec
- **Author:** Kārlis Goba (YL3JG)
- **Source:** https://github.com/kgoba/ft8_lib
- **License:** MIT
- **Location:** `vendor/ft8_lib/`

Provides the core FT8 decoder/encoder used by `backend/ft8_appliance/decode/`.
Bundled verbatim as a git submodule with upstream's own LICENSE file preserved.

## Data Files

### cty.dat — DXCC country file
- **Author:** Jim Reisert (AD1C)
- **Source:** https://www.country-files.com/
- **License:** Freely redistributable for non-commercial amateur radio use
- **Location:** `data/cty.dat`

Used for DXCC entity / continent / lat-lon lookup of any amateur callsign.
Update by downloading the latest `cty.dat` from country-files.com.

### Marinefunker membership list
- **Source:** Marinefunker-Diplom-Suchliste (PDF, public-domain reference of
  current DOK-Z01 members)
- **Format:** Parsed into `marinefunker.json` via `scripts/import_marinefunker.py`
- **Use:** Maritime mobile fraternity badge / hunt-tier priority

### DXCC rarity scores
- **Source:** Compiled from ClubLog "Most Wanted" lists and community DX
  resources (see `dxcc_rarity.json`)
- **Use:** Picker priority for rare DXCC entities

## External Services Consumed

The appliance integrates with the following services. None of them are
included in this repository — they are reached over the network at runtime
and each operator must provide their own credentials. Their use is subject
to the respective service's terms.

| Service | Purpose | Auth |
|---|---|---|
| [QRZ.com](https://www.qrz.com) | Callsign XML lookup + auto-Logbook | XML user/password + logbook API key |
| [HamQTH](https://www.hamqth.com) | Callsign lookup fallback | Optional user/password |
| [PSK Reporter](https://pskreporter.info) | Reception reports (who heard us) | None |
| [HamQSL Solar](https://www.hamqsl.com) | Solar/propagation conditions | None |
| [Blitzortung.org](https://www.blitzortung.org) | Real-time lightning strikes | None (public WS) |
| [Club Log](https://clublog.org) | Auto-Logbook + DXCC awards | Email + App Password + API Key |
| [NG3K ADXO](http://www.ng3k.com/Misc/adxo.html) | DXpedition schedule scraping | None |
| [ntfy.sh](https://ntfy.sh) | Push notifications | Topic name |

## Algorithms

### LZW decode for Blitzortung WebSocket stream
- **Origin:** Algorithm is the canonical client-side LZW variant used by
  `map.blitzortung.org` browser code (publicly observable).
- **Implementation:** `backend/ft8_appliance/integrations/blitzortung_ws.py`
  (`lzw_decode()`)
- **License:** Our Python port is part of this project (MIT).

## Tooling

- **FastAPI**, **Pydantic**, **SQLAlchemy**, **httpx**, **websockets**,
  **uvicorn**, **structlog** — Python ecosystem, each MIT/BSD
- **Svelte 5**, **Vite**, **Leaflet** — Frontend, MIT
- **Hamlib** — CAT control, LGPL (linked dynamically via rigctld)

## Special Thanks

- **Joe Taylor (K1JT)** and the WSJT-X team for inventing FT8 and publishing
  the protocol openly.
- **Michael Wells (G7VJR)** for Club Log and the realtime upload API.
- **Philip Gladstone** for PSK Reporter.

73 de Sebastian DO3XR & Raymond DK9XR
