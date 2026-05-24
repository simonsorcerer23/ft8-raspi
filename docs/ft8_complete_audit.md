# FT8-Funktions-Komplettaudit

**Datum:** 2026-05-24
**Auslöser:** Sebastian — nach R-Report-WSJT-X-Konformanz-Bug (Audit Action 5):
„wie konnten wir sowas Essentielles übersehen? Großes Audit aller FT8-Funktionen!"
**Scope:** alle Code-Pfade die FT8-Protokoll-Nachrichten erzeugen, parsen
oder beeinflussen. Hardware-Layer (rigctld, ALSA) und UI-Komponenten
**nicht** im Scope (eigene Audits / Issues).
**Methodik:** Code-Inventur + Spec-Vergleich (WSJT-X-Quellcode, QEX-Paper,
ADIF-3.1.4-Spec) + **Real-Data-Cross-Check** gegen 2346 Decodes aus der
letzten 2h auf ft8 (Sebastians Daily-Driver-Pi).

Companion zu `docs/wsjtx_qso_state_audit.md` (State-Machine-spezifisch).
Dieser hier deckt **alles drumherum** ab.

---

## Zusammenfassung

| # | Finding | Severity | Status |
|---|---|---|---|
| F1 | ADIF `PROGRAMVERSION` hardcoded `0.1.0` | medium | **Erledigt v0.3.3** |
| F2 | ADIF-Filename hardcoded `dk9xr_ft8.adif` (multi-op broken) | medium | **Erledigt v0.3.3** |
| F3 | ADIF fehlende Felder: OPERATOR, STATION_CALLSIGN, COUNTRY | medium | **Erledigt v0.3.3** |
| F4 | SNR-Clamp auf FT8-Spec-Range (-50..+49) fehlte | low (defensiv) | **Erledigt v0.3.3** |
| F5 | Hashed-Call-Receive für eigene compound calls | low | **Erledigt v0.3.4** |
| F6 | FT4-Mode nur teilweise verdrahtet | medium | **Erledigt v0.4.0** |
| F7 | Directed CQ (CQ DX/EU/POTA) — outgoing nicht unterstützt | low | **Erledigt v0.3.4** |
| F8 | Free-Text Tx5 wird stumm ignoriert | low | **Erledigt v0.3.4** |
| F9 | Contest-Messages (RU/FD/RTTY) im Picker nicht erkannt | low | **Erledigt v0.3.4** |
| F10 | AP-Decoding (a priori) | low | bereits ditched, verifiziert |

**Bilanz:** 4 sofort-fixe in v0.3.3, 4 weitere in v0.3.4 nach Sebastian-
Request „alles fixen". Nur F6 (FT4) offen — Recherche zeigte: die C-
Shim-Layer hat FT4 schon implementiert (`ft4_shim_decode_slot`,
`ft4_shim_synth_message`), es fehlt nur das Python-Wiring. Geplant
fuer v0.4.0, **deutlich kleiner als initial gedacht** (~1-2h statt
1-2 Personentage).

---

## Methodik im Detail

### Phase 1 — Code-Inventur
Identifiziert alle Stellen die FT8-Protokoll-spezifisch sind:
- **Tx-Message-Bau:** 6 `_emit_*`-Methoden in `statemachine/machine.py`
- **Decode-Parsing:** `parse_message()` in `decode/pipeline.py` + 4
  `_find_*`-Helper in `statemachine/machine.py`
- **Slot-Timing:** `slot_clock.py` + `audio/slot_sync.py`
- **PSK-Reporter-Upload:** `integrations/psk_reporter.py`
- **ADIF-Export:** `web/routes/adif.py`
- **CQ-Frequenz-Picker:** `statemachine/machine.py::_next_cq_freq_hz`

