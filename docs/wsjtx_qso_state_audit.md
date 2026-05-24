# WSJT-X QSO-State-Audit (Korrektheit, nicht Feature-Parität)

**Datum:** 2026-05-24
**Autor:** Claude (Auftrag Sebastian)
**Scope:** Vergleich unserer QSO-State-Machine (`statemachine/machine.py`)
gegen WSJT-X-Auto-Sequencing-Referenzverhalten. **Kein** Feature-Parity-
Sweep — das ist via `project_ft8_wsjtx_tier.md` separat gescoped + gecappt.

**Quellen:**

- *The FT4 and FT8 Communication Protocols* — Taylor/Franke/Somerville,
  QEX Jul/Aug 2020. Figure 5 (Top-Level State Diagram) + Figure 6
  (Calling-State detail) + §8 (Performance / AP-Decoding).
  Download: https://wsjt.sourceforge.io/FT4_FT8_QEX.pdf
- *WSJT-X User Guide 2.6.0/2.7.0* — sourceforge.io/wsjtx-doc
- Community-Diskussionen: wsjtx.groups.io (Watchdog/Auto-Seq Threads)

---

## 0 — Referenz: WSJT-X 6-Message Standard-Sequenz

Aus QEX-Paper Figure 5 + Tabelle 1:

| TxN | Message | Bedeutung |
|---|---|---|
| Tx1 | `CQ MYCALL MYGRID` | Eigener CQ-Ruf |
| Tx2 | `HISCALL MYCALL MYGRID` | Antwort mit Grid |
| Tx3 | `HISCALL MYCALL SNR` | Signal-Report |
| Tx4 | `HISCALL MYCALL R-SNR` | Roger-Report |
| Tx5 | `HISCALL MYCALL RR73` | Roger + Goodbye |
| Tx6 | `HISCALL MYCALL 73` | Final-Goodbye |

**Wichtigster Reference-Mechanismus (QEX Fig. 6):** WSJT-X "Calling"-State
besteht aus `Waiting → Parse message → [TxN update]` Zyklen. Eine
Transition `Tx message = TxN+1` passiert **nur wenn** im aktuellen Slot
eine *neue* "for-us"-Message decodiert wurde, die der erwarteten
Vorgängernachricht entspricht. Andernfalls **bleibt die alte TxN im
Sender und wird im nächsten Slot wiederholt**.

Daraus folgt das WSJT-X-Default-Verhalten:
- **Kein Slot-Counter-Retry-Limit.** Re-Send unbegrenzt.
- **Kein Failed-Cooldown.** Nach Watchdog manuell neu starten.
- **Watchdog = Tx-Watchdog**, default 6 min (`Settings → General →
  Tx watchdog`). Deaktiviert Auto-Tx danach.
- **AutoSeq-Off-after-QSO:** "Auto-Seq deactivates Enable Tx at the end
  of each QSO" — heißt: nach erfolgreichem QSO halt, nicht weiter
  CQ rufen (es sei denn "Call CQ first" gesetzt).

---

## 1 — Audit: State-by-State Vergleich

Legende: ✅ Match / ⚠️ Intentional Deviation / ❌ Bug / 📋 Bewusst-anders-und-OK / 🚫 Tier-Bloat (außer Scope)

### 1.1 IDLE → Pickup eines CQ (Hunting-Modus, `auto_answer=true`)

| Aspekt | WSJT-X | Unsere SM | Status |
|---|---|---|---|
| Pickup-Strategie | „Call 1st" → erster decodierter CQ im Slot wird gepickt (falls aktiviert) | Stärkster SNR-CQ, mit DXCC-Präferenz + Blacklist + Cooldown + SNR-Floor + Audio-Freq-Filter + Skip-Worked | 📋 Bessere Heuristik für Unattended-Betrieb, gewollt |
| Tail-Ender-Detection in IDLE | "Tail-end calling" durch Doppelklick im IDLE (manuell) | Auto: PRIO 1 in `on_decodes` — Direct-Reply-Pickup mit Report-from-partner | ⚠️ Wir machen es automatisch in IDLE, WSJT-X braucht Klick |
| Grid-Reply-Pickup in IDLE | Doppelklick auf "DK9XR W1AW FN31" in IDLE → manuelle Antwort | Auto: PRIO 2 (`_find_answer_to_us`) → QSO_RESPOND mit Report | ⚠️ Wir automatisieren, WSJT-X manuell |
| Cooldown-Check vor Pickup | Existiert nicht | `recent_until`-Filter vor Pickup | 📋 Unsere Verbesserung für Unattended |

