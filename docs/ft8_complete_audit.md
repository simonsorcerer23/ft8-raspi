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
| F5 | Hashed-Call-Receive für eigene compound calls | low | dokumentiert (latent) |
| F6 | FT4-Mode nur teilweise verdrahtet | medium | dokumentiert + Warning |
| F7 | Directed CQ (CQ DX/EU/POTA) — outgoing nicht unterstützt | low | dokumentiert (nice-to-have) |
| F8 | Free-Text Tx5 wird stumm ignoriert | low | dokumentiert |
| F9 | Contest-Messages (RU/FD/RTTY) im Picker nicht erkannt | low | dokumentiert |
| F10 | AP-Decoding (a priori) | low | bereits ditched, verifiziert |

**Bilanz:** 4 sofort-fixe in v0.3.3, 1 mit Runtime-Warning + Doku, 4
dokumentiert als nice-to-have. **Keine kritischen Bugs offen.**

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

### F5 — Hashed-Call-Receive für eigene compound calls *(latent)*

**Symptom (theoretisch):** Wenn unser Pi je einen compound own-call
verwendet (`DK9XR/P`, `DL/W1AW`), und Partner antwortet uns:
- Partner-Tx muss unser Call hashen (passt nicht in 13-Char-Frame)
- Decode bei uns: `<...> THEIR_CALL JN58` (oder R-Report)
- `_find_*_to_us`-Check: `d.call_to == my_call` → **False** (call_to ist `<...>`)
- → wir verpassen die Antwort, QSO timeoutet

**Real-Welt-Risiko aktuell:** **null**. Sebastians Pi hat nur
DK9XR + DO3XR konfiguriert, beides kurze Calls ohne `/`. Hash-
Frames werden nicht generiert.

**Was nötig wäre wenn Sebastian /P-Operation will:**
1. Hash-Tabelle der recently-seen own-call-Pairs aufbauen
2. `_find_*`-Funktionen prüfen `call_to in {my_call, "<...>"}` und im
   Fall `<...>` zusätzlich `call_from == current_qso.their_call` als
   Match (Kontext aus laufendem QSO)
3. Test für `<...> EK/RX3DPK -10` → erkennt als R-Report von EK/RX3DPK

**Aufwand:** ~30 Zeilen + 2 Tests, ~30 min wenn er das je braucht.

**Decision:** **defer bis Sebastian /P/MM operiert**.

### F6 — FT4-Mode nur teilweise verdrahtet *(dokumentiert + Warning v0.3.3)*

**Status:**
- `OperatingConfig.mode = "FT4"` ist in der Config-Definition wählbar
- `Orchestrator.start()` swappt SlotClock auf 7.5s ✓
- `audio/slot_sync.py` hat **`SAMPLES_PER_SLOT = SAMPLE_RATE_HZ × 15 = 180000`** hardcoded ✗
- `decode/ft8_native.c` ist FT8-only ✗
- TX-Synth produziert FT8-Costas-Array nicht FT4-Costas ✗

**Konsequenz wenn `mode=FT4` gesetzt:**
- SlotClock feuert alle 7.5s, aber Buffer extrahiert 15s → spans 2
  FT4-Slots → Decoder sieht Mix aus 2 Slots → zerstörte Frames
- TX würde FT8-Frames in 7.5s-Slots packen → Partner-FT4-Decoder
  versteht's nicht
- **Auf FT4 funktioniert nichts** (Hunt geht nicht, CQ geht nicht)

**Fix v0.3.3 (minimal):** Runtime-Log-WARNING beim Boot wenn
mode=FT4 gesetzt:
```
FT4 mode is NOT fully supported (audit-finding v0.3.3): SlotBuffer
is hardcoded to 15s + decoder is FT8-only. Use mode=FT8 in production.
```

