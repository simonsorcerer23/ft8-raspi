# Architecture — Hochgericht FT8 Appliance

**🇬🇧 English** · [🇩🇪 Deutsch](architecture.de.md)

**Version:** 3.0
**Status:** In field use — two Pis (`ft8`, `ft8-2`) in production
**Operators:** DK9XR + DO3XR (multi-operator)
**Rigs:** Icom IC-705 / IC-7300

> Note: this document started life as a planning paper. It is continuously
> reconciled with the actual state; the full release history lives in
> [CHANGELOG.md](./CHANGELOG.md).

---

## 1. Mission

A "fire-and-forget" FT8 appliance on a Raspberry Pi 5 that plugs into the
IC-705 / IC-7300 and is operated over Wi-Fi from an Android phone browser.
Deliberately **no** WSJT-X/WSJT-Z. Only the functions actually needed — but
those stable, foolproof, and with genuine convenience features for portable
holiday operation.

Design principles:
- **Minimalism:** no GUI workaround via Xvfb, no subprocess zoo.
- **Multi-operator:** several profiles/callsigns (e.g. father + son), hot-switch at runtime, each with its own logs/credentials/licence class.
- **Field-capable:** runs entirely without internet (with reduced features).
- **Recovery first:** any anomaly is reported immediately, never silently swallowed — and a completed QSO is never lost (spill + backup).
- **Access protected:** the entire API is password-/token-secured (localhost trusted).

---

## 2. Hardware stack

| Component | Model |
|---|---|
| Computer | Raspberry Pi 5 (16 GB RAM) |
| Case | Argon ONE V3 M.2 NVMe |
| Storage | 1 TB Samsung 990 EVO Plus NVMe |
| Time/location source | u-blox VK-162 USB GPS |
| Rig | Icom IC-705 (USB: CAT + audio) |
| Power | USB-PD power bank (5 V / 5 A) |
| Network | onboard Wi-Fi (BCM43455), no second chip |

**RF-hygiene rule:** the IC-705 **must** be plugged into a **USB 2.0 port (black)** on the Pi 5. USB 3.0 signalling (5 Gbps) emits broadband RF garbage from ~500 MHz upwards (see the Intel whitepaper "USB 3.0 Radio Frequency Interference Impact on 2.4 GHz Wireless Devices"). With the cable close to the IC-705's RF front end this noticeably raises the noise floor on 2 m / 70 cm and can also disturb HF bands. USB 2.0 (480 Mbps) has more than enough bandwidth for audio (~768 kbps) + CAT (~38 kbps).

---

## 3. Connectivity

### 3.1 Wi-Fi roaming

NetworkManager with a prioritised profile list:

```
1. Home Wi-Fi
2. Sebastian's Android hotspot
3. Dad's Android hotspot
4..N. Networks added manually via the web UI (camping, hotel, etc.)

→ none available after 60s → AP fallback
```

### 3.2 AP fallback

- SSID: `ft8-hochgericht`
- WPA2-PSK, password in `config.yaml`
- Own captive portal: `hostapd` + `dnsmasq` + nftables DNAT to the local web server
- Android opens the UI automatically when joining the Wi-Fi

**Android connectivity-check handling (mandatory):**
Modern Android (and iOS) automatically tests for internet after joining a
Wi-Fi. If the DNS/HTTP stack answers wrongly, the phone shows "Connected,
no internet" and **may drop off the Wi-Fi** as soon as LTE/cellular becomes
available again. In AP fallback this is prevented by:

| Probe URL | Response from the Pi |
|---|---|
| `connectivitycheck.gstatic.com/generate_204` | HTTP 204 No Content |
| `www.google.com/generate_204` | HTTP 204 No Content |
| `clients3.google.com/generate_204` | HTTP 204 No Content |
| `connectivity-check.ubuntu.com` | HTTP 204 No Content |
| all other HTTP requests | 302 redirect → `http://ft8.local/` |

`dnsmasq` resolves *all* DNS queries to the Pi's IP, and the local web
server has dedicated 204 handlers for the probe paths.

### 3.3 Third-party captive portals

These are **not** proxied through the Pi. Workaround: Dad's Android connects
to the foreign Wi-Fi (hotel etc.), authenticates at the portal itself, then
shares the result onward to the Pi via its hotspot. This reduces the problem
to the normal case "Pi connects to an Android hotspot".

### 3.4 mDNS

`avahi-daemon` exposes the Pi as `ft8.local`.

### 3.5 Time source (essential)

```
GPS satellites → VK-162 → gpsd → chrony → system clock
                                  ↑
                                  └─ NTP only as backup when internet is present
```

GPS time is extremely accurate at ±100 ns. FT8 requires < 500 ms. Time is
therefore guaranteed **independently of the internet** as long as GPS has
sky view.

---

## 4. Software stack

### 4.1 Layer overview

```
┌─────────────────────────────────────────────────────────────┐
│  Browser (Android Chrome, installed as PWA)                 │
│  Svelte 5 SPA: decodes, map, ADIF log, config, status       │
└──────────────┬──────────────────────────────────────────────┘
               │  HTTP/JSON + Server-Sent Events
┌──────────────▼──────────────────────────────────────────────┐
│  Controller (Python 3.12, FastAPI, uvicorn)                 │
│                                                             │
│   ┌──────────────┐  ┌─────────────┐  ┌──────────────────┐   │
│   │ State Machine│  │ Audio Loop  │  │ Web/SSE Handler  │   │
│   └──────────────┘  └─────────────┘  └──────────────────┘   │
│   ┌──────────────┐  ┌─────────────┐  ┌──────────────────┐   │
│   │ Config       │  │ Integrations│  │ Watchdog/Health  │   │
│   └──────────────┘  └─────────────┘  └──────────────────┘   │
└─┬────────┬───────────────┬───────────────┬──────────────────┘
  │        │               │               │
  │ TCP    │ TCP           │ FFI (cffi)    │ ALSA
  │ 4532   │ 2947          │               │
  ▼        ▼               ▼               ▼
rigctld   gpsd          ft8_lib       IC-705 USB audio
(Hamlib)  (GPS)         (decode/enc)
```