**Lücken:** Keine.

**Anmerkung „Tail-Ender-Höflichkeit" (Audit-Selbstkorrektur 2026-05-24):**
Ich hatte initial einen vermeintlichen Bug aufgenommen — „WSJT-X wartet
bei Tail-End-Calls bis Partner sein finales 73 gesendet hat, wir nicht".
Bei genauerem Code-Lesen ist das **strukturell nicht relevant** für uns:
`_pick_hunt_target` filtert ausschließlich auf Messages mit
`call_to is None` UND `startswith("CQ")` — wir picken nur CQ-rufende
Stationen, niemals mid-QSO-Stationen. Damit kann der Tail-Ender-
Etikette-Konflikt der WSJT-X adressiert (manuelles Anrufen einer
mid-QSO-Station via Doppelklick) bei uns nicht entstehen.

---

### 1.2 CQ_CALLING (eigener CQ wird gerufen)

| Aspekt | WSJT-X | Unsere SM | Status |
|---|---|---|---|
| CQ-TX-Slot-Parity | "Tx Even/1st" Checkbox, auto-flip bei Doppelklick auf gepickten Partner | `cq_tx_slot_parity` aus Config (statisch even/odd), kein Auto-Flip | ⚠️ Bewusst statisch — wir sind im CQ-Modus ja nicht reaktiv auf Partner |
| Smart-Frequency-Picker | TX-Audio-Freq ist fixiert (UI-Slider), bei CQ wird derselbe Freq genutzt | `_next_cq_freq_hz`: schaut Belegungs-Histogramm, picked ruhigsten Bin | 📋 Unsere Verbesserung — minimiert Kollisionen |
| Tail-Ender während CQ | AutoSeq: wenn Partner mit Report antwortet (`Tx3`-style) → in Calling-State stay, send `Tx4` (R-Report) | `on_decodes` PRIO `te = _find_answer_with_report_to_us` → State.QSO_REPORT, `_emit_send_r_report` | ✅ Match |
| Standard-Reply auf CQ | Partner antwortet mit Grid → wir senden Report | `_find_answer_to_us` → State.QSO_RESPOND, `_emit_respond_with_report` | ✅ Match |
| Re-Send des CQ ohne Antwort | jeden Slot bis Watchdog (kein Counter) | `on_slot_tick` → `_emit_cq()` jeden parity-matching Slot, kein Limit | ✅ Match |
| Watchdog-Mechanismus | 6 min Tx-Watchdog → Halt Tx | Kein direkter Watchdog im CQ-State — endlos CQ bis User stoppt | ❌ **Bug-Kandidat**: könnte stundenlang CQ rufen wenn keiner antwortet |

**Lücken:**
- **CQ-Watchdog fehlt**: WSJT-X stoppt nach 6 min ohne QSO-Progress. Wir
  haben einen separaten `mode_watchdog_min`-Push (15 min stillschweigen),
  aber der nimmt nur den Pi vom CQ-Modus runter wenn er **keine
  Decodes** mehr macht. Wir haben keinen Mechanismus für "CQ rufen seit
  X Minuten, niemand antwortet, vielleicht Band tot oder QRG belegt".

  → **Action 1:** `cq_idle_timeout_min` (existiert bereits in Config!)
  prüfen ob aktiv. Falls ja: passt; falls nein: scharfschalten.

---

### 1.3 QSO_RESPOND (wir warten auf Report vom Partner)

