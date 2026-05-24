# Implementation Status

Diese Datei dokumentiert was tatsächlich gebaut wurde — ergänzend zur
`architecture.md` die Soll-Zustand und Design-Entscheidungen festhält.

**Letztes Update:** 2026-05-15 nach Bonus + Tranche-6 + Live-Sweep

---

## 1. Code-Struktur

```
ft8/
├── architecture.md               Soll-Architektur (V2)
├── IMPLEMENTATION_STATUS.md      ← du bist hier (Ist-Stand)
├── README.md                     Quick-Start
│
├── backend/                                                     [Python 3.12]
│   ├── pyproject.toml
│   ├── ft8_appliance/
│   │   ├── main.py                Entrypoint (uvicorn-Skelett)
│   │   ├── config/                AppConfig (Pydantic v2 strict, YAML)
│   │   │   ├── models.py          Operator/Bands/Antennas/Operating/
│   │   │   │                       Network/Integrations/UI
│   │   │   └── loader.py          load_config/get_config/hot-reload
│   │   ├── audio/
│   │   │   ├── slot_sync.py       Phase-Lock-Anchor 180000-Sample-Cut
│   │   │   └── alsa_io.py         AlsaCapture + AlsaPlayback (Pi-only)
│   │   ├── decode/
│   │   │   ├── ft8_shim.c         eigene C-Wrapper (decode + tx-synth)
│   │   │   ├── _build_ft8.py      cffi build script
│   │   │   ├── ft8_native.py      Python facade (decode_slot, synth_message)
│   │   │   └── pipeline.py        DecodePipeline + parse_message
│   │   ├── encode/                (reserviert)
│   │   ├── statemachine/
│   │   │   ├── states.py          State enum, DecodedMsg, QsoContext,
│   │   │   │                       MachineContext (+ blacklist + auto_answer
│   │   │   │                       + skip_worked)
│   │   │   ├── guards.py          HardwareState, GuardLimits, 7 guards
│   │   │   │                       (time/audio_drift/antenna/swr/alc/
│   │   │   │                       battery/temp)
│   │   │   └── machine.py         StateMachine (CQ/Hunting/QSO-Sequenz)
│   │   ├── rig/
│   │   │   └── rigctld_client.py  Hamlib TCP. RigSnapshot mit 20 Feldern
│   │   │                           (freq/mode/bw/ptt/swr/alc/s-meter/
│   │   │                           rfpower/af/rf/nr/preamp/att/nb/agc/
│   │   │                           vfo/split/battery/temp)
│   │   ├── gps/
│   │   │   └── gpsd_client.py     gpsd JSON-on-TCP
│   │   ├── integrations/          Online-Dienste mit Resilience-Pattern
│   │   │   ├── base.py            AsyncTTLCache + CircuitBreaker
│   │   │   ├── cty_dat.py         Offline-DXCC-Lookup (cty.dat parser)
│   │   │   ├── qrz.py             QRZ.com XML-API
│   │   │   ├── hamqth.py          HamQTH (kostenloser Fallback)
│   │   │   ├── hamqsl.py          Solar-Indizes (SFI/A/K)
│   │   │   ├── psk_reporter.py    "Wer hat mich gehört" + Upload-Stub
│   │   │   ├── ntfy.py            Push-Notifications via ntfy.sh
│   │   │   ├── blitzortung.py     Gewitter-Strike-Ring + Haversine
│   │   │   └── dx_cluster.py      DX Cluster Telnet-Client
│   │   ├── db/
│   │   │   ├── models.py          SQLAlchemy: Qso/Decode/Heard/
│   │   │   │                       psk_reporter_in/swr_log/Blacklist/
│   │   │   │                       config_history (+ my_lat/my_lon
│   │   │   │                       für Trip-Map)
│   │   │   ├── session.py         async-aiosqlite Engine
│   │   │   └── repository.py      Repository-Helpers
│   │   ├── runtime/
│   │   │   ├── orchestrator.py    Async Main-Loop. Verbindet StateMachine
│   │   │   │                       + Rig + GPS + Decode-Source + DB +
│   │   │   │                       Integrations. Hot-Config-Reload,
│   │   │   │                       PTT-Stuck-Watchdog, ntfy on QSO,
│   │   │   │                       DXCC-new-detection.
│   │   │   └── slot_clock.py      UTC-aligned Slot-Iterator (Fake für Tests)
│   │   ├── util/
│   │   │   ├── maidenhead.py      lat/lon ↔ Locator
│   │   │   ├── bandplan.py        band_from_freq_hz + IARU-Region
│   │   │   ├── system_health.py   chrony tracking + Pi-Temp/Throttle
│   │   │   ├── band_simulator.py  32-Stationen Band-Simulator
│   │   │   └── band_suggester.py  Heuristik SFI/K/Tageszeit/Aktivität
│   │   ├── watchdog/              (reserviert)
│   │   └── web/                                              [FastAPI]
│   │       ├── app.py             create_app(orchestrator)
│   │       ├── deps.py            DI: get_orchestrator
│   │       └── routes/
│   │           ├── status.py      /api/status + /api/qso/conversation
│   │           ├── healthcheck.py /api/healthcheck (Pi-Check-JSON-Twin)
│   │           ├── control.py     CQ/Stop/Panic/Reset/Reply/AutoAnswer/
│   │           │                   Skip/Blacklist/TxPower/Antenna/Band
│   │           ├── log.py         /api/log + decodes + heard + map +
│   │           │                   operating-locations + heatmap
│   │           │                   (mit Sort+Filter+Pagination)
│   │           ├── config.py      /api/config GET+PUT (Hot-Reload)
│   │           ├── integrations.py callsign/solar/psk/blitzortung/dx
│   │           ├── stats.py       Heute-Stats + Band-Vorschläge +
│   │           │                   Best-Time-Histogramm
│   │           ├── adif.py        ADIF 3.1.4 Export
│   │           ├── captive.py     OS-Captive-Probes (Android/iOS/Win)
│   │           └── sse.py         /sse/decodes + /sse/status
│   └── tests/                     127 pytest grün
│       ├── mocks/                 mock_rigctld + mock_gpsd + mock_audio
│       ├── test_decode_spike.py   ft8_lib cffi
│       ├── test_decode_pipeline.py WAV→DecodedMsg (Roundtrip)
│       ├── test_tx_synth.py       text→PCM→decode rekonstruiert Original
│       ├── test_slot_sync.py      Phase-Lock-Anchor
│       ├── test_statemachine.py   QSO-Flow + Guards + Freq-Propagation
│       ├── test_orchestrator.py   End-to-End mit Mock-Hardware
│       ├── test_rig_gps_clients.py Hamlib + gpsd-Protokoll
│       ├── test_web.py            HTTP-Endpoints
│       ├── test_chaos.py          GPS-Lost/SWR-Spike/Panic/Audio-Silent
│       ├── test_edge_cases.py     Late-Answer/exotische-Calls/Skip
│       ├── test_config.py
│       ├── test_db.py
│       ├── test_integrations.py   QRZ/HamQTH/Solar/PSK/cty.dat (respx)
│       ├── test_mocks.py
│       └── test_system_health.py  chrony Parser
│
├── frontend/                                       [Svelte 5 + Vite + Leaflet]
│   ├── package.json
│   ├── vite.config.js             Build → backend/web/static
│   ├── public/                    manifest + favicon
│   └── src/
│       ├── main.js                Svelte 5 mount
│       ├── App.svelte             Tab-Layout (Funk/Map/Log/Blacklist/Konfig)
│       │                           + First-Boot-Detection
│       ├── lib/
│       │   ├── api.js             fetch-Wrapper für alle Endpoints
│       │   ├── sse.js             EventSource-Helper
│       │   ├── sound.svelte.js    attachStatusStream + attachDecodeStream
│       │   │                       + WebAudio-Chimes + Browser-Notifications
│       │   ├── geo.js             Großkreis (Antimeridian-Split) +
│       │   │                       gridToLatLon
│       │   └── stores.svelte.js   Reaktive Stores (Status/Health/Decodes/
│       │                           Log mit Sort+Filter / Map mit Modes)
│       └── components/
│           ├── StatusBar.svelte        Op-Mode + State + Rig + GPS + Worked
│           ├── RigPanel.svelte         IC-705 Instrument-Display
│           ├── StateIndicator.svelte   (legacy, ersetzt durch StatusBar)
│           ├── StatusBadges.svelte     Healthcheck-Section-Badges
│           ├── ControlPanel.svelte     CQ + Antworten + TX-Pwr + Antenna +
│           │                            QSO-Skip + Panic
│           ├── DecodeList.svelte       Live-Decodes + Worked-B4 +
│           │                            Blacklist-Tag + Reply-Confirm
│           ├── QsoConversation.svelte  Live-Transkript + Next-Action-Hint
│           ├── StatsDashboard.svelte   Heute/7d/Best-DX + Band-Vorschläge
│           ├── Map.svelte              Leaflet + 5 Layer + Großkreise +
│           │                            sortierbare Stations-Liste
│           ├── ADIFTable.svelte        Sortable Headers + Multi-Filter +
│           │                            Präfix-Klick-Filter + ADIF-Export
│           ├── BlacklistPanel.svelte   CRUD-UI
│           ├── ConfigPanel.svelte      Form-Editor + YAML-Mode-Toggle
│           ├── SolarWidget.svelte      SFI/A/K Header-Widget
│           ├── BestTimeChart.svelte    24h-Histogramm CSS-Bars
│           └── FirstBootWizard.svelte  3-Step Setup
│
├── vendor/ft8_lib/                git-submodule, -fPIC kompiliert
├── docs/
│   └── operations.md              SSH-Workflow + Pi-Check
├── scripts/
│   ├── pi-check.sh                Shake-Down Health-Bash-Script
│   ├── dev_run.py                 Komplette App auf Workstation
│   ├── dev_e2e_test.py            Headless 7-Assertion E2E-Smoke
│   └── fetch_offline_tiles.sh     Offline-OSM-Tiles für /tiles Mount
└── deploy/
    ├── install.sh                 Pi-OS Lite Provisioning
    ├── systemd/                   3 Units (controller/rigctld/ap-fallback)
    ├── chrony/                    GPS-SHM Refclock Config
    ├── dnsmasq/                   AP-Fallback DHCP+DNS-Catch-All
    ├── hostapd/                   AP-Fallback WPA2
    ├── nftables/                  Captive-Portal Redirect
    ├── networkmanager/            (Profile via nmcli runtime)
    └── scripts/
        ├── start-ap-fallback.sh
        └── stop-ap-fallback.sh
```