### 4.2 Decoder/encoder: `ft8_lib`

- Karlis Goba (YL3JG), MIT licence, ~2000 LoC of C
- Decoder: LDPC + CRC, full FT8 standard
- Encoder: complete FT8 protocol stack
- Integration: `cffi` wrapper, loaded inside the Python process, no subprocess
- If weak-signal performance turns out insufficient later: `jt9` from the WSJT-X source tree can be added as a subprocess (architecture unchanged)

### 4.3 Backend: Python 3.12 + FastAPI

- **HTTP + SSE:** FastAPI on uvicorn (uvloop)
- **Audio:** `python-alsaaudio` for capture (12000 Hz mono) and playback
- **Rig control:** TCP client to `rigctld` (port 4532). Hamlib >= 4.6 for the IC-705
- **GPS:** TCP client to `gpsd` (port 2947)
- **Persistence:** `aiosqlite` + a lightweight repository pattern
- **Slot timing:** asyncio task synchronised to the system clock (xx:00, xx:15, xx:30, xx:45)

### 4.4 Frontend: Svelte 5

- Built with Vite → static files → mounted by FastAPI
- SSE (not WebSocket) for push: decodes, status, heard updates
- **Map:** Leaflet, offline tiles preinstalled on the NVMe (world @ zoom 1-5, Europe @ zoom 6-10, ~5 GB)
- **PWA manifest:** "add to home screen"
- **Languages:** German (Dad), English — toggle in the UI
- **Theme:** auto day/night based on GPS time + sun position

**Tile-serving separation (mandatory):** the offline tiles physically live in `/var/lib/ft8-appliance/tiles/` (next to `qso.sqlite`) and are served by FastAPI as its own `StaticFiles` mount under `/tiles/{z}/{x}/{y}.png` — **not** as part of the Vite build bundle. Leaflet references them via a URL template. This keeps `backend/ft8_appliance/web/static/` (the Vite build output) small (<10 MB), and tiles can be updated or regenerated independently of an app update. Important: tiles deliberately live **outside** the git workdir (`/home/sebastian/ft8-appliance/`) so that `ft8-self-update.service` (= `git checkout vX.Y.Z`) never touches them.

### 4.5 Process management

systemd units:

| Unit | Purpose |
|---|---|
| `ft8-controller.service` | Python app |
| `rigctld.service` | Hamlib daemon |
| `gpsd.service` | GPS daemon |
| `chrony.service` | time |
| `hostapd@ap0.service` | AP fallback (on trigger) |
| `NetworkManager.service` | Wi-Fi roaming |
| `avahi-daemon.service` | mDNS |
| `ft8-self-update.timer/.service` | pulls tagged releases from GitHub every 10 min, health check + auto rollback |

### 4.6 Access & security (API auth)

The appliance controls a real transmitter and holds credentials — so the
HTTP API must not be open. Enforced via ASGI middleware (`web/auth.py`):

- **localhost (127.0.0.1/::1) is trusted** — anyone on the Pi has full access via SSH anyway, and the self-update health probe runs over localhost.
- **Static SPA / assets / tiles / captive probes** are open (login must load, the AP captive portal must work).
- **Everything under `/api` and `/sse`** needs the **master token** (`api_token`) via `Authorization: Bearer` OR `?token=` (query form for SSE, since `EventSource` cannot set headers). The master token can be set as a **memorable login password** (`POST /api/auth/token`).
- **Separate, tightly scoped `ntfy_action_token`:** only for operational control toggles (stop/cq/hunt/reset-lock/set-mode/tx-power/set-freq/panic), embedded in the ntfy lock-screen buttons. A topic leak therefore grants no secret/shutdown access.
- **Secret redaction:** `GET /api/config` returns no plaintext secrets; `config.yaml` is `0600`.
- **No network binding restriction** (binds `0.0.0.0`), because the AP fallback needs reachability on all interfaces — the token protects everywhere equally.

---

## 5. State machine (QSO flow)

```
                          ┌──────┐
                  ┌──────►│ IDLE │◄───────────┐
                  │       └───┬──┘            │
                  │           │               │
            [BTN: Stop]   [BTN: CQ]       [QSO complete]
                  │           │               │
                  │           ▼               │
                  │    ┌─────────────┐        │
                  │    │ CQ_CALLING  │        │
                  │    │ TX every 30s│        │
                  │    └─────┬───────┘        │
                  │          │                │
                  │ [someone answers me]      │
                  │          │                │
                  │          ▼                │
                  │   ┌──────────────┐        │
                  ├───┤ QSO_RESPOND  │        │
                  │   │ TX: "<them>  │        │
                  │   │  <me> <grid>"│        │
                  │   └──────┬───────┘        │
                  │          │                │
                  │   [got signal report]     │
                  │          │                │
                  │          ▼                │
                  │   ┌──────────────┐        │
                  ├───┤ QSO_REPORT   │        │
                  │   │ TX: "<them>  │        │
                  │   │  <me> R-NN"  │        │
                  │   └──────┬───────┘        │
                  │          │                │
                  │       [got RR73]          │
                  │          │                │
                  │          ▼                │
                  │   ┌──────────────┐        │
                  │   │ QSO_LOG      │        │
                  │   │ ADIF + DB    │        │
                  │   │ PSK Reporter │        │
                  │   └──────┬───────┘        │
                  │          │                │
                  │          ▼                │
                  │   ┌──────────────┐        │
                  └───┤ QSO_GRACE    │────────┘
                      │ wait 1 slot  │
                      │ maybe Tx6=73 │
                      └──────────────┘
```