| Aspekt | WSJT-X | Unsere SM | Status |
|---|---|---|---|
| Erwartung | `Tx3` von Partner (Report) | `_find_report_from_them` → QSO_REPORT, R-Report senden | ✅ Match |
| Partner repeated CQ (uns nicht gehört) | Re-send Tx2 (Grid) jeden Slot bis Watchdog | Re-send mit `cq_resends`-Counter, Limit `qso_max_cq_resends=2`, dann bail + Cooldown | ⚠️ Bewusst aggressiver Bail — vermeidet Hammering einer Station die uns nie hört |
| Partner picked anderen Caller | Partner sendet "OTHERCALL HISCALL ..." — WSJT-X bleibt im Calling, sendet weiter Tx2 ins Leere | `heard_them_with_other` → bail + Cooldown | 📋 Unser Bail ist proaktiv. WSJT-X verschwendet Slots. |
| Partner geht silent | Watchdog (6 min) | `qso_max_stale_slots=6` → bail + Cooldown (~90 s) | ⚠️ Wir geben deutlich schneller auf |
| Failed-Cooldown danach | Nicht vorhanden | 15 min Default `qso_failed_cooldown_min` | 📋 Unattended-Optimierung |

**Lücken:** Keine kritischen.

---

### 1.4 QSO_REPORT (wir warten auf RR73)

| Aspekt | WSJT-X | Unsere SM | Status |
|---|---|---|---|
| Erwartung | `Tx5` (RR73) oder `Tx6` (73) vom Partner | `_find_closing` matcht RR73/RRR/73 → QSO_LOG | ✅ Match |
| Partner repeated Report (Tx3 statt Tx5) | AutoSeq: detect Tx3 in eingehender Message → "we're back at Tx3-state" → Tx4 (R-Report) wird WEITER gesendet (kein Reset) | **2026-05-24 (v0.1.x):** `report_resends`-Counter, 1× Resend, dann bail | ✅ Match (Verhalten WSJT-X-kompatibel, mit Bail-Schutz) |
| Partner fällt zurück zu CQ (Tx1 statt Tx5) | AutoSeq: AlternativerWeg dass Partner unser R-Report nicht decoded. WSJT-X bleibt in Tx4, sendet R-Report weiter (bis Watchdog) | **2026-05-24 (v0.2.1)** nach DO1BJF-Verlust: gleicher `report_resends`-Counter wie repeated-report-Pfad, 1× Resend dann bail | ✅ Match (gleiches Symptom — "sie hörten R-Report nicht" — gleiche Reaktion) |
| AP-Decoding (a priori) | RR73/73/RRR-Spezial-Decoder mit ~3 dB Vorteil (QEX Fig 8) | Nicht implementiert (Sweep B, deferred) | 🚫 Tier-Bloat — explizit verworfen |
| Partner silent | Watchdog 6 min | `qso_max_stale_slots=6` → bail + cooldown (~90 s) | ⚠️ Schneller Bail |
| Re-send Limit | unlimitiert bis Watchdog | `qso_max_report_resends=1` | ⚠️ Härter als WSJT-X |

**Lücken:**
- **AP-Decoding für RR73**: laut QEX-Paper bringt das **bis zu 3 dB extra
  Empfindlichkeit** speziell am QSO-Ende (genau wo wir am häufigsten
  Timeouts haben — siehe UN7JO-Verlust). Das ist quantitativ unsere
  größte WSJT-X-Diskrepanz. **Aber:** explizit in
  `project_ft8_wsjtx_tier.md` als Sweep B deferred/gestrichen. Nur
  re-aktivieren wenn echte Hardware-Messung den Bedarf zeigt. Heute
  nicht angefasst.

---

### 1.5 QSO_LOG / nach RR73

| Aspekt | WSJT-X | Unsere SM | Status |
|---|---|---|---|
| Tx6 (`73`) senden | Wird gesendet (Tx5 → Tx6 transition in Fig 6) | **Nicht gesendet** — wir sind nach RR73 direkt im IDLE/CQ_CALLING | 📋 Bewusste Slot-Einsparung |
| Logging | Nach Tx5 oder Tx6 wird ADIF geschrieben + (optional) zu LoTW/QRZ hochgeladen | `LOG_QSO`-Action → ORM-Insert → QRZ-Upload-Hook | ✅ Match (Logik) |
| Auto-CQ-loopback | "Call CQ first" Checkbox → ja, sonst halt | `auto_cq`-Flag → State.CQ_CALLING + neuer CQ | ✅ Match |
| AutoSeq-Off-after-QSO | Default: "AutoSeq deactivates Enable Tx at the end of each QSO" | Nicht implementiert — wir lassen `auto_answer` aktiv | ⚠️ Bewusst aktiv-bleibend (Unattended-Designziel) |
| Partner repeated RR73 nach unserem Halt | AutoSeq würde Tx6 (73) re-senden | Wir sind schon IDLE/CQ_CALLING — kein Re-Ack | ❌ **Bug-Kandidat (mild)**: Partner könnte denken QSO unsicher |