---

## 2. Feature-Stand vs. architecture.md §6

| Kapitel | Feature | Backend | Frontend |
|---|---|---|---|
| **6.1 Core** | FT8-Decode | ✅ ft8_lib + cffi | ✅ DecodeList live SSE |
|  | FT8-Encode + TX | ✅ Synth | 🟡 Stub bis ALSA |
|  | Auto-CQ | ✅ | ✅ Big-Button |
|  | Auto-Reply | ✅ | ✅ |
|  | Hunting-Mode | ✅ | ✅ Big-Button (Antworten) |
|  | Slot-Sync GPS | ✅ | n/a |
|  | Watchdog | ✅ multi-level | n/a |
|  | ADIF-Log | ✅ DB | ✅ Tabelle + Export |
|  | Band-Presets | ✅ Config | ✅ Quick-Switch in Stats |
|  | Rufzeichen/Locator/Power | ✅ Hot-Reload | ✅ Form-Editor + YAML |
|  | Decode-Liste live | ✅ SSE | ✅ |
|  | Audio-Gain Kalibrierung | ✅ ALC Closed-Loop | ✅ RigPanel-Cell |
|  | TX-Power per CAT | ✅ rig.set_rfpower | ✅ Slider 1-10W |
| **6.2 Smart Operating** | TX-Freq Kollisions-Vermeidung | ✅ Rotation 1200/1500/1800/2100 | ✅ via Status |
|  | Smart-CQ-Throttling | ❌ | ❌ |
|  | Run vs S&P | ✅ exclusive Toggle | ✅ zwei Big-Buttons |
|  | "Only answer me"-Filter | ✅ Config | ✅ DecodeList-Checkbox |
|  | Worked-B4 | ✅ Set + Hint | ✅ Badge + Reply-Confirm |
|  | Tail-Ender | ✅ State Machine | ✅ via QSO-Conv |
|  | Auto-CQ-Loop (WSJT-Z) | ✅ ctx.auto_cq | ✅ via Status |
|  | New-DXCC/Grid/Grid-Band | ✅ Sets + helpers | ✅ Multi-Color Decodes |
| **6.x Mode** | FT4 (7.5s slot, 4-FSK) | ✅ shim + cffi + slot_clock | ✅ Mode-Tag |
|  | AP-Decoding (Soft-Bit-Prior) | 🟡 C-Hook stub | n/a (Sweep B) |
|  | Multi-Pass + Subtract | 🟡 C-Hook stub | n/a (Sweep B) |
| **6.3 Antennen-Schutz** | Antennen-Profile | ✅ Config | ✅ ControlPanel-Dropdown |
|  | Band-Lockout | ✅ antenna_guard | ✅ (auto-switch beim Band) |
|  | SWR-Curve | ✅ DB | ❌ Plot fehlt |
| **6.4 Portable** | Auto-QTH GPS | ✅ Maidenhead-Live | ✅ StatusBar |
|  | Auto-Präfix Ausland | 🟡 cty.dat geladen | ❌ noch nicht im SM |
|  | IARU-Bandplan-Lockout | ✅ Helper | ❌ Guard nicht aktiv |
| **6.5 Monitoring** | SWR-Alarm | ✅ Guard | ✅ RigPanel-Color |
|  | IC-705 Battery | ✅ get_battery_v | ✅ RigPanel-Tag |
|  | Pi-CPU-Temp | ✅ | ✅ Healthcheck |
|  | Storage-Watcher | ✅ | ✅ |
|  | Multi-Level-Watchdog | ✅ systemd + app | n/a |
|  | PTT-Stuck-Detection | ✅ rig-poll-loop | ✅ Lock-Reason |
|  | Time-Guard | ✅ | ✅ Badge |
|  | Blitzortung | ✅ Client + Endpoint | ❌ kein WS-Consumer |
| **6.6 Online-Integrationen** | QRZ.com | ✅ XML-API | ✅ /api/callsign |
|  | HamQTH | ✅ | ✅ |
|  | cty.dat | ✅ Boot-Load | ✅ (DXCC-new ntfy) |
|  | PSK Reporter Download | ✅ | 🟡 Map-Layer TODO |
|  | PSK Reporter Upload | ❌ IPFIX-Stub | n/a |
|  | hamqsl Solar | ✅ | ✅ Header-Widget |
| **6.7 UI/Maps** | Live-Map worked+heard | ✅ /api/map | ✅ Leaflet + Farben |
|  | Heard-Heatmap | ✅ /heard/heatmap | ✅ Layer-Toggle |
|  | Live-Großkreise zu Decodes | ✅ | ✅ Layer-Toggle, ageing |
|  | Operating-Standorte | ✅ /operating-locations | ✅ Layer-Toggle |
|  | DX-Cluster-Spots | ✅ Telnet-Client | ✅ Layer (read only) |
|  | Offline-Tiles | ✅ /tiles Mount + Script | ✅ Fallback OSM |
|  | ADIF-Tabelle | ✅ multi-filter | ✅ sortable Headers |
|  | Status-Badges | ✅ /healthcheck | ✅ |
|  | Panic-Button | ✅ | ✅ |
|  | QSO-Skip | ✅ | ✅ (in-QSO) |
|  | Callsign-Blacklist | ✅ DB-CRUD | ✅ Tab + Inline-Button |
|  | Best-Time-Predictor | ✅ /best-time/{band} | ✅ CSS-Bar-Chart |
| **6.8 Push** | ntfy.sh | ✅ on LOG_QSO + DXCC | n/a |
|  | Browser Sound-Alerts | n/a | ✅ WebAudio + Notify-API |
| **6.9 Konfig** | Web-Konfig | ✅ GET/PUT | ✅ Forms + YAML-Mode |
|  | Hot-Reload | ✅ on_config_changed | ✅ ohne Reboot |
|  | Konfig-Versionierung | ✅ DB-Schema | ❌ keine Snapshots |
|  | First-Boot-Wizard | n/a | ✅ 3-Step |

