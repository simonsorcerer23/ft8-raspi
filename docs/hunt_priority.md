# Hunt-Priority-Tiers (v0.10.0)

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
        │          │  new_dxcc_band → 0/1  │
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
| `snr` | Signal-to-Noise Ratio in dB | int | aus Decode-Pipeline |

## Default-Reihenfolge

Aus `OperatingConfig.hunt_priority`:

1. `marine_psk` — Marinefunker mit PSK-Trust
2. `marine` — Marinefunker (auch ohne PSK)
3. `tail_end_target` — Tail-End-Pick nach Closing-Detect (v0.11.0)
4. `new_dxcc_psk` — Neues DXCC mit PSK-Trust
5. `new_dxcc` — Neues DXCC (auch ohne PSK)
6. `psk_heard_us` — PSK-Asymmetrie für routine-EU-Stationen
7. `new_dxcc_band` — 5BWAS-Award-Tracking
8. `new_grid` — Neues Maidenhead-Grid (VUCC)
9. `new_grid_band` — Grid auf diesem Band noch nicht (VUCC-Band)
10. `not_worked` — Neue Calls (auch routine)
11. `dxcc_rarity` — Rarity-Bonus
12. `snr` — Tie-Breaker (bestes Signal gewinnt)

**Begründung der Default-Reihenfolge** (Sebastian-Wunsch 2026-05-26):
- Marinefunker top weil persönliche Community (Raymond ist Mitglied)
- PSK-bestätigte DXCCs darüber, weil Asymmetrie-Trust den Reply-Erfolg sichert
- Pure-PSK-Tier darüber als Allgemein-Asymmetrie-Boost
- 5BWAS vor `not_worked` weil Band-Variation für Awards mehr Wert hat als Routine-First-Contact

## Editierung via UI

ConfigPanel → Hunt-Priorität:
- **Drag&Drop** der Reihen (☰-Handle)
- **▲/▼-Buttons** für präzise Schritte
- Änderungen werden bei „Speichern" persistiert
- Pi liest die neue Reihenfolge **pro Slot** — kein Restart nötig

## PSK-Reciprocity aktivieren

`marine_psk`, `new_dxcc_psk` und `psk_heard_us` greifen nur wenn
`OperatingConfig.psk_reciprocity_enabled = true`. Default ist **false**.

Wenn aktiviert:
- Background-Loop `_psk_reciprocity_refresh_loop` fetcht alle X Sekunden
  (Default 600 = 10 min) von pskreporter.info die Reception-Reports der
  letzten 1h für **alle** konfigurierten Operator-Calls (DK9XR + DO3XR
  bei Multi-Op)
- Result wird in `_psk_heard_us_cache: set[str]` gehalten
- Picker liest pro Slot in `ctx.psk_heard_us`
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

Die Tier-Reihenfolge entscheidet dann nur unter den **bereits gefilterten**
Kandidaten wer den Slot bekommt.

## Tests

`backend/tests/test_hunt_priority.py` — 27 Tests:
- Pro Tier: positive/negative-cases
- Aggregation + lexikographische Ordnung
- Permutation: ändert die hunt_priority den Winner?
- Edge-cases: leere Liste, unbekannte Tier-Namen, leere call_from
- DXCC-Rarity prefix-fallback
- PSK-Cache freshness + lookup

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
