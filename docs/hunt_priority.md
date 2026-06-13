# Hunt-Priority-Tiers (v0.66.0)

Beim Hunting (Auto-Antworten auf CQ-Rufe) priorisiert der Picker
nicht mehr nur „neues DXCC zuerst, dann SNR" wie vor v0.10.0, sondern
nutzt eine **kaskadierende Tier-Kette**, deren Reihenfolge der Benutzer
via Konfig-UI selbst bestimmen kann.

## Architektur

```
                   ┌────────────────────┐
   Decodes ──┐     │  HUNT_TIERS-Registry │
             │     │  (machine.py)         │
             ▼     │                       │
   ┌────────────┐  │  marine_psk → 0/1     │
   │ _pick_hunt │  │  marine     → 0/1     │
   │  _target   │──┤  new_dxcc_psk → 0/1   │
   └────────────┘  │  new_dxcc   → 0/1     │
        │          │  psk_heard_us → 0/1   │
        │          │  psk_snr → dB/-99     │
        ▼          │  not_worked → 0/1     │
  Score-Tuple      │  dxcc_rarity → 0..100 │
  (lex. Vergleich) │  snr → int (Tie-Brk)  │
                   └────────────────────┘
```

Jeder Tier liefert einen **integer-Score** (höher = besser).
Das Score-Tuple wird **lexikographisch** verglichen — die erste Stelle
dominiert, bei Gleichstand entscheidet die zweite usw. Letzte Stelle
ist konventionell `snr` als Tie-Breaker.

## Die Tiers im Detail

| Name | Bedingung | Score | Datenquelle |
|---|---|---|---|
| `marine_psk` | Call ist Marinefunker **und** hat uns laut PSK Reporter recently gehört | 0/1 | `marinefunker.json` + PSK-Cache |
| `marine` | Call ist Marinefunker (auch ohne PSK-Confirmation) | 0/1 | `marinefunker.json` |
| `tail_end_target` | Station hat in den letzten 30 s ein RR73/RRR/73 gesendet (= QSO beendet, jetzt frei) — 24 h Cooldown pro Station | 0/1 | `tail_end_candidates` + Toggle `tail_end_hunter_enabled` |
| `new_dxcc_psk` | Call ist aus neuem DXCC **und** PSK sagt „hört uns" | 0/1 | cty.dat + PSK-Cache |
| `new_dxcc` | Call ist aus neuem DXCC (auch ohne PSK) | 0/1 | cty.dat + worked-Set |
| `psk_heard_us` | PSK Reporter sagt: dieser Call hat uns recently gespottet | 0/1 | PSK-Cache |
| `new_dxcc_band` | DXCC haben wir, aber NICHT auf diesem Band (5BWAS) | 0/1 | `worked_dxcc_band`-Set |
| `new_grid` | Neues Maidenhead-Grid (VUCC-Award) | 0/1 | `worked_grids`-Set |
| `new_grid_band` | Grid haben wir, aber NICHT auf diesem Band (VUCC-Band-Variation) | 0/1 | `worked_grid_band`-Set |
| `not_worked` | Call wurde noch nie gearbeitet | 0/1 | `worked`-Set |
| `dxcc_rarity` | DXCC-Rarity-Score aus Tabelle (P5=100, 9J=45, DE=0) | 0..100 | `dxcc_rarity.json` |
| `psk_snr` | PSK Reporter SNR, mit dem uns die Gegenstation gehört hat | dB, fehlend = -99 | PSK-SNR-Cache |
| `snr` | Signal-to-Noise Ratio in dB | int | aus Decode-Pipeline |

## Default-Reihenfolge

Aus `OperatingConfig.hunt_priority`:

1. `not_bad_reputation` — Soft-Blacklist meiden (Filter, v0.15.0)
2. `not_his_tx_slot` — Slot-Parity-Awareness (Filter, v0.15.0)
3. `not_in_pileup` — Pile-Up-Avoidance (Filter, v0.19.0)
4. `marine_psk` — Marinefunker mit PSK-Trust
5. `marine` — Marinefunker (auch ohne PSK)
6. `new_dxcc_psk` — Neues DXCC mit PSK-Trust
7. `new_dxcc` — Neues DXCC (auch ohne PSK)
8. `grayline` — CQ-Rufer im eigenen Grayline-Fenster (v0.14.0)
9. `band_open` — hamqsl: Band aktuell „Good" (v0.14.0)
10. `active_hour` — DB-History: Continent jetzt aktiv (v0.16.0)
11. `buddy_seen` — Worked auf anderem Band (RX-Pfad bekannt, v0.17.0)
12. `new_dxcc_band` — 5BWAS-Award-Tracking
13. `new_grid` — Neues Maidenhead-Grid (VUCC)
14. `new_grid_band` — Grid auf diesem Band noch nicht (VUCC-Band)
15. `not_worked` — Neue Calls (auch routine)
16. `dxcc_rarity` — Rarity-Bonus
17. `psk_heard_us` — PSK sagt „hört uns" (v0.65.3 ZURÜCKGESTUFT, s.u.)
18. `psk_snr` — graduelles PSK-Reciprocity-Signal
19. `snr` — Haupt-Tie-Breaker (bestes Signal gewinnt)
20. `tail_end_target` — Tail-End-Pick (v0.65.2 UNTER `snr`, s.u.)