**Bonus-Features (Sebastians Liste):**
| # | Feature | Status |
|---|---|---|
| 1 | Compact Header-Statusbar | ✅ |
| 2 | Worked-B4 Reply-Warnung | ✅ Confirm-Dialog |
| 3 | "Nur an mich" Filter | ✅ Checkbox |
| 4 | Rare-DX Highlight + Push | ✅ New-DXCC-ntfy |
| 5 | Stats-Dashboard | ✅ |
| 6 | DX-Cluster | ✅ Backend, Map-Marker pending |
| 7 | ADIF-Export | ✅ |
| 9 | Best-Time-Predictor | ✅ |
| 10 | Audio-VU-Meter | ❌ (Pi-Hardware) |
| 11 | Quick-Band-Switch | ✅ in Stats |
| 12 | GPS-per-QSO + Pin | ✅ |
| 16 | Band-Vorschlag (kein Auto) | ✅ |

**Live-Demo-Daten** (in `dev_run.py`):
* 12 Demo-QSOs (Heim NBG + Italien-Urlaub) mit GPS pro QSO
* 7 Heard-Stationen weltweit
* 32-Stationen-Band-Simulator (DL/PA/G/OE/HB/IK/EA/W/JA/VK/CE/... mit
  realistischen Calls und Grids, reagiert auf User-TX)