**QSO_GRACE mechanics (Sebastian 2026-05-24, audit finding 2):** after
RR73 + LOG_QSO we sit in QSO_GRACE for one slot and listen whether the
partner repeats their RR73 (= our RR73 was not decoded). If so: we send a
73 (Tx6) afterwards as a closure confirmation — analogous to WSJT-X.
Otherwise, after one slot, on to CQ_CALLING (auto_cq=True) or IDLE.

**Before every TX transition the state machine checks:**
- Time guard: GPS sync OK, DT < 0.5 s
- PTT watchdog: max. 18 s per transmission
- ALC: level in the green zone
- SWR: < configured threshold (default 2.0)
- Battery: IC-705 internal voltage > 12 V (if on battery)
- Band lockout: the active antenna covers the band
- IARU band-plan lockout: TX frequency within the permitted FT8 segment of the GPS-determined region

On violation of any condition: TX is refused, the UI shows an alarm badge.

Additional modes:
- **Hunting:** instead of calling CQ, respond to other stations' CQs (button selects which)
- **Run vs. S&P:** "Run" stays on frequency, "S&P" answers + moves on
- **Panic stop:** immediate PTT off + state → IDLE, TX lock until button reset

---

## 6. Feature set

> UI impression (demo mode, fictional data): decode list, daily statistics,
> world map with coverage, and the filtered logbook.
>
> ![Radio view](docs/screenshots/funk.png)
> ![World map](docs/screenshots/map.png)

### 6.1 Core (MVP)

- FT8 decode across the full 3 kHz passband
- **FT4 decode + encode** in parallel (7.5 s slot, 4-FSK, 105 symbols) —
  mode switch in `OperatingConfig.mode`, separate shim functions
  `ft4_shim_decode_slot` / `ft4_shim_synth_message`, `SlotClock(slot_seconds=…)`
