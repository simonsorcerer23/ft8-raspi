# CEPT-Länderliste — wo dürfen Klasse A und E funken?

Auto-generiert aus `backend/ft8_appliance/integrations/cept.py` (Quelle: ARRL/CEPT, Stand Mai 2023). Bei Reise sicherheitshalber prüfen.

## Hintergrund

- **Klasse A** (Volllizenz) = CEPT-1 (T/R 61-01) → Gastbetrieb in allen unten gelisteten Ländern.
- **Klasse E** (Novice) = CEPT-Novice (ECC/REC (05)06) → NUR in der Teilmenge die diese Empfehlung umsetzt.
- **Klasse N** (Einsteiger seit 24.06.2024) = international NICHT anerkannt → Auslandsbetrieb generell gesperrt.
- Format im Betrieb: `<Prefix>/<Heimat-Call>`, z.B. `9A/DK9XR` in Kroatien, `W/DO3XR` in den USA.

## ✅ Klasse E + A erlaubt (CEPT-Novice, 25 Länder)

| Land | Prefix | Klasse-A Power-Cap |
|---|---|---|
| Belarus | `EW` | 1000 W |
| Belgien | `ON` | 1500 W |
| Bosnien-Herzegowina | `E7` | 1000 W |
| Dänemark | `OZ` | 1000 W |
| Finnland | `OH` | 1500 W |
| Hawaii | `KH6` | 1500 W |
| Island | `TF` | 1000 W |
| Kroatien | `9A` | 1000 W |
| Lettland | `YL` | 1000 W |
| Liechtenstein | `HB0` | 1000 W |
| Litauen | `LY` | 1000 W |
| Luxemburg | `LX` | 1000 W |
| Moldau | `ER` | 1000 W |
| Niederlande | `PA` | 400 W |
| Polen | `SP` | 750 W |
| Portugal | `CT` | 1500 W |
| Rumänien | `YO` | 1500 W |
| Russland | `UA` | 1000 W |
| Schweiz | `HB9` | 1000 W |
| Slowakei | `OM` | 1500 W |
| Slowenien | `S5` | 1000 W |
| Tschechien | `OK` | 1000 W |
| USA | `W` | 1500 W |
| Ungarn | `HA` | 1500 W |
| Österreich | `OE` | 400 W |

## 🅰️ NUR Klasse A (CEPT-1-only — Klasse E hier GESPERRT, 18 Länder)

| Land | Prefix | Klasse-A Power-Cap |
|---|---|---|
| Albanien | `ZA` | 1000 W |
| Bulgarien | `LZ` | 1500 W |
| Estland | `ES` | 1000 W |
| Frankreich | `F` | 500 W |
| Griechenland | `SV` | 1000 W |
| Großbritannien | `G` | 400 W |
| Irland | `EI` | 400 W |
| Italien | `I` | 500 W |
| Monaco | `3A` | 500 W |
| Montenegro | `4O` | 1000 W |
| Nordmazedonien | `Z3` | 1000 W |
| Norwegen | `LA` | 1000 W |
| Schweden | `SM` | 1000 W |
| Serbien | `YU` | 1000 W |
| Spanien | `EA` | 1500 W |
| Türkei | `TA` | 1000 W |
| Ukraine | `UR` | 1000 W |
| Zypern | `5B` | 1000 W |

## Hinweise

- **USA/Hawaii**: Klasse A via FCC-CEPT (DA-16-1048). **Klasse E ebenfalls erlaubt** — die USA ist ECC/REC (05)06 beigetreten, deutsche Klasse E (CEPT-Novice) darf dort operieren (47 CFR §97.107), limitiert auf Klasse-E-Bänder (80/15/10m + 2m/70cm) ∩ US-Zuteilungen, max 100W. FT8 auf 15m ist abgedeckt.
- **Power-Caps** sind für Klasse A; für Klasse E greift der nationale 100-W-Cap. Bei IC-705/IC-7300 bindet ohnehin der Rig-Cap (10/100 W) — die Länder-Caps sind nie limitierend.
- **Hart geblockt**: Die Box verweigert TX wenn die Lizenzklasse im gewählten Land nicht erlaubt ist (gleiche Mechanik wie Band-/Power-Lockout).
- **GPS-Detection** ist ein Vorschlag — bei verschachtelten Balkan-Grenzen (Kroatien/Bosnien) kann das Rechteck mehrdeutig sein; der Operator bestätigt den Wechsel manuell.