**Lücken:**
- **Repeated RR73 wird ignoriert nach QSO_LOG**: Wenn der Partner im
  nächsten Slot nochmal RR73 sendet (= "habe deine Antwort nicht
  decodiert"), reagieren wir nicht. WSJT-X würde Tx6 (`73`) re-senden
  als Bestätigung. Praktische Folge: Partner sieht **kein** finales
  73 von uns und denkt evtl. QSO unsicher. Aber: ist im Log → kein
  funktionaler Schaden. Klassisches "WSJT-X-höflicher".

  → **Action 2 (klein):** im nächsten Slot nach QSO_LOG noch ein „73"
  hinterhersenden wenn Partner RR73 wiederholt. Kostet 1 Slot, ist
  WSJT-X-konformer. Priority low.

---

### 1.6 Globale State-Machine-Mechanik

| Aspekt | WSJT-X | Unsere SM | Status |
|---|---|---|---|
| Tx-Watchdog (Wallclock) | 6 min Default | Kein Wallclock-Watchdog, nur slot-counter | ⚠️ Bewusst slot-basiert |
| Halt-Tx-Button | "Halt Tx" stoppt sofort | `on_user_stop` → State.IDLE + STOP_TX | ✅ Match |
| Auto-Seq-Toggle | Checkbox | `ctx.auto_answer` (Hunting), `ctx.auto_cq` (CQ-loopback) | ✅ Match (granularer) |
| Late-TX-Tolerance | Decoder akzeptiert TX die bis ~5 s nach Slot-Beginn starten (QEX Fig 9) | Decoder ist ft8_lib-vendored → gleiche Toleranz | ✅ Match (vermutlich) |
| TX-Freq-Stick during QSO | TX folgt dem Audio-Slot wo Partner gerufen hat | `qso.freq_offset_hz` wird aus Decoded-Msg übernommen, alle TX im QSO darauf | ✅ Match |

---

## 2 — Identifizierte Lücken (priorisiert)

### Bug-Kandidaten (würde fixen)

1. **CQ-Watchdog im endlosen CQ-Modus** *(Action 1)*
   - WSJT-X stoppt CQ nach 6 min ohne Pickup. Wir rufen endlos.
   - **Existiert evtl. schon** als `cq_idle_timeout_min` in Config — Prüfen
     ob die Logik im Orchestrator aktiv ist und greift.
   - **Sebastian-Klarstellung 2026-05-24:** Pi soll NIE selbst ausschalten,
     der State bleibt CQ_CALLING. Nur ntfy-Push „CQ läuft seit Xmin ohne
     Antwort" — Sebastian entscheidet ob STOP / Hunting / weiter.
   - **Aufwand:** 15 min Code-Audit + ~10 Zeilen Fix (Wallclock-Timer +
     One-Shot-ntfy-Push).

2. **Mild: Tx6/73-Re-Send wenn Partner RR73 wiederholt** *(Action 2)*
   - Falls Partner im Slot nach QSO_LOG nochmal RR73 schickt, ein 73
     hinterhersenden. WSJT-X-Konformität, Höflichkeit.
   - **Aufwand:** 10-15 Zeilen, neue Mini-State `QSO_LOGGED_GRACE` oder
     einfach im `on_decodes` nach QSO_LOG noch 1 Slot zuhören.
   - **Priority:** low — funktional schon korrekt geloggt.
   - **Erledigt (v0.1.x):** `QSO_GRACE`-State + repeated-RR73-Detection
     + 73-Resend. Sebastian sah QSO_GRACE wenn Partner RR73 wiederholt.

3. **R-Report-Resend wenn Partner zurück zu CQ statt RR73** *(Erledigt v0.2.1)*
   - **Symptom:** Sebastian DO1BJF-QSO 2026-05-24 — wir sendeten R+04 1×,
     DO1BJF decoded es nicht (SNR -12 dB marginal), fiel zurück in
     `CQ DO1BJF JO42`-Loop. Unser code wartete 45 s auf RR73, kein
     RR73 kam → bail + IDLE, QSO nicht geloggt.
   - **WSJT-X-Referenz (QEX Fig 6):** AutoSeq bleibt in Tx4 solange
     "for-us" Decode kein Tx5 enthält. Ein Tx1 (CQ) vom Partner ist
     "for-us" wenn his_call match, aber kein Tx5 → bleibt in Tx4 →
     R-Report wird im nächsten TX-Slot wiederholt.
   - **Fix (v0.2.1):** im QSO_REPORT-Branch zusätzlich zu "repeated
     report" auch "repeated CQ vom selben Partner" als R-Resend-Trigger.
     Gleicher `report_resends`-Counter, gleicher Cap
     `qso_max_report_resends=1`. Bei Match: stale_slots Reset +
     R-Report-Resend genau wie im repeated-report-Pfad.
   - **Test:** `test_qso_report_partner_repeats_cq_triggers_r_resend`
     in `backend/tests/test_statemachine.py`.
   - **Lessons:** Symptom „Partner hat unser R-Report nicht decoded"
     hat zwei Manifestationen (repeated report + repeated CQ), gleiche
     Reaktion. Refactor zu unified "they didn't hear R-Report"-Branch.

4. **picked_another-Detection auch in QSO_REPORT** *(Erledigt v0.2.2)*
   - **Symptom:** Sebastian M7CCZ-Case 2026-05-24 16:42 UTC — M7CCZ gab uns
     `DK9XR M7CCZ -06`, wir antworteten R-06, dann startete M7CCZ statt
     RR73 ein neues QSO mit EA1DUS (`EA1DUS M7CCZ -03` → RR73). Unsere
     State-Machine sah keinen RR73 zu DK9XR + keinen repeated-CQ + keinen
     repeated-report → wartete 3 Slots (~45 s) bis Timeout. Real-Loss: 3
     verschwendete RX/TX-Slots, Pi-Slot blockiert für andere QSO-
     Möglichkeiten.
   - **Gap:** Die `heard_them_with_other`-Detection (Partner sendet
     `call_from=their, call_to=other`) existierte bereits in QSO_RESPOND
     (Zeile 294-303 in `machine.py`) aber **fehlte in QSO_REPORT**.
     Asymmetrie zwischen den beiden QSO-Active-States.
   - **Fix (v0.2.2):** Spiegel-Check in QSO_REPORT VOR der
     repeated-report/cq-Auswertung. Bei Match: sofort
     `_bail_qso_with_cooldown(their_call, "picked_another")` — gleicher
     Bail-Pfad wie in QSO_RESPOND, gleicher 15-min-Cooldown.
   - **Tests:**
     - `test_qso_report_partner_picks_another_bails_with_cooldown`
     - `test_qso_report_picked_another_takes_priority_over_repeated_report`
   - **Quantitatives:** In der Killer-Query oben („106 QSO_REPORT-Timeouts
     letzte 7 Tage") waren 8 Cases (8 %) "Partner schon im neuen QSO mit
     anderer Station" — genau die Klasse die dieser Fix einsparrt. **Keine
     zusätzlichen QSOs** (Partner ist sowieso weg), aber **8 × 3 Slots =
     24 vergeudete Slots/Woche** zurückgewonnen.
   - **Lessons:** Symmetrie-Disziplin zwischen verwandten States — wenn
     QSO_RESPOND einen Bail-Trigger hat, QSO_REPORT prüfen ob derselbe
     Trigger dort auch greifen sollte. Tracking-Punkt für künftige
     state-machine-Additions.

### Bewusste Abweichungen (würde NICHT ändern, dokumentieren)

3. Aggressive Bail + 15-min-Failed-Cooldown — Unattended-Design.
4. Slot-Counter statt Wall-Clock-Watchdog — präziser für headless.
5. Smart-Freq-Picker für CQ — verbessert Kollisionsverhalten.
6. Auto-pickup von Direct-Replies in IDLE — Hunting-Mode-Designziel.
7. SNR-Floor + Audio-Freq-Filter + DXCC-Prio im Picker — Unattended-Optimierung.

### Tier-Bloat (explizit nicht bauen, siehe `project_ft8_wsjtx_tier.md`)

8. **AP-Decoding (a priori)** für RR73/73 — bringt theoretisch ~3 dB.
   Sebastians Entscheidung vom 15.05.2026: Sweep B nicht spekulativ,
   nur wenn echte Daten Bedarf zeigen.

   **Vorstudie 2026-05-24 (Audit-Action F4):** 106 QSO_REPORT-Timeouts
   der letzten 7 Tage analysiert. Killer-Query: was haben die Partner
   nach unserem Timeout gesendet?

   | Kategorie | Anzahl | % |
   |---|---|---|
   | Partner danach silent (war wirklich weg) | 83 | 78 % |
   | Partner sendete direkt nochmal an UNS (= AP-rettbar) | **0** | **0 %** |
   | Partner war schon im neuen QSO mit anderer Station | 8 | 8 % |
   | Partner sendete neuen CQ (hat uns aufgegeben) | 14 | 13 % |

   **Ergebnis:** AP-Decoding hätte EXAKT 0 zusätzliche QSOs der letzten
   7 Tage gerettet. Die echten Verluste sind „Partner-aufgegeben" oder
   „Partner-im-neuen-QSO" — beides Probleme die mit besserem Decoder
   nicht lösbar sind. **Sweep B endgültig defer**, Sebastians
   ursprüngliche Entscheidung 100 % bestätigt.

   **Was tatsächlich helfen würde** (nicht Teil dieses Audits, eigene
   Tickets):
   - ~~QSO_REPORT-Bail wenn Partner schon im neuen QSO mit anderer Station
     (heard_them_with_other-Pattern wie schon in QSO_RESPOND existiert) →
     spart Slots, erhöht QSO-Quote nicht~~ **→ Erledigt v0.2.2 als Action 4.**
   - Schnelleres Pickup von Stationen die wir grad gehört haben aber
     verloren — mit Cooldown-Bypass-Logik

---

## 3 — Quantitatives: lohnt sich Action 1?

Schauen wir uns die DO3XR-Stats vom 24.05. an (siehe vorherige
Konversation):

- **72 QSOs in 18 h** = im Schnitt 1 QSO alle 15 min
- Im **CQ-Modus** ist die Pickup-Rate niedriger als Hunter → CQ-Watchdog
  würde Stunden-langes Endlos-Rufen verhindern, was Sebastian zuletzt
  bewusst gegen Hunter eingetauscht hat
- **mode_watchdog_min** existiert und greift bei Funkstille — aber nicht
  bei „decode aktiv, niemand antwortet auf unseren CQ"

**Empfehlung:** Action 1 prüfen + aktivieren = sehr lohnend.
Action 2 = Nice-to-have, nicht dringend.

---

## 4 — Sebastian-Entscheidungs-Checkliste

- [ ] Action 1 — CQ-Watchdog scharfschalten oder neu bauen? (~15 min)
- [ ] Action 2 — Tx6/73-Resend nach QSO_LOG? (~10 min, optional)
- [x] Action 3 — Tail-Ender-Höflichkeit prüfen: **erledigt, war
      Fehlalarm**. Unser Picker filtert auf CQ-only, mid-QSO-Stationen
      werden gar nicht erst angerufen. Siehe §1.1.
- [ ] Sweep B (AP-Decoding) re-evaluieren wenn echte Timeout-Stats
      vorliegen?

---

## Anhang A — Methodik

1. Komplettes `statemachine/machine.py` gelesen (757 Zeilen)
2. WSJT-X QEX-2020-Paper Figure 5+6 ausgewertet (offizielles
   State-Diagram)
3. Cross-Check via WSJT-X-Manual-Suchen + Community-Threads
   (wsjtx.groups.io)
4. Pro State-Transition Tabelle aufgebaut: Erwartung vs. Implementierung
5. Status-Klassifizierung: Match / Intent / Bug / Tier-Bloat
6. Bug-Kandidaten an Ende priorisiert