**Begründung der Default-Reihenfolge** (Sebastian-Wunsch 2026-05-26):
- Marinefunker top weil persönliche Community (Raymond ist Mitglied)
- PSK-bestätigte DXCCs darüber, weil Asymmetrie-Trust den Reply-Erfolg sichert
- Pure-PSK-Tier darüber als Allgemein-Asymmetrie-Boost
- 5BWAS vor `not_worked` weil Band-Variation für Awards mehr Wert hat als Routine-First-Contact

**Telemetrie-getriebene Anpassungen (pick_attempt-Auswertung 2026-06):**
- `psk_heard_us`: v0.65.1 zunächst HOCHGESTUFT (Kleinstichprobe 12,6 %), in
  **v0.65.3 wieder ZURÜCKGESTUFT** — bei größerem n widerlegt: als Entscheider
  nur 2,6 % Completion (n=78) vs `sole`-Baseline 8 % im selben Fenster. Das
  *binäre* „hat uns gehört"-Flag ist ein schwaches Picker-Signal; der echte
  Prädiktor ist die *graduelle* Lautstärke `psk_snr` (≥ −8 dB bei der DX → ~14 %
  Completion vs ~7,6 % grenzwertig). `psk_snr` ist deshalb jetzt ein eigener
  Tier knapp vor `snr`.
- `tail_end_target` UNTER `snr` (v0.65.2): als Entscheider nur ~3 % Completion.
  Steht jetzt als letztes Glied hinter dem `snr`-Breaker → gewinnt faktisch nur
  bei exakt gleichem SNR, also praktisch neutralisiert ohne Feature-Kill.

## Conservative Gates

Vor dem Tier-Scoring gibt es zusätzliche Hunt-Gates:

- Sole-CQ-Gate: Wenn nur ein CQ im Slot steht, wird er nur bei Award-/Kontextsignal
  oder gutem Decode-/PSK-SNR angerufen.
- Strict Mode: Nach einer schlechten Serie von Hunt-Outcomes verlangt der Picker
  temporär dieselbe Evidenz für alle Routine-Ziele.
- `hunt_profile`: `balanced`, `rate` oder `dx`. In FT4 nutzt `balanced` automatisch
  das Rate-Profil für Routine-Calls; FT8 bleibt breiter.

## Band/Mode-Autopilot

Der Autopilot ist ein vorgeschalteter Hunt-Controller. Er entscheidet nicht,
welcher CQ-Rufer gepickt wird, sondern auf welcher freigegebenen
Band/Mode-Kombination der Hunter gerade laufen soll.

Konfigurierbare Policy in `OperatingConfig` / Config-UI:

- `autopilot_enabled`: Schalter fuer den Autopilot.
- `autopilot_allowed_bands`: harte Band-Grenze. Default aktuell `["15m"]`.
- `autopilot_allowed_modes`: erlaubte Modi, typischerweise `["FT8", "FT4"]`.
- `autopilot_window_min`: lokales Messfenster fuer Decodes und Pick-Outcomes.
- `autopilot_cooldown_min`: Mindestzeit zwischen zwei Umschaltungen.
- `autopilot_min_decodes`: Decode-Dichte ab der FT4 als Rate-Mode sinnvoll ist.
- `autopilot_min_attempts` plus FT4-Completion-Schwellen: Rueckfall auf FT8,
  wenn FT4 im lokalen Fenster schlecht performed.

Safety-Gates:

- laeuft nur im Hunt-Betrieb (`auto_answer=True`, `auto_cq=False`)
- nur wenn die State-Machine `IDLE` ist
- nie waehrend PTT oder aktivem QSO
- nur auf konfigurierte, lizenz-/power-erlaubte und von der aktiven Antenne
  abgedeckte Baender