---

## 3. Tests + Quality

| Metric | Wert |
|---|---|
| pytest grün | 145 / 1 dokumentierter skip |
| pytest Dauer | ~6 s |
| E2E-Smoke | 7 Checks alle grün (~2 s) |
| Frontend Build | 258 KB JS / 41 KB CSS (gzip: 81 KB / 11 KB) |
| Ruff lint | 13 verbleibende Issues (FastAPI Depends-Defaults False-Positive) |
| Commits auf main | 16 (Architektur → Skelett → Phasen B-J → Bonus-Sweeps → WSJT-X-Parity) |

---

## 4. Memory-Einträge (Claude Personal Persistence)

In `~/.claude/projects/-home-sebastian.../memory/`:
- `feedback_hardware_kommentare.md` — keine ungebetenen Hardware-Tipps
- `feedback_seam_audit.md` — Phasen-Übergangs-Daten-Audit
- `feedback_feature_completeness.md` — bei jedem Milestone Soll/Ist-Matrix
- `project_ft8_appliance.md` — Architektur-Constraints (Android-only, Single-WLAN)
- `project_ft8_pi_check.md` — SSH-Trigger-Phrasen "check Pi" usw.
- `project_ft8_autonomy.md` — Claude entwickelt autonom, User gibt UI-Feedback
- `project_ft8_wsjtx_tier.md` — Tier-2-Reste + Tier 3 dauerhaft verworfen; nur Sweep B offen