- FT8 encode + TX via CAT PTT
- Auto-CQ
- Auto-reply to callers (full QSO sequence)
- Hunting mode (answer other stations' CQs)
- Slot-synchronous TX/RX timing from GPS time
- Watchdog family:
  - PTT max time (hard limit per transmission)
  - **CQ-idle watchdog** (`cq_idle_timeout_min`, default 10 min): if
    CQ has run for X minutes without a pickup → ntfy push with action
    buttons ("STOP CQ" / "Switch to hunting"). The Pi **never** shuts
    itself off — Sebastian's explicit rule 2026-05-24
  - **Mode watchdog** (`mode_watchdog_min`, default 15 min): no more
    decodes → radio-silence push (check antenna/audio)
  - **Boot-mode-mismatch watchdog:** `boot_mode != "off"` but no auto
    mode active → push "Pi is idle"
  - **Tamper detection** (Sebastian 2026-05-24): external changes at the
    rig (power, mode, filter, frequency) trigger an ntfy push with a
    rollback action; the appliance's own CAT commands are recognised via
    an echo-window helper and not alarmed (see §6.5)
- ADIF log (live in the UI, exportable)
- Band presets (standard FT8 frequencies)
- Callsign, locator (or GPS auto), power profile
- Live decode list
- Audio-gain calibration + live monitoring + drift alarm
- TX power settable via CAT (default: max, manually selectable)

### 6.2 Smart operating

- **19-tier hunting picker** (configurable priority order,
  `OperatingConfig.hunt_priority`, drag-and-drop in the UI): lexicographic
  scoring across tiers such as `not_bad_reputation`, `not_in_pileup`,
  `tail_end_target`, `grayline`, `band_open`, `buddy_seen`, `new_dxcc(_band)`,
  `psk_heard_us`, `new_grid(_band)`, `not_worked`, `dxcc_rarity`, `snr`.
  Details: [`docs/hunt_priority.md`](docs/hunt_priority.md).
- **Hard filters** on top of the soft order: `skip_worked` (only stations
  never worked), `dxcc_only` (award mode: prefer radio silence over a non-ATNO).
- **Soft blacklist / reputation** (DB-backed, GLOBAL across operators,
  base-call normalised): stations that repeatedly bail/go silent get
  downgraded.
- **Band-aware QSO cooldown:** success cooldown per (call, band) — a long
  cooldown therefore never blocks a new band slot (5BWAS/VUCC).
- **Pick telemetry (`pick_attempt`):** logs every hunt pick with its outcome
  (completed / went-silent / bailed) plus rich context — the deciding tier
  (`winning_tier`), how loud *we* land at the DX (`psk_snr`, from PSK Reporter),
  pick age, # candidates, resends, distance, continent, mode, TX power, band
  occupancy, SNR/DT — for a data-driven A/B of the tiers
  (`/api/stats/pick-attempts`). Drove the **2026-06 retune**: `psk_heard_us`
  upranked (12.6 % completion as decider vs `snr` 6.7 %), `tail_end_target`
  pushed below `snr` (~3 % only). ~72 % of picks are "sole" (no choice), so
  tier order is inherently low-leverage; the dominant failure is the first call
  going unanswered, which tracks `psk_snr`/distance (being heard), not selection.
- TX frequency choice with collision avoidance: rotation 1200/1500/1800/2100 Hz
  per CQ transmission (`MachineContext.cq_freq_rotation`)
- Smart-CQ throttling (passive probing after N unsuccessful CQs) — parked
- Run vs. S&P mode switch
- "Only answer calls to me" filter
- Worked-B4 display
- **Tail-ender:** a direct report receive without a grid stage jumps
  CQ_CALLING → QSO_REPORT (two slots saved)
- **Auto-CQ loop** (WSJT-Z style): after LOG_QSO back to CQ_CALLING until
  the user presses Stop — controlled via `MachineContext.auto_cq`
- **ALC closed loop:** `_observe_alc_during_tx` trims the audio gain
  (0.05..1.0) into the target-window range; operating config:
  `audio_gain` / `alc_target_low` / `alc_target_high`
- **Multi-colour highlighting:** decodes annotated with `is_new_dxcc`,
  `is_new_grid`, `is_new_grid_on_band` (sets `_worked_grids` +
  `_worked_grid_band` hydrated from the DB, maintained in `_do_log_qso`)
- **WSJT-X-compliant QSO resilience** (Sebastian 2026-05-24 after the
  UN7JO / audit session):
  - **R-report resend** in QSO_REPORT: if the partner sends their report
    again instead of RR73 (= our R-report not decoded), we resend the
    R-report once before giving up. Config:
    `qso_max_report_resends` (default 1, range 0..3)
  - **Tx6/73 closure ack** via the QSO_GRACE state: a 1-slot window after
    RR73 + LOG_QSO; if the partner repeats their RR73 → send 73 afterwards
    as a final confirmation
  - **Grid resend** in QSO_RESPOND: if the partner ignores us and keeps
    calling CQ, we resend the grid up to `qso_max_cq_resends`
    times, then bail + failed cooldown
  - Ongoing WSJT-X correctness audit of the state machine:
    see [`docs/wsjtx_qso_state_audit.md`](./docs/wsjtx_qso_state_audit.md)
    + memory `feedback_wsjtx_korrektheit.md`. **Note:** this is **not**
    a feature-parity sweep (that is separately scoped and capped via
    `project_ft8_wsjtx_tier.md`)

### 6.3 Antenna protection

- Active antenna profiles (e.g. "Endfed 20m", "Doublet 80/40/20")
- TX lockout for bands without a matching antenna
- SWR-curve logger per band

### 6.4 Portable operation & CEPT

- Auto QTH/locator via GPS (Maidenhead, 6 characters)
- **GPS country detection via point-in-polygon** (`integrations/cept.py` +
  `data/cept_borders.json`, simplified Natural Earth borders): bbox as a
  pre-filter, then real polygons for disambiguation (cleanly resolves
  Balkan/Adriatic overlaps such as Croatia's C-shape around Bosnia, without
  ordering hacks).
- **CEPT compliance:** for German **Class A (T/R 61-01)** and
  **Class E (ECC/REC (05)06)** it knows where one may operate
  **without a guest licence** for short-term operation — the primary source
  is the DARC country list. Suspended countries (Belarus, Russia) are
  blocked. Suggests the correct CEPT prefix (`<area>/DK9XR`); no auto-switch,
  the operator confirms.
- IARU band-plan lockout per region (R1/R2/R3)

### 6.5 Monitoring & safety

- SWR alarm + TX stop on overshoot
- IC-705 battery monitor (Vbus via CAT)
- Pi CPU temperature (alarm > 75 °C)
- Storage watcher (SSD wear, disk space)
- Multi-level watchdog: in-process heartbeat + systemd watchdog + optional GPIO HW watchdog
- PTT-stuck detection (PTT on but no audio → immediate off)
- Time guard (no TX without GPS sync)
- **Blitzortung.org** live data, alarm for thunderstorms inside a configurable
  radius (default 30 km, `alarm_radius_km`; hot-reloadable since 2026-06)

**Data safety (audit 2026-05-30):** the irreplaceable log data is protected several ways over:
- **Atomic config writes** (`util/atomicfile.py`): tmp + `fsync(file)` + `fsync(dir)` + rename, `.bak` snapshot, `0600`, process-wide write lock against concurrent writers. `PUT /api/config` no longer flattens operators/secrets (`preserve_secrets`: "empty = keep").
- **SQLite robust:** WAL + `synchronous=NORMAL` + `busy_timeout=30s` (eliminates "database is locked" between QSO insert and upload drains).
- **QSO spill:** if the DB write of a completed QSO fails (full SD/lock/corruption) → backup to `unlogged_qsos.jsonl` + ntfy alarm, automatic retry on the next success/start. **No silent QSO loss.**
- **Daily DB backup** (`VACUUM INTO`, rotation 7) + **telemetry retention** (decode/pick_attempt/heard/swr/psk to 90 days; `qso` never).

**Tamper detection (Sebastian 2026-05-24):** if someone turns settings on
the rig's front panel (a common scenario: "Dad fiddles in secret") the Pi
should ping rather than blindly follow. Mechanics:

1. **Echo window:** every app-initiated CAT change is registered in
   `_recent_app_commands[key] = (target_value, monotonic_ts)`
   (helper `Orchestrator._register_app_command`, TTL 3 s).
2. **Rig poll sync** (every 1 s): if the rig value ≠ our internal state
   → echo check via `_is_app_echo(key, rig_value, tolerance=...)`:
   - match inside the window → our own command, silent sync
   - mismatch OR window expired → **external change** →
     `asyncio.create_task(self._notify_xxx_tamper(...))`
3. **Throttle per setting:** only 1 push per new value. If someone turns
   from 50W → 30W → 5W, two pushes arrive (for 30 and 5); if it stays at
   30W, no further one.
4. **Boot gate:** the `_tamper_armed` flag is only set after the first
   complete sync — on a service restart we do not know what was turned
   beforehand, hence a silent initial sync.

Monitored settings: TX power (`rfpower_norm`), mode (`mode`),
filter width (`bandwidth_hz`, only < 2000 Hz or > 6000 Hz alarms because
the IC-7300's FIL1/2/3 slots are regularly 2700/3600), frequency (its own
path via the frequency-drift watchdog with 100 Hz tolerance, rollback
action "Back to XXm").

### 6.6 Integrations (online)

- **QRZ.com XML API** (callsign lookup) + **QRZ Logbook API** (auto-upload of QSOs, a separate logbook/key per on-air call via `qrz_logbooks`)
- **Club Log** auto-upload (realtime + putlogs bulk), per-operator account
- **HamQTH** as a free lookup fallback
- **cty.dat** locally as an offline fallback (DXCC prefix → country/continent)
- **PSK Reporter:** upload of own decodes + download of "who heard me?" (feeds the `psk_heard_us` reciprocity tier)
- **hamqsl.com:** solar indices (SFI, A/K, MUF), a small widget
- **NG3K ADXO:** auto-import of the DXpedition calendar → watchlist + 24h ntfy reminder
- **DX cluster** (telnet) as an additional spot source
- **Maritime operators (DF7PM list):** ⚓ badge + MF number for active members, its own picker tier
- **Upload resilience (v0.40.0, hardened 2026-06):** QRZ/ClubLog mark a QSO done only on a *clearly hard* reject; transient errors are retried (ceiling 15 + give-up alarm). A 2026-06 incident exposed a gap: a tz-naive-vs-aware `datetime` crash in **both** drain loops backed up uploads for ~2 weeks while being swallowed as a benign per-cycle "hiccup". Fix added two safety nets: (a) `_as_utc()` coercion of DB datetimes + a `DTZ` lint gate against naive datetimes, and (b) **drain-loop failure escalation** (`_note_drain_outcome`) — N consecutive sweep failures now raise an ntfy alarm (`push.upload_stuck_*`) instead of staying silent.

**Resilience principle (applies to all online features):**
Every online integration must **degrade gracefully**:
- Aggressive timeouts (max 5 s per request, no blocking)
- Local cache with TTL; the UI shows the cache age ("data from 12 min ago")
- On failure: an `offline` badge on the respective UI component, no error popup, no error cascade
- Core functionality (decode, TX, QSO sequence, log) is **never** dependent on online services
- The failure of one integration must not affect another (circuit breaker per service)

### 6.7 UI / maps

- Live map: worked stations (one colour) + currently heard ones (another colour)
- Heard heatmap history (last 24 h)
- Offline tiles
- Searchable/filterable ADIF table
- Status badges: GPS fix, time sync, rig connection, Wi-Fi status, SWR, ALC, battery, temperature
- Panic-stop button (large, red, always visible)
- QSO-skip button (during a running sequence)
- Callsign blacklist
- Best-time-predictor widget (based on PSK Reporter history)

### 6.8 Push & remote (out)

- **ntfy.sh** push notifications: new DXCC, new region, QSO complete, critical alarms
- Sound alerts in the web UI (Browser Notification API)

### 6.9 Config quality

- All settings via the web (no SSH fiddling in the field)
- Hot-reload on config changes
- Config versioning with rollback option
- First-boot setup wizard

### 6.10 Internationalisation (i18n) — fully bilingual DE/EN

The UI **and** backend-generated strings are bilingual with a live header
toggle (default German, the operators are German hams):

- **Frontend catalog:** `frontend/src/lib/i18n.svelte.js` (`lang` store + `t()`),
  strings in `lib/locales/{de,en}.js`. Reactive — toggling re-renders everything.
- **Backend catalog:** `backend/ft8_appliance/i18n.py` (`_DE`/`_EN` + `translate(key, lang, **params)`)
  for backend-origin text: guard/lock reasons (`guard.*`/`lock.*`), status hints
  (`hint.*`), ntfy push bodies (`push.*`). No overlap with the frontend keys.
- **Browser strings** (status/SSE/control): the state machine stores a code +
  params; translation happens at serve time via `?lang=` (`web/deps.ui_lang`),
  which the frontend appends to every request + both SSE URLs.
- **ntfy push bodies** (no request — they go to the phone): translated at
  generation time using the configured default (`config.ui.language` →
  `i18n.set_default_lang()` at startup).
- **Three CI gates** (run in `release.sh`) keep it honest: DE/EN key + placeholder
  parity (front + back), AST call-site param coverage, and a hard-coded-German
  scanner over the `.svelte` templates. `translate()`/`t()` never crash on a
  missing key/param — they leak raw `{braces}` — hence the gates.

---

## 7. Data model

### 7.1 `config.yaml`

**Multi-operator model** (Sebastian 2026-05-23): several operator profiles
with their own logs, QRZ accounts and licence classes are possible.
Backward compat: old single-`operator:` configs are converted automatically
on load into `operators: [...]` + `active_callsign`.

```yaml
# New form (several operators)
operators:
  - callsign: DK9XR
    default_locator: JN58td      # empty = GPS auto
    default_power_w: 50
    license_class: A             # A | E | N (German AfuV)
    qrz_user: DK9XR
    qrz_password: secret
    qrz_logbook_api_key: ABCD-1234
    clublog_email: dk9xr@example.org
    clublog_app_password: "..."
    clublog_api_key: "..."
    qrz_logbooks:                # on-air call → its own QRZ logbook key
      DK9XR/AM: WXYZ-9876        # QRZ needs a logbook per prefix/suffix
    home_country: DL
    current_operating_country: null   # set during CEPT foreign operation
  - callsign: DL2XYZ
    default_locator: JO31
    default_power_w: 100
    license_class: E

active_callsign: DK9XR           # current operator (hot-switch via API)
operator_auto_login_seconds: 30  # auto-default after service start
api_token: "..."                 # API login (settable as password); 0600, redacted from GET
ntfy_action_token: "..."         # tight token only for ntfy control buttons
```

> `operating:` has further fields beyond the ones shown below, among them
> `mode` (FT8/FT4), `hunt_priority` (19-tier order), `hunt_skip_worked`,
> `hunt_dxcc_only`, `qso_cooldown_min`, `psk_reciprocity_enabled`,
> `tail_end_hunter_enabled`, `boot_mode`, `mode_watchdog_min`.

Old form (still accepted, transparent migration):
```yaml
operator:
  callsign: DK9XR
  default_locator: JN58td
  default_power_w: 10
```

bands:
  - name: "20m"
    freq_khz: 14074
    antenna: endfed_2040
  - name: "40m"
    freq_khz: 7074
    antenna: endfed_2040

antennas:
  - name: endfed_2040
    bands: ["20m", "40m"]
  - name: doublet_80_40_20
    bands: ["80m", "40m", "20m"]

operating:
  auto_cq_interval_s: 30
  max_ptt_s: 18
  cq_idle_timeout_min: 10
  swr_max: 2.0
  alc_max: 0                     # 0 = never compression-capable

network:
  wifi_priority:
    - { ssid: "Home", psk: "..." }
    - { ssid: "Seb-iPhone", psk: "..." }
    - { ssid: "Dad-Android", psk: "..." }
  ap_fallback:
    ssid: "ft8-hochgericht"
    psk: "..."

integrations:
  qrz:
    enabled: true
    user: "dk9xr"
    password: "..."
  hamqth:
    enabled: true
  psk_reporter:
    enabled: true
    upload_decodes: true

ui:
  language: de
  theme: auto
```

### 7.2 SQLite schema (sketch)

> Sketch — the real state is maintained via additive migrations in
> `db/session.py` (only `ALTER TABLE ADD COLUMN`, idempotent).
> In addition to the tables shown below, there are also, among others:
> **`pick_attempt`** (picker A/B telemetry), **`call_reputation`** (soft
> blacklist, global), **`watchlist`**, **`dxpedition_schedule`**,
> **`freq_reputation`** (smart CQ). WAL is active. Several tables carry
> `user_callsign` (multi-operator).

```sql
CREATE TABLE qso (
  id          INTEGER PRIMARY KEY,
  call        TEXT NOT NULL,
  band        TEXT NOT NULL,
  freq_hz     INTEGER NOT NULL,
  mode        TEXT NOT NULL DEFAULT 'FT8',
  rst_sent    INTEGER,
  rst_rcvd    INTEGER,
  grid_rcvd   TEXT,
  qso_start   TIMESTAMP NOT NULL,
  qso_end     TIMESTAMP NOT NULL,
  my_grid     TEXT NOT NULL,
  my_power_w  INTEGER,
  swr_avg     REAL,
  notes       TEXT,
  -- multi-operator + DX + upload tracking (later migrations):
  user_callsign    TEXT,   -- which operator (home call)
  station_callsign TEXT,   -- the call actually transmitted (e.g. 9A/DK9XR)
  mf_mfnr          INTEGER,-- maritime-operator number (snapshot)
  qrz_uploaded     BOOLEAN DEFAULT 0,
  qrz_upload_attempts INTEGER DEFAULT 0,
  qrz_last_attempt_at TIMESTAMP,
  clublog_uploaded BOOLEAN DEFAULT 0,
  clublog_upload_attempts INTEGER DEFAULT 0,
  clublog_last_attempt_at TIMESTAMP
);

CREATE TABLE decode (
  id          INTEGER PRIMARY KEY,
  ts          TIMESTAMP NOT NULL,
  call_from   TEXT,
  call_to     TEXT,
  grid        TEXT,
  message     TEXT NOT NULL,
  snr_db      INTEGER,
  dt_s        REAL,
  freq_offset_hz INTEGER,
  band        TEXT
);
CREATE INDEX idx_decode_ts ON decode(ts);

CREATE TABLE heard (
  call        TEXT PRIMARY KEY,
  last_seen   TIMESTAMP NOT NULL,
  count       INTEGER DEFAULT 1,
  grid        TEXT,
  best_snr    INTEGER
);

CREATE TABLE psk_reporter_in (
  ts          TIMESTAMP NOT NULL,
  rx_call     TEXT NOT NULL,
  rx_grid     TEXT,
  snr_db      INTEGER,
  band        TEXT,
  PRIMARY KEY (ts, rx_call)
);

CREATE TABLE swr_log (
  ts          TIMESTAMP NOT NULL,
  band        TEXT NOT NULL,
  freq_hz     INTEGER NOT NULL,
  swr         REAL NOT NULL
);

CREATE TABLE blacklist (
  call        TEXT PRIMARY KEY,
  added       TIMESTAMP NOT NULL,
  reason      TEXT
);

CREATE TABLE config_history (
  id          INTEGER PRIMARY KEY,
  ts          TIMESTAMP NOT NULL,
  yaml_snapshot TEXT NOT NULL
);
```

---

## 8. Data flow (hot loop)

```
t=0s   ┌─ ALSA capture runs continuously (ring buffer)
       │
       │  Slot start (UTC xx:00/15/30/45 ± 0.5s verified by the time guard)
       ▼
t=0s   Slot begins, RX mode
       Audio samples are buffered
       ...
t=13.5s Slot almost full, decode task starts (parallel to the last audio)
       ft8_lib decodes → list of decodes with call/grid/SNR/DT
       ▼
t=14.0s Decodes into SQLite, to the state machine, to the frontend SSE
       State machine decides: what to do in the next slot?
       ▼
t=14.5s If TX decided:
         - pre-flight checks (all guards)
         - rigctld set_freq, set_mode
         - ft8_lib encode message → 12000 Hz PCM
         - rigctld set_ptt 1
       ▼
t=15.0s New slot, ALSA playback of the encoded symbols
       PTT watchdog timer runs
       ▼
t=27.5s TX done, PTT off, RX active again
       Audio capture keeps running anyway
       ▼
t=30.0s Next slot begins, the loop closes
```

### 8.1 Audio slot synchronisation (anti-drift)

**Problem:** the IC-705 USB audio has its own crystal (~±50 ppm). The Pi system clock comes from GPS (±100 ns). Over several slots the two drift apart. If you computed slot position from sample count alone, the drift would accumulate up to a DT violation.

**Solution (phase-locking, not resampling):**

1. **GPS time is the master clock.** Slot boundaries are determined solely from the `chrony` system time, never from ALSA sample positions.
2. **Anchor at slot start:** at slot start (`t = xx:00.000` UTC ± 1 ms from chrony) the current ALSA capture position is noted as an anchor sample (`anchor_frame`) via `snd_pcm_status_get_avail`.
3. **Hard cut after 15 s:** exactly `12000 × 15 = 180000` samples from `anchor_frame` onward are cut out of the ring buffer and handed to `ft8_lib`.
4. **Recalibrated per slot:** on the next slot, `anchor_frame` is set anew — drift does not accumulate across slot boundaries.
5. **TX path analogous:** ALSA playback is started when `chrony.now() == next_slot_start`, not when the encoder is done (the encoder must be finished beforehand, otherwise a late-TX penalty).
6. **Drift monitoring:** the difference `expected_samples_per_slot` vs. `actual_samples_per_slot` is logged. At > 5 samples (0.4 ms) diff → warning in the UI; at > 50 samples → audio-calibration alarm (ALSA card or crystal defective).
7. **Only if drift > 0.5 % occurs over several slots** (which should not happen) is resampling considered as a workaround — currently out of scope.

This strategy matches the phase-lock principle from WSJT-X's own audio handling and avoids costly real-time resampling logic in the hot path.

---

## 9. Build & deploy

### 9.1 Repo structure (planned)

```
hochgericht-ft8/
├── backend/
│   ├── pyproject.toml
│   ├── ft8_appliance/
│   │   ├── main.py
│   │   ├── audio/
│   │   ├── decode/        ← cffi wrapper for ft8_lib
│   │   ├── statemachine/
│   │   ├── rig/           ← rigctld client
│   │   ├── gps/
│   │   ├── integrations/  ← QRZ, HamQTH, PSK Reporter, hamqsl, Blitzortung
│   │   ├── web/           ← FastAPI routes + SSE
│   │   ├── db/
│   │   └── config/
│   └── tests/
├── frontend/
│   ├── package.json
│   ├── vite.config.js
│   └── src/
│       ├── routes/
│       ├── lib/
│       └── components/
├── vendor/
│   └── ft8_lib/           ← as a git submodule
├── deploy/
│   ├── systemd/
│   ├── networkmanager/
│   ├── chrony/
│   └── install.sh
├── data/
│   └── cty.dat            ← offline DXCC database
├── tiles/                 ← offline map tiles (gitignored, generated)
├── architecture.md        ← this document
└── README.md
```

### 9.2 Bring-up phases

> **Status:** all phases completed — the appliance is in productive field
> use on two Pis (`ft8`, `ft8-2`). The following list is the original
> bring-up order (historical).

1. **Phase 0:** Pi OS Lite install, SSH, NVMe boot, systemd basics
2. **Phase 1:** hardware verify: ALSA finds the IC-705, rigctld talks to the rig, gpsd delivers a fix
3. **Phase 2:** decoder spike — apply ft8_lib to stored WAV files
4. **Phase 3:** live decode loop in Python, decodes to stdout
5. **Phase 4:** encoder + TX path, PTT watchdog hard-tested
6. **Phase 5:** state machine + logging
7. **Phase 6:** web backend + minimal Svelte UI (decode list + buttons)
8. **Phase 7:** map + ADIF table + status badges
9. **Phase 8:** online integrations (QRZ, PSK Reporter, hamqsl)
10. **Phase 9:** antenna profiles, band lockouts, IARU logic
11. **Phase 10:** AP fallback + captive portal + Wi-Fi roaming
12. **Phase 11:** push (ntfy), sounds, theming, PWA polish
13. **Phase 12:** field test

---

## 10. Decisions journal

| Decision | Chosen | Rejected | Rationale |
|---|---|---|---|
| Decoder | ft8_lib | jt9, WSJT-X/Z headless | MIT, small, no Fortran, no Xvfb, no subprocess |
| Backend | Python 3.12 + FastAPI | Go, Rust | audio/GPS/ham ecosystem, dev speed |
| Frontend | Svelte 5 (static) | Vanilla JS, Vue, React, HTMX, SvelteKit | smallest bundles, scales with features, no Node server |
| Push protocol | SSE | WebSocket | one-way is enough, more robust, simpler |
| Persistence | YAML (config) + SQLite (data) | pure SQLite, pure YAML | YAML human-editable, SQLite for structured data |
| Multi-user | multi-operator profiles in YAML | DB table | YAML consistent with bands/antennas, diff-friendly, hot-switch via /api/operators/select |
| Wi-Fi topology | single chip + AP fallback | dual-chip travel router | user decision |
| Captive portal, foreign Wi-Fi | not proxied | travel-router mode | phone hotspot solves it transparently |
| Time source | GPS (chrony) | NTP alone | internet independence |
| PTT method | CAT (Hamlib) | RTS/DTR, VOX | deterministic, watchdog-capable |
| Audio capture | ALSA directly | PulseAudio, PipeWire | latency, fewer dependencies |
| USB port for IC-705 | USB 2.0 (black) | USB 3.0 (blue) | USB 3.0 emits RF garbage → RX noise floor |
| Audio slot sync | phase-lock to GPS time, hard cut per slot | sample count, resampling | drift does not accumulate, no real-time DSP needed |
| Captive-portal connectivity check | 204 answers for Google/Ubuntu probes | do nothing | otherwise Android drops off the Pi Wi-Fi |
| Online-integration resilience | cache + graceful degrade + circuit breaker | hard dependencies | field-capability without internet |
| Decoder mode mix | standard / deep / multi / extreme (default extreme since v0.7.1) | only 1 fixed mode | the Pi 5 has CPU headroom, subtract+hint+notch yield ~5-6% more decodes; CPU-adaptive fallback on overload |
| Decoder subtract path | real subtract-and-rerun (synth → in-place subtract → re-decode) | only pass1+pass2 merge | JTDX-style: masked weaker signals become visible |
| Hint-pass validation | decoded text must contain a known call | AP decoding | false-positive filter equivalent to JTDX type 2; avoids AP phantoms |
| Auto-notch path | FFT spectral notch per slot, numpy-only | scipy biquad cascade | avoids the 150 MB scipy dep on the Pi |
| Pass-stats tracking | per-pass counts in `/api/status.decoder_pass_stats` | only a total counter | data-driven insight into which pass adds real value |
| DT-offset correction | auto-calibration via rolling median | only a diagnostic push | self-correcting for systematic audio-buffer offsets |
| PSK Reporter | upload from the decode path active (`upload_decode()` from the slot handler) | client implemented but untouched | reciprocal community value without PII |

> **Detailed docs for all decoder releases v0.5.2 – v0.8.0:**
> see [`docs/decoder_evolution.md`](docs/decoder_evolution.md)

---

## 11. Out of scope (deliberately dropped)

- Waterfall display
- JT9/JT65/MSK144 or other modes (FT4 *is* in scope, see §6.x)
- DXpedition hound mode
- Manual message templates
- WSJT-X/Z tier 2 remnants (points 1/2/3/4/6) — explicitly rejected 2026-05-15
- WSJT-X/Z tier 3 entirely — explicitly rejected 2026-05-15
- ~~Multi-user profiles (for now)~~ — implemented 2026-05-23, see §7.1
- Trip mode with voice memos
- Audio recording on demand
- JSONL decode dump
- USB-stick backup
- LotW / eQSL auto-upload (~~ClubLog~~ is implemented, see §6.6)
- Easter-egg animations
- Dual-Wi-Fi-chip travel-router mode

Implemented since (were once out of scope):
- ~~Multi-user profiles~~ — implemented 2026-05-23, see §7.1
- ~~ClubLog auto-upload~~ — implemented, see §6.6
- ~~DXCC award tracking~~ — picker tiers `new_dxcc`/`new_dxcc_band` (5BWAS) + `new_grid(_band)` (VUCC), see §6.2
- ~~Remote support via Tailscale/WireGuard~~ — both Pis run over Tailscale (access token-secured)

All others retrofittable if wanted later.

---

## 12. Open questions

1. **TX-audio low-pass:** a filter on the Pi before ALSA out, or do we trust the IC-705 to limit cleanly? — TBD in field test.
2. **HW watchdog on GPIO:** the Pi 5 has one, is the effort worth it? Probably later, not in the MVP.
3. **Theme-switch logic:** sunrise/sunset calculation from lat/lon, or a simple UTC-hour scheme?
4. **Map-tile pre-download:** how much world coverage is realistic (storage vs. benefit)?
5. **Wi-Fi country code:** must match the GPS position (legally required!) — automatic switching?
6. **NetworkManager vs. plain wpa_supplicant:** both work, NM has a nicer D-Bus API for web config.
7. ~~**i18n of the backend strings**~~ — **RESOLVED 2026-06.** Everything
   (frontend UI, guard/lock reasons, status hints, ntfy push bodies) is now
   fully bilingual DE/EN with a live toggle. See §6.10. Guarded by three CI
   gates against DE/EN drift and hard-coded strings.

---

## 13. Glossary

- **FT8:** digital weak-signal mode, 15-second slots, 50 Hz bandwidth per signal
- **DT (delta time):** temporal offset of a decode relative to slot start. > 0.5 s = not decodable
- **ALC (Automatic Level Control):** rig-internal limiting, should be 0 for FT8 (otherwise splatter)
- **SWR (Standing Wave Ratio):** measure of antenna match. > 2.0 = problematic, > 3.0 = TX stop
- **Maidenhead locator:** geo-coordinate shorthand, e.g. `JN58td` (6 characters)
- **DXCC:** ARRL country list, ~340 "entities" worldwide
- **CAT (Computer-Aided Transceiver):** serial control of the rig
- **PTT (Push to Talk):** transmit switch
- **CQ:** general call "who can hear me?"
- **RR73:** "Roger, 73" — QSO confirmation at the end
- **PSK Reporter:** crowd-sourced reception reports, https://pskreporter.info
