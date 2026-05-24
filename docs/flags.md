# Country-Flag-Emojis für Callsigns

**Status:** Aktiv seit v0.3.0 (Sebastian-Request 2026-05-24).

Jedes fremde Callsign in der UI, im QSO-Log, in den Decodes und in
callsign-spezifischen ntfy-Pushes bekommt die zugehörige Landesflagge
als Unicode-Emoji vorangestellt — z.B. 🇩🇪 DL5XYZ, 🇺🇸 W1AW, 🇯🇵 JA1ABC.

## Wie es funktioniert

1. **DXCC-Lookup** via existierende `cty.dat`-Integration (siehe
   `integrations/cty_dat.py`). Liefert `DxccEntity` mit
   `primary_prefix` (z.B. "DL" für Germany, "K" für USA).
2. **DXCC → ISO-3166-1-alpha-2** über kuratierte Tabelle in
   `integrations/flags.py` (`_DXCC_PRIMARY_TO_ISO2`, ~280 Einträge,
   deckt alle aktiven DXCC-Entities ab).
3. **ISO2 → Flag-Emoji** über Unicode-Regional-Indicator-Trick:
   `"DE"` → `chr(0x1F1E9) + chr(0x1F1EA)` = 🇩🇪.

Helper-Funktion:

```python
from ft8_appliance.integrations.flags import flag_for_call

flag = flag_for_call("DL5XYZ", orch.integrations.cty)
# → "🇩🇪"
```

Returns `""` (leer) bei:
- Unbekanntem Callsign (nicht in cty.dat)
- DXCC ohne sinnvolle Flagge (Antarktis-Basen, ITU/UN-Sonder-Calls →
  `_DXCC_SKIP_FLAG`-Set)
- `cty=None` (cty.dat-Integration nicht aktiv)
- Exception im Lookup

Caller können den Leerstring sicher konkatenieren ohne Layout-Bruch.

## Wo Flaggen erscheinen

### Backend-API (neue Felder)

| Endpoint | Modell | Feld |
|---|---|---|
| `GET /api/log` | `QsoOut` | `flag` |
| `GET /api/decodes` | `DecodeOut` | `flag` |
| `GET /api/heard` | `HeardOut` | `flag` |
| `GET /api/status` | `StatusResponse` | `current_qso_flag` |
| `GET /api/qso/conversation` | `ConversationResponse` | `partner_flag` |
| `GET /api/integrations/psk/who-heard-me` | `PskHeardRow` | `flag` |

Alle Felder default `""` → backward-compatible.

### Frontend-Components

| Component | Stelle |
|---|---|
| `DecodeList.svelte` | Vor der `{d.message}`-Ausgabe in der Liste |
| `ADIFTable.svelte` | Vor dem `<span class="call">` in der Log-Tabelle |
| `WhoHeardMe.svelte` | In der Reporter-Tabelle vor dem rx_call |
| `QsoConversation.svelte` | Im Partner-Header neben dem 📡-Emoji |
| `StatusBar.svelte` | Im current-QSO-Indikator |

### ntfy-Pushes

| Push | Flag? | Begründung |
|---|---|---|
| QSO complete (`📡 QSO complete: W1AW`) | ✅ | Fremdes Call |
| New DXCC (`🆕 New DXCC! W1AW`) | ✅ | Fremdes Call |
| DX-Cluster-Hint (`🆕 DXCC-Spot: ...`) | ✅ | Fremdes Call |
| SWR/ALC/Audio-Alerts | ❌ | System, kein Call |
| GPS-Fix-Lost, CQ-Idle-Watchdog | ❌ | System |
| Power/Mode/Filter-Tamper | ❌ | System |
| Self-Update Outcomes | ❌ | System |
| Pi-Shutdown | ❌ | System |

Flag-Prepend ist im `NtfyClient.notify()` zentralisiert über den
optionalen `flag=`-Parameter; Caller berechnet das Flag via
`flag_for_call()` und übergibt's. Bei vorhandenem `title` wird das
Flag dem Titel vorangestellt (`f"{flag} {title}"`), sonst der Message.