---

## 5. Was im Code als TODO bleibt

**Hardware-abhängig (warten auf Pi):**
- Echte ALSA-Capture + TX-Playback (`_do_tx_message` ist heute log-only)
- VU-Meter (braucht ALSA-RMS)
- Live IC-705 CAT (heute über Mock-rigctld validiert)
- gpsd ↔ VK-162 USB
- Audio-Drift-Messung gegen reale Quartz

**Nicht-Hardware, aber bewusst geparkt:**
- IPFIX-UDP-Upload zu PSK Reporter (heute Stub)
- Blitzortung Websocket-Consumer (heute nur Endpoint mit Strike-Ringbuffer-API)
- DX-Cluster Marker auf Map (Spotter→Grid-Lookup fehlt)
- Konfig-Versionierung Schreibpfad
- IARU-Bandplan-Guard aktiv (Helper da)
- Auto-Präfix bei Auslandsbetrieb in State Machine
- "The Skip" State-Machine-Edge-Case (dokumentierter pytest.skip)
- SWR-Curve-Plot pro Band

**Tranche-7 wenn Pi da ist:**
- ALSA-Bring-Up + Phase-Lock-Validierung an echtem Stream
- TX-Wiring inkl. Watchdog am echten PTT (Synth fertig, fehlt PCM-Out + PTT-Sequence)
- ALC-Closed-Loop am echten IC-705 kalibrieren (Target-Fenster justieren)
- FT4-Mode am echten Stream gegen-validieren (Round-trip-Tests sind synth-only)
- Field-Test Italien-Urlaub

**Sweep B (deferred — Hardware nicht zwingend nötig):**
- AP-Decoding echt: LDPC-Soft-Bit-Pinning. C-Hook delegiert aktuell an
  Single-Pass; braucht Patch in `vendor/ft8_lib`, der den Soft-Bit-Buffer
  zwischen `ftx_decode_candidate` und `bp_decode` exponiert.
- Multi-Pass+Subtract echt: synth-back jedes Pass-1-Decodes am
  gemessenen dt/freq → Sample-Subtract aus Original-Waveform →
  re-`ftx_find_candidates` auf Residual. Braucht Residual-Buffer-Zugriff
  oder eine Re-Implementierung der monitor-Pipeline mit Subtract-Hook.
- Beide API-Stable (`decode_slot_ap`, `decode_slot_multipass`) — Aufrufer
  können heute schon integrieren.