- schreibt keine Config-Datei; Runtime-Mode/Dial werden live gestellt

Entscheidungsbasis:

- echte lokale Daten: `decode`-Dichte pro Band und `pick_attempt`-Outcomes pro
  Band/Mode
- schwacher Physik-Prior: Tageszeit-/Band-Heuristik plus vorhandene
  hamqsl-Bandbedingungen, aber lokale Daten koennen den Prior ueberstimmen
- Modusregel: FT4 bei dichter Aktivitaet fuer Rate, FT8 bei schwacher Dichte
  oder schlechter FT4-Completion

## Editierung via UI

ConfigPanel → Hunt-Priorität:
- **Drag&Drop** der Reihen (☰-Handle)
- **▲/▼-Buttons** für präzise Schritte
- Änderungen werden bei „Speichern" persistiert
- Pi liest die neue Reihenfolge **pro Slot** — kein Restart nötig

## PSK-Reciprocity aktivieren

`marine_psk`, `new_dxcc_psk`, `psk_heard_us` und `psk_snr` greifen nur wenn
`OperatingConfig.psk_reciprocity_enabled = true`. Default ist **false**.

Wenn aktiviert:
- Background-Loop `_psk_reciprocity_refresh_loop` fetcht alle X Sekunden
  (Default 600 = 10 min) von pskreporter.info die Reception-Reports der
  letzten 1h für **alle** konfigurierten Operator-Calls (DK9XR + DO3XR
  bei Multi-Op)
- Result wird in `_psk_heard_us_cache: set[str]` gehalten
- Picker liest pro Slot in `ctx.psk_heard_us` und `ctx.psk_snr`
- Bei API-Fehler: alter Cache bleibt, Picker arbeitet weiter (fail-open)

## Backward-Compat

Wenn `hunt_priority` leer/None ist (alte Config ohne Migration), fällt
der Picker auf die v0.9-Logik zurück: `(is_new_dxcc, snr)` Tuple.
Kein Crash, nur weniger smart.

## Verwandte Konfig-Felder

Diese werden weiter **als Filter** ausgewertet (raus aus dem Pool **bevor**
das Tier-Scoring greift):

- `hunt_skip_worked: bool` — alle worked Calls ignorieren
- `hunt_dxcc_only: bool` — nur neue-DXCC-Calls überhaupt picken
- `hunt_snr_floor_db: int` — SNR-Mindestwert
- `hunt_audio_freq_min_hz` / `_max_hz` — Audio-Bandpass-Filter
- `hunt_profile: balanced|rate|dx` — Routine-Picks Richtung Rate oder DX
  gewichten; Balanced-FT4 nutzt automatisch das Rate-Gate
- `hunt_sole_min_snr_db` / `hunt_sole_min_psk_snr_db` — Mindest-Evidenz fuer
  einzelne Routine-CQs ohne Award-/Kontextwert
- `hunt_poor_run_window`, `hunt_poor_run_min_successes`,
  `hunt_poor_run_strict_min` — Strict-Mode-Ausloeser nach schlechter Serie
- `hunt_strict_min_snr_db` / `hunt_strict_min_psk_snr_db` — strengere
  Mindest-Evidenz waehrend Strict Mode

Die Tier-Reihenfolge entscheidet dann nur unter den **bereits gefilterten**
Kandidaten wer den Slot bekommt.

## Tests

`backend/tests/test_hunt_priority.py` — 35 Tests:
- Pro Tier: positive/negative-cases
- Aggregation + lexikographische Ordnung
- Permutation: ändert die hunt_priority den Winner?
- Edge-cases: leere Liste, unbekannte Tier-Namen, leere call_from
- DXCC-Rarity prefix-fallback
- PSK-Cache freshness + lookup
- `psk_snr`-Scoring und Validator-Migration

## Tier-Funktion hinzufügen

In `backend/ft8_appliance/statemachine/machine.py`:

```python
def _tier_my_new_tier(d, ctx):
    # 0 = no match, höher = besser
    return 1 if irgendwas else 0

HUNT_TIERS["my_new_tier"] = _tier_my_new_tier
```

Plus:
- Test in `test_hunt_priority.py`
- Optional: in Default `hunt_priority`-Liste in `config/models.py` einsortieren
- UI: `TIER_LABELS` + `TIER_ICONS` in `ConfigPanel.svelte` ergänzen

Unbekannte Tier-Namen im hunt_priority werden vom Picker still ignoriert
(kein Crash bei Tippfehler oder version-skew).
