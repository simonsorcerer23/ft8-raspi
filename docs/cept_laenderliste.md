# CEPT-Länderliste — wo dürfen Klasse A und E funken?

**Auto-generiert** aus `backend/ft8_appliance/integrations/cept.py`.

**Primärquelle:** DARC „Countries with CEPT Licence" (Hans Schwarz DK5JI,
dk5ji@darc.de), Stand **2026-05-16**, CC BY-NC-ND 4.0 — gepflegt aus den
offiziellen ECC/CEPT-Dokumenten (T/R 61-01 + ECC/REC (05)06) und den
nationalen Verordnungen. Jede Zeile ist gegen die Spalte
**„Short-term WITHOUT guest licence" (Full / Novice)** der jeweiligen
Länderseite verifiziert. Nichts ist geschätzt.

## Hintergrund

- **Klasse A** (Volllizenz) = CEPT-1 (T/R 61-01) → Gastbetrieb dort wo
  Spalte *Full* = x.
- **Klasse E** (Novice) = CEPT-Novice (ECC/REC (05)06) → Gastbetrieb dort
  wo Spalte *Novice* = x — eine **echte Teilmenge** der A-Länder.
- **Klasse N** (Einsteiger seit 24.06.2024) = international NICHT anerkannt
  (keine ECC-Empfehlung dazu) → Auslandsbetrieb generell gesperrt.
- **Power** = dokumentierte Full-Leistung (PEP) auf **15m (21 MHz)** aus
  der jeweiligen Band-Tabelle des DARC-PDF. Bei IC-705/IC-7300 bindet
  ohnehin der Rig-Cap (10/100 W) — die Länder-Caps sind nie limitierend.
- Format im Betrieb: `<Prefix>/<Heimat-Call>`, z.B. `9A/DK9XR` in Kroatien,
  `W/DO3XR` in den USA.

## ✅ Klasse E + A erlaubt (CEPT-Novice, 27 Länder)

| Land | Prefix | 15m Full (Klasse A) |
|---|---|---|
| Belgien | `ON` | 1500 W |
| Bosnien-Herzegowina | `E7` | 1500 W |
| Dänemark | `OZ` | 1000 W |
| Finnland | `OH` | 1500 W |
| Georgien | `4L` | 1600 W |
| Hawaii | `KH6` | 1500 W |
| Island | `TF` | 1000 W |
| Kroatien | `9A` | 1500 W |
| Lettland | `YL` | 1000 W |
| Liechtenstein | `HB0` | 1000 W |
| Litauen | `LY` | 1000 W |
| Luxemburg | `LX` | 100 W |
| Moldau | `ER` | 100 W |
| Montenegro | `4O` | 1500 W |
| Niederlande | `PA` | 400 W |
| Nordmazedonien | `Z3` | 1500 W |
| Polen | `SP` | 500 W |
| Portugal | `CT` | 1500 W |
| Rumänien | `YO` | 200 W |
| Schweiz | `HB9` | 1000 W |
| Slowakei | `OM` | 750 W |
| Slowenien | `S5` | 1500 W |
| Tschechien | `OK` | 750 W |
| USA | `W` | 1500 W |
| Ukraine | `UR` | 200 W |
| Ungarn | `HA` | 1500 W |
| Österreich | `OE` | 200 W |

## 🅰️ NUR Klasse A (CEPT-1-only — Klasse E hier GESPERRT, 15 Länder)

| Land | Prefix | 15m Full (Klasse A) |
|---|---|---|
| Bulgarien | `LZ` | 350 W |
| Estland | `ES` | 1000 W |
| Frankreich | `F` | 500 W |
| Griechenland | `SV` | 500 W |
| Großbritannien | `G` | 1000 W |
| Irland | `EI` | 400 W |
| Italien | `I` | 500 W |
| Malta | `9H` | 400 W |
| Monaco | `3A` | 100 W |
| Norwegen | `LA` | 1000 W |
| Schweden | `SM` | 200 W |
| Serbien | `YU` | 1500 W |
| Spanien | `EA` | 1000 W |
| Türkei | `TA` | 400 W |
| Zypern | `5B` | 400 W |

## ⛔ CEPT-Mitgliedschaft ausgesetzt (Gastbetrieb derzeit gesperrt)

Im DARC-PDF mit `**` markiert. Recommendation-Spalten wären x/x, aber die
Mitgliedschaft ist suspendiert → die Box blockt TX für **jede** Klasse.

| Land | Prefix | 15m Full (Klasse A) |
|---|---|---|
| Belarus | `EW` | 100 W |
| Russland | `UA` | 1000 W |

## ❌ Kein CEPT-Drop-in (Gast-Lizenz erforderlich)

Diese Länder sind in der Spalte *Full* = **−** (auch Klasse A braucht eine
vorab beantragte individuelle Gast-Lizenz). Sie sind **bewusst nicht** in
der Box-Auswahl — wer dorthin will, muss vorher eine Lizenz beantragen:
**Albanien (ZA), Andorra (C3), Aserbaidschan (4J), San Marino (T7),
Vatikan (HV)**.

## Hinweise

- **USA/Hawaii**: DARC führt die USA als „ITU Region 2" (umfasst
  conterminous states + Alaska + Hawaii). Short-term w/o guest:
  Full = x **und** Novice = x → Klasse A **und** Klasse E erlaubt.
  Rechtsgrundlage 47 CFR §97.107: Privilegien = Heimat-Lizenz-Bedingungen
  (Klasse-E-Bänder, max 100 W) ∩ US-Zuteilungen. FT8 auf 15m ist abgedeckt.
- **Power-Caps** sind für Klasse A (15m Full). Für Klasse E greift national
  meist 100 W; bei IC-705/IC-7300 bindet ohnehin der Rig-Cap.
- **Hart geblockt**: Die Box verweigert TX wenn die Lizenzklasse im
  gewählten Land nicht erlaubt ist (gleiche Mechanik wie Band-/Power-Lockout).
- **GPS-Detection** ist ein Vorschlag — bei verschachtelten Balkan-Grenzen
  (Kroatien/Bosnien) kann das Rechteck mehrdeutig sein; der Operator
  bestätigt den Wechsel manuell.