## Mapping-Details

### Coverage

Alle ~340 aktiven DXCC-Entities sind erfasst (Stand 2026-05). Für
politisch unklare oder deleted DXCC zeigt die UI keine Flagge.

### Sonderfälle ohne Flag

```python
_DXCC_SKIP_FLAG = {
    "1A",     # Sovereign Military Order of Malta (kein Staat)
    "1S",     # Spratly Islands (disputed)
    "4U1I",   # ITU HQ Geneva
    "4U1U",   # UN HQ NY
    "CE9",    # Antarctica
    "KC4",    # US Antarctic Bases
    "VP8/G",  # South Georgia (UK)
    # ... weitere Antarktis-Claims
}
```

### Per-DXCC-vs-ISO-Konflikte (bewusst)

| DXCC | Politische Realität | Unsere Flagge |
|---|---|---|
| Alaska (KL) | US-Bundesstaat | 🇺🇸 |
| Hawaii (KH6) | US-Bundesstaat | 🇺🇸 |
| Sicily (IT9) | IT-Region | 🇮🇹 |
| Sardinia (IS) | IT-Region | 🇮🇹 |
| Madeira (CT3) | PT-Region | 🇵🇹 |
| Azores (CU) | PT-Region | 🇵🇹 |
| Canary Is (EA8) | ES-Region | 🇪🇸 |
| Jersey (GJ) | Crown Dependency | 🇯🇪 (eigene ISO) |
| Isle of Man (GD) | Crown Dependency | 🇮🇲 (eigene ISO) |
| Northern Ireland (GI) | UK-Konstituent | 🇬🇧 (ENG ist DXCC, ISO nur GB) |
| Scotland (GM) | UK-Konstituent | 🇬🇧 |
| Wales (GW) | UK-Konstituent | 🇬🇧 |

Konvention: **lokale ISO wenn vorhanden** (Jersey, IoM), **sonst
Mutterland** (UK-Konstituente, US-Bundesstaaten, IT/ES/PT-Regionen).
Damit sind die Flaggen visuell wiedererkennbar — die exakte DXCC steht
ja im Call selbst.

## Rendering-Hinweise

* **Browser:** Unicode-Flag-Emojis funktionieren in allen modernen
  Browsern via System-Emoji-Font (Apple Color Emoji, Noto Color Emoji,
  Segoe UI Emoji). Windows-Browser **vor** Windows 11 zeigen ggf. nur
  Buchstaben-Paare (Regional Indicator Glyphs) statt Flaggen — sieht
  unsexy aus aber bricht nichts.
* **ntfy-Mobile-App:** rendert Flaggen nativ über die OS-Emoji-Engine.
* **Linux-Terminal (journalctl):** zeigt Flaggen wenn Noto Color Emoji
  installiert ist (Standard auf modernem Debian 12+). Sonst Buchstaben-
  Paare als Fallback.

## Tests

`backend/tests/test_flags.py` (17 Tests):

- ISO2-to-Flag-Conversion (DE/US/JP/lowercase/invalid)
- DXCC-zu-ISO2-Mapping (Top-45-Entities sanity-checked)
- Skip-List (Antarktis/UN/ITU)
- Prefix-Fallback (längere Prefixe fallen auf kürzere zurück)
- End-to-end flag_for_call mit mini-cty.dat (DL/K/JA/EA)
- Edge-cases (None-inputs, unbekannte Calls, Exception im Lookup)

## Pflege

Wenn neue DXCC dazukommen (sehr selten — Z8 South Sudan war die letzte
Major-Addition 2011):

1. Eintrag in `_DXCC_PRIMARY_TO_ISO2` ergänzen
2. Test im `must_have`-Dict in `test_flags.py` ergänzen
3. Doku-Tabelle updaten falls Sonderfall

Wenn ISO selbst sich ändert (sehr selten — Eswatini=SZ war 2018):

1. Eintrag korrigieren
2. Tests laufen lassen