### Phase 2 — Spec-Vergleich
Jede Funktion gegen Spec geprüft:
- WSJT-X 2.7 Source (Tx1-Tx6, AutoSeq-Logik)
- QEX Sept/Okt 2020 (Steve Franke „FT8 — High-Performance Mode")
- ADIF 3.1.4 (`https://adif.org/314/ADIF_314.htm`)

### Phase 3 — Real-Data-Cross-Check
SQL-Queries gegen Sebastians Live-DB:
- 2346 Decodes letzte 2h: **100 % geparst** (call_from immer gesetzt)
- 1020 Closing-Tokens: RR73 (60 %), 73 (38 %), RRR (2 %)
- 0 Contest-Messages aufgetreten
- ~30 hashed-call-Decodes (`<...>`) — alle korrekt als opaque Token behandelt
- 2 eigene TX mit Partner-Compound-Call (`EK/RX3DPK DO3XR JN58`) — funktionierte

---

## Findings im Detail

### F1 — ADIF PROGRAMVERSION hardcoded *(Erledigt v0.3.3)*

**Vorher:**
```python
f"<PROGRAMVERSION:5>0.1.0 "
```

Hartcoded auf v0.1.0 — nach allen Releases (v0.2.0 … v0.3.2) noch
immer der Initial-Wert. LotW/eQSL-Uploads zeigten falsche Software-
Version, kein Hinweis welcher Bugfix wann aktiv war.

**Fix v0.3.3:** Import von `_version.__version__`, dynamische Länge
via `_adif_field("PROGRAMVERSION", progver)`.

### F2 — ADIF-Filename hardcoded multi-op-broken *(Erledigt v0.3.3)*

**Vorher:**
```python
'attachment; filename="dk9xr_ft8.adif"'
```

Beim Export aus dem DO3XR-Profil bekam der Browser trotzdem die Datei
`dk9xr_ft8.adif` angeboten — verwirrend, mischbar im Download-Ordner.

**Fix v0.3.3:** `f"{active_op.lower()}_ft8.adif"` mit dem Callsign
des aktuell aktiven Operator-Profils.

### F3 — Fehlende ADIF-Standard-Felder *(Erledigt v0.3.3)*

**Vorher fehlten:** OPERATOR, STATION_CALLSIGN, COUNTRY.

- LotW erwartet STATION_CALLSIGN für die Zertifikats-Zuordnung
- Multi-Op-Logs ohne OPERATOR-Feld lassen sich nicht sauber zu zwei
  Profilen splitten
- COUNTRY (= DXCC-Entity-Name) erleichtert manuelle DXCC-Recherche

**Fix v0.3.3:** drei Felder pro QSO-Zeile geschrieben:
- `STATION_CALLSIGN` + `OPERATOR` = `Qso.user_callsign` (per-QSO, mit
  Fallback auf aktiven Operator wenn das alte Feld leer)
- `COUNTRY` via `cty.dat`-Lookup des QSO-Calls

Backwards-compatible (alte ADIF-Reader ignorieren unbekannte Felder).

### F4 — SNR-Clamp auf FT8-Spec-Range *(Erledigt v0.3.3)*

**Spec:** FT8 SNR-Field ist 7-bit signed, encodierbar -50..+49 dB.
Werte außerhalb produzieren undefined Encoder-Verhalten (vermutlich
Truncation auf 7 bit).

**Risiko vorher:** wenn `qso.their_snr_at_us` aus einem fehlerhaften
Decode kam (z.B. -120 dB durch numerischen Bug), würden wir
`R-120` transmitten — ft8_lib-Encoder verhalten unklar, Partner-
Decoder würde garbage sehen.

**Fix v0.3.3:** zentrale `_clamp_snr()`-Helper-Methode, clamped auf
`[FT8_SNR_MIN=-50, FT8_SNR_MAX=49]`, wird in beiden Emit-Pfaden
(`_emit_respond_with_report` + `_emit_send_r_report`) genutzt.

Real-Welt-Auswirkung: gleich null (Decoder liefert nur Werte im
realistischen -30..+30-Bereich), aber **defensive Code-Disziplin**.

### F5 — Hashed-Call-Receive für eigene compound calls *(Erledigt v0.3.4)*

**Symptom (vor Fix, theoretisch):** Wenn unser Pi je einen compound
own-call verwendet (`DK9XR/P`, `DL/W1AW`), und Partner antwortet uns:
- Partner-Tx muss unser Call hashen (passt nicht in 13-Char-Frame)
- Decode bei uns: `<...> THEIR_CALL JN58` (oder R-Report)
- Alte `_find_*_to_us`-Check: `d.call_to == my_call` → False → wir
  verpassen die Antwort, QSO timeoutet

**Fix v0.3.4:** Helper `_hashed_match(field, expected)` matched sowohl
exakten Call als auch `<...>`-Placeholder. In `_find_report_from_them`
und `_find_closing` (beides Funktionen mit Kontext „wir wissen Partner-
Call"): beide Felder werden gegen `_hashed_match` geprüft. Ambiguity-
Guard: wenn beide Felder `<...>` sind, kein Match (wäre wild guess).

**Tests:**
- `test_hashed_call_receive_in_qso_report`: `<...> EK/RX3DPK -10`
  wird als Report erkannt
- `test_hashed_call_closing_recognized`: `<...> EK/RX3DPK RR73`
  schließt QSO
- `test_double_hashed_message_ambiguous_no_match`: `<...> <...>`
  triggert nichts (Sicherheit)

**Real-Welt-Beweis vor Fix:** in Sebastians Live-DB existieren
mehrere Decodes wie `<...> EK/RX3DPK R-04` und `<...> CR7BUJ R-04` —
sobald Sebastian (oder ein anderer Operator) mit compound-Call
antwortet, würde der Partner-Tx so aussehen und ohne Fix verloren
gehen.

**Nicht abgedeckt (out-of-scope):** Initial-Pickup wenn ein Compound-
Call uns ruft ohne dass wir vorher CQ ausgesandt haben — wir kennen
ihren Call nicht und können den Hash nicht resolven. Wäre ein
Hash-Table-Management-Problem (`ft8_lib` hat einen, wir nicht
gewrappt). Real-Häufigkeit: minimal. Verbleibender Risk dokumentiert.

### F6 — FT4-Mode voll unterstützt *(Erledigt v0.4.0)*

**Sebastian-Request 2026-05-24 nach v0.3.3:** "alles fixen + ft4
möglich machen!"

**Status vor v0.4.0:**
- `OperatingConfig.mode = "FT4"` Config-Feld vorhanden
- `Orchestrator.start()` swappte SlotClock auf 7.5s
- `audio/slot_sync.py` `SAMPLES_PER_SLOT` **hardcoded 180000 (15s)** ✗
- `DecodePipeline` rief immer `decode_slot` (FT8-only) ✗
- TX-Synth-Pfad wäre FT8-only ohne explizite Mode-Check ✗

**HUGE FINDING bei Code-Recherche:** der C-Shim
(`decode/ft8_shim.c`) und Python-Binding (`decode/ft8_native.py`)
hatten FT4 **vollständig implementiert** mit
`ft4_shim_decode_slot`/`ft4_shim_synth_message`/`SAMPLES_PER_SLOT_FT4=90000`/
`TX_SAMPLES_FT4=60480`. Es fehlte nur das High-Level-Wiring →
F6 wurde damit von "1-2 Personentage" zu "~1-2h".

**Fix v0.4.0 (5 Code-Pfade):**
1. `audio/slot_sync.py::SlotBuffer.extract_slot()` parametrisiert
   mit `slot_seconds`-Argument (default 15s, backward-compatible).
   `FT4_SLOT_SECONDS=7.5` + `FT4_SAMPLES_PER_SLOT=90000` als Konstanten.
2. `decode/pipeline.py::DecodePipeline` neues Feld `mode: str = "FT8"`,
   `__call__` routet basierend auf Mode zum richtigen Decoder
   (`decode_slot` oder `decode_slot_ft4`) + Slot-Window.
3. `runtime/production.py::_build_decode_source` liest
   `config.operating.mode` und reicht's an `DecodePipeline` weiter.
4. `runtime/orchestrator.py::on_config_changed` propagiert
   Hot-Mode-Switch (z.B. User toggelt im UI) auf `decode_source.mode`
   live; SlotClock-Tempo braucht für vollen Effekt Service-Restart
   (Warning im Log).
5. UI: `ConfigPanel.svelte` neuer `<select>` mit FT8/FT4-Optionen +
   `cq_directed`-Input + YAML-Serialisierung von beiden Feldern.

**TX-Synth-Pfad war schon mode-aware** (Orchestrator-Zeile 2805ff:
`synth_message` vs `synth_message_ft4` basierend auf
`self.config.operating.mode`).

**Tests:**
- `test_slot_sync.py` neu: 4 Tests für SlotBuffer-Parametrisierung
  (FT8-default, FT4-window, zero-pad, mode-switch)
- `test_tx_synth.py::test_ft4_synth_roundtrips_through_decoder` bereits
  vorhanden, läuft auf Pi mit gebauter C-Extension
- Frontend-Build erfolgreich

**Boot-Warning entfernt:** v0.3.3-Warning `"FT4 mode is NOT fully
supported"` ersetzt durch `"FT4 mode active: 7.5s slots,
decode_slot_ft4 + synth_message_ft4 wired."` als info-level Log.

**Wann wirst du FT4 nutzen?** Spec-Wert: schnellere QSOs (~5-7 sec vs
~12 sec für FT8), aber kürzere Bursts → weniger Sensitivity (≈3 dB
weniger DX-Reichweite). Typisch für Inland-NA-VHF-Contest oder
Conditions mit kurzem QSB. Sebastian kann jetzt **per UI-Toggle**
zwischen FT8/FT4 wechseln, Mode-Switch wirkt nächste Slot-Iteration.

### F7 — Directed CQ (CQ DX/EU/POTA) *(Erledigt v0.3.4)*

**Fix v0.3.4:** Neue Config-Option `operating.cq_directed: str` (max 4
chars, regex `^[A-Z0-9]*$`). Leerwert = klassischer `CQ {us} {grid}`.
Sonst prefix: z.B. `cq_directed: "DX"` → `CQ DX DK9XR JN58`, oder
`"POTA"` → `CQ POTA DK9XR JN58`.

**Code-Pfad:**
- `OperatingConfig.cq_directed` neu in `config/models.py`
- `MachineContext.cq_directed` neu in `statemachine/states.py`
- Hydration: Boot (`Orchestrator.__init__`) + Hot-Reload (`on_config_changed`)
- `_emit_cq`: wenn `ctx.cq_directed` gesetzt, prepend

**Tests:**
- `test_directed_cq_emit_dx`: `cq_directed='DX'` → `CQ DX DK9XR JN58`
- `test_directed_cq_empty_falls_back_to_plain`: `cq_directed=''` → klassisch

### F8 — Free-Text Tx5 *(Erledigt v0.3.4)*

**Fix v0.3.4:** Strikte Callsign-Heuristik im Parser. Wenn weder
`tokens[0]` noch `tokens[1]` wie ein Callsign aussieht (Regex
`^(?=.*[A-Z])(?=.*\d)[A-Z0-9/]{3,11}$`, also mind. 1 Buchstabe + 1
Ziffer), klassifizieren wir die Message als Free-Text:
- `ParsedMessage.is_freetext = True`
- `call_from`/`call_to`/`grid`/`report` bleiben `None`
- Downstream: `DecodedMsg.is_freetext` propagiert
- `_pick_hunt_target` skippt Free-Text-Decodes (kein QSO-Versuch)

**Tests:**
- `test_picker_skips_freetext_marked_as_cq` — Hunt-Picker ignoriert
  Decodes mit `is_freetext=True` auch wenn message mit "CQ" beginnt

**Beispiele die jetzt korrekt klassifiziert sind:**
- `73 GL` → is_freetext=True, kein call_from/call_to mehr
- `TU JIM` → is_freetext=True
- `5W ENDFED` → is_freetext=True
- Hashed-Calls (`<...>`) bleiben gültig (separate Behandlung)
- Standard-Calls `DK9XR W1AW JN58` unverändert geparst

**Schutz vor Worked-Liste-Pollution:** Vorher konnten Free-Text-Token
wie "73" oder "GL" als call_from in die `is_worked_before`-Map landen.
Nach Fix ist call_from=None → kein worked-Eintrag mehr.

### F9 — Contest-Messages im Picker *(Erledigt v0.3.4)*

**Fix v0.3.4:** Helper `_is_contest_cq(message, contest_tokens)` im
state-machine-Modul. `_pick_hunt_target` skippt CQs deren 2. Token in
folgendem Set ist: `{TEST, RU, FD, WW, WPX, SS, IARU, CWT}`.

Sebastian operiert nicht im Contest — wenn das mal jemand braucht,
ist `cqs = [d for d in cqs if not _is_contest_cq(...)]` per-Config
togglebar zu machen (~1 Zeile + neues `ctx.allow_contest_cq`).
Aktuell **fest off**.

**Test:** `test_picker_skips_contest_cq` — gibt zwei CQs (TEST + plain)
in den Picker, erwartet dass der plain gepickt wird.

**Beobachtung Real-Daten:** in den 2346 Decodes der letzten 2h **null
Contest-Messages** — ausserhalb von Contest-Wochenenden quasi nicht
relevant. Aber: wenn am Wochenende ein Contest läuft, würde der
Pi sonst Slot-Verschwendung mit erfolglosen Standard-Antworten
betreiben. Mit dem Filter: Pi pickt die nicht-Contest-Stationen
weiter und macht echte QSOs.

### F10 — AP-Decoding (a priori) *(bereits ditched, verifiziert)*

**Stand:** in `docs/wsjtx_qso_state_audit.md` als Tier-Bloat #8
dokumentiert, durch Killer-Query-Analyse (106 QSO_REPORT-Timeouts /
7 Tage, **0 davon AP-rettbar**) endgültig deferred.

**In diesem Audit verifiziert:** kein Code-Pfad ruft AP-Funktionen
auf, ft8_lib ist mit Standard-Soft-Decision-Decoder konfiguriert.
Konsistent mit Spec-Stand.

---

## Was geprüft wurde und sauber ist

### Tx-Message-Bau (alle 5 emit-Methoden)
| Methode | Tx-Stage | Format | Status |
|---|---|---|---|
| `_emit_cq` | Tx1 | `CQ {us} {grid4}` | ✅ |
| `_emit_respond_with_grid` | Tx2 | `{them} {us} {grid4}` | ✅ |
| `_emit_respond_with_report` | Tx3 | `{them} {us} {snr:+03d}` | ✅ seit v0.3.2 WSJT-X-konform |
| `_emit_send_r_report` | Tx4 | `{them} {us} R{snr:+03d}` | ✅ seit v0.3.2 WSJT-X-konform |
| `_emit_log_qso` | Tx5 | `{them} {us} RR73` | ✅ |
| GRACE-Pfad | Tx6 | `{them} {us} 73` | ✅ (nur bei Partner-RR73-Repeat) |

### Decode-Parser
- `parse_message` deckt CQ + directed-CQ + Tx2-Tx6 ab
- 100 % der real-traffic-Decodes geparst (2346/2346 letzte 2h)
- Closing-Token-Erkennung: RR73 + RRR + 73 alle drei
- Hashed-Call (`<...>`) als opaque Token weitergereicht (richtig)

### Slot-Timing
- 15s-Slot-Boundary via `round(posix/15)` — keine int()-Off-by-One-Drift
- TX-Slot-Parity (even/odd) konfigurierbar
- RX-Window 150ms Delay nach Slot-Ende → letzte ALSA-Period kommt mit

### Audio-Frequenz
- Quietest-100Hz-Bin-Picker für eigene CQ
- In-QSO-Frequenz folgt Partner (split operation)
- Bereich 300-2400 Hz (WSJT-X-Standard-Passband)

### Time-Sync-Guards
- Chrony ODER GPS-Fix → TX freigegeben
- `audio_drift_samples` + `time_offset_s` haben Schwellen
- TX_LOCKED-State bei drift > 50 samples oder offset > 0.5s

### PSK-Reporter
- IPFIX-UDP-Format (Standard)
- Flush alle 5 min batched
- Spotting nur wenn `upload_decodes=True` in Config

### ADIF-Export (jetzt nach v0.3.3-Fixes)
- ADIF 3.1.4 Header
- Alle Pflichtfelder: CALL, QSO_DATE, TIME_ON, BAND, MODE
- LotW/eQSL-Compat: STATION_CALLSIGN, OPERATOR, COUNTRY
- Multi-Op-Filename + Operator-Field

---

## Lessons Learned

1. **Spec-Konformität braucht expliziten Cross-Check.** Action 5
   (R-Report-SNR-Direction) wurde übersehen weil unser Audit auf
   State-Transitions fokussierte, nicht Message-Content. Künftig
   pro outgoing Tx-Message **mindestens einen realen Decode** als
   Oracle nutzen.
2. **Hardcoded Konstanten = Bug-Stäube.** `0.1.0` in PROGRAMVERSION,
   `dk9xr_ft8.adif` als Filename, `180000` als SAMPLES_PER_SLOT —
   alle drei Bugs aus „Initial-Wert vergessen wegzuziehen". Disziplin:
   **bei jedem Release auch nach Hardcodes greppen**.
3. **Feature-Toggle ohne Code-Vollständigkeit ist Trap.** FT4-Toggle
   sah aus als hätten wir Multi-Mode-Support, hatte aber nur 30 %
   implementiert. Künftig: entweder vollständig oder **Toggle erst
   einbauen wenn alle Layer hinzu**.
4. **Real-Daten schlagen Theorie.** Killer-Query (welche Calls hat der
   Partner nach Timeout gesendet?) widerlegte AP-Decoding-Hype mit
   8.5 von 10 erwarteter Fix-Effekten = 0. Künftig: **gegen Real-DB
   queryen bevor wir Features bauen** die statistisch fragwürdig sind.

---

## Nicht-Findings (Sanity-Check)

Folgende Sachen habe ich explizit geprüft und nichts gefunden:
- **Hashed-Call false-positive in worked-Liste:** nein, `<...>` wird
  nie in worked-Set übertragen (Validation greift)
- **SNR-Range-Overflow im Decoder:** ft8_lib clamped intern auf
  -24..+24 — kein out-of-range Risiko upstream
- **DT-Tolerance:** Decode mit dt > 2.5s wird vom shim gefiltert
- **PTT-Stuck bei Restart mid-burst:** v0.2.0 watchdog rettet via
  sd_notify, v0.2.1 panic-stop vor Restart entlastet zusätzlich
- **Split-Frequency-Drift in langem QSO:** Partner-Freq bleibt
  konstant solang `qso.freq_offset_hz` nicht geändert wird, keine
  ungewollte Drift