**Vollständige FT4-Unterstützung** wäre eigenständiges Projekt:
- SlotBuffer parametrisieren mit `slot_seconds`
- ft8_lib FT4-Decoder einbinden (existiert in ft8_lib)
- TX-Synth FT4-Symbol-Tabelle (4-FSK, 105 Symbole, 20.83 Hz Spacing)
- Tests
- → ~1-2 Personentage. Lohnt sich nicht solange Sebastian FT8 fährt.

**Decision:** **FT4 als „experimentell/broken" deklariert**, Warning
beim Boot reicht. UI-Dropdown bleibt drin (für späteres Enablement),
Operator weiß durchs Warning Bescheid.

### F7 — Directed CQ (CQ DX/EU/POTA) nicht im Tx-Pfad *(nice-to-have)*

**Gap:** Unser `_emit_cq` sendet immer `CQ DK9XR JN58`. Real-Welt-
Patterns die wir NICHT generieren:
- `CQ DX K1ABC FN42` — möchte nur DX-Stationen
- `CQ EU K1ABC FN42` — möchte nur EU-Stationen
- `CQ POTA K1ABC FN42` — POTA-Activator-Spot
- `CQ TEST K1ABC FN42` — Contest-CQ

**Receive-Pfad funktioniert:** `parse_message` erkennt directed-CQs
(Code-Path mit `rest[0].isalpha()`-Check), call_from + grid werden
korrekt extrahiert.

**Send-Pfad nicht implementiert.** Wenn Sebastian je „nur DX"
ankündigen wollte: 1 String-Konfig + `_emit_cq` erweitern.

**Decision:** **nicht jetzt bauen** — Sebastians Use-Case ist
Standard-CQ, nicht Contest/POTA.

### F8 — Free-Text Tx5 wird ignoriert *(nice-to-have)*

**Spec:** FT8 erlaubt 13-char Free-Text als Tx5/Tx6. Beispiele:
- `73 GL`
- `TU 73 OM`
- `5W ENDFED`
- `NAME RAY`

**Unser Parser:** schlägt fehl (`parse_message` versucht
`<to> <from> <tail>` Pattern → tail ist Free-Text-Token → keine
Klassifizierung als Closing → fällt durch). Free-Text-Decodes
landen in der DB mit `call_from = first_token`, `call_to = second_token`
und `message = full_string` — kann zu False-Positives in der
Worked-Liste führen wenn der erste Token zufällig wie ein Call aussieht.

**Real-Welt-Häufigkeit:** in den 2346 Decodes der letzten 2h:
**null Free-Text-Messages** (alle waren standard QSO-Patterns).
FT8-Operatoren nutzen das praktisch nie — der Mode ist auf
strukturierten Austausch optimiert.

**Decision:** **defer** — Real-Risiko gering, Komplexität nicht
gerechtfertigt.

### F9 — Contest-Messages im Picker nicht erkannt *(nice-to-have)*

**Spec:** FT8 hat 4 Contest-Formate die andere Message-Strukturen
verwenden:
- **RU-Contest:** `<call> <call> RST EX` (EX = Sektion-Code)
- **NA-VHF:** `<call> <call> R<grid>`
- **ARRL-FD:** `<call> <call> CLASS SECTION`
- **WW-Digi:** Standard FT8 mit Token-Suffix

**Was wir tun:** wir antworten auf jede CQ, auch contest-CQ. Wenn ein
Contester `CQ TEST K1ABC FN42` ruft, antworten wir mit Standard-Grid
`K1ABC DO3XR JN58` — Contester ignoriert uns (er erwartet Class+Section).

**Real-Welt-Häufigkeit:** in der letzten 2h **null Contest-Decodes**.
Außerhalb von Contest-Wochenenden quasi nicht relevant.

**Decision:** **defer** — Sebastian operiert nicht im Contest. Wenn
ein Contest-Weekend kommt: Hunting-Picker könnte `CQ TEST`-Pattern
deprioritisieren um Slot-Verschwendung zu vermeiden. ~10 Zeilen.

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
