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

## Deployment-Voraussetzung: `data/cty.dat`

Die `cty.dat`-Datei (~100 KB, von country-files.com) ist **gitignored**
und muss **manuell** im Repo-Pfad `data/cty.dat` deployed werden — sie
ist Runtime-Data, kein Source-Code, und ändert sich periodisch
(Quartal/Jahres-Updates).

Wenn die Datei fehlt, läuft der Service problemlos weiter, aber:
- `orch.integrations.cty` ist `None`
- `flag_for_call()` liefert immer `""` (leerer String)
- Flaggen sind in UI/Log/Decodes/ntfy **unsichtbar**

**Fix bei frischem Pi-Setup oder fehlender Datei:**

```bash
# Aus aktuellem Backup (falls vorhanden):
cp /home/sebastian/ft8-appliance.rsync-backup-*/data/cty.dat \
   /home/sebastian/ft8-appliance/data/cty.dat

# ODER von country-files.com nachziehen:
curl -L -o /home/sebastian/ft8-appliance/data/cty.dat \
   "https://www.country-files.com/cty/cty.dat"

# Service neu starten damit's geladen wird:
sudo systemctl restart ft8-controller

# Verify im Log: "cty.dat loaded (4482 entries)"
sudo journalctl -u ft8-controller --since "30 sec ago" | grep cty
```

Self-Updates lassen `data/cty.dat` unangetastet (untracked file, git
checkout berührt's nicht). Datei einmal deployen → reicht bis zur
nächsten cty-Version.

## Pflege

Wenn neue DXCC dazukommen (sehr selten — Z8 South Sudan war die letzte
Major-Addition 2011):

1. Eintrag in `_DXCC_PRIMARY_TO_ISO2` ergänzen
2. Test im `must_have`-Dict in `test_flags.py` ergänzen
3. Doku-Tabelle updaten falls Sonderfall

Wenn ISO selbst sich ändert (sehr selten — Eswatini=SZ war 2018):

1. Eintrag korrigieren
2. Tests laufen lassen


### `operating.rig_auto_restore` (2026-09-06, Default `false`)

Sieht die Tamper-Erkennung eine fremde Betriebsart (nicht PKTUSB) oder eine Filterbreite unter 2000 Hz, setzt die App nach spätestens 15 s Betriebsart, 2700 Hz und den konfigurierten Dial des aktuellen Bands zurück, nie während eines eigenen Bursts und nie die Leistung. Ohne das Flag gibt es dafür den Button „Rig zurücksetzen“ im Steuerpanel (`POST /api/control/restore-rig`). Anlass: Bedienung am IC-7300 mit USB und 350-Hz-Filter.


### Antwortstrategie (2026-09-07): `hunt_weak_requires_psk`, `hunt_weak_snr_db`, `hunt_cq_fallback`, `hunt_cq_fallback_after_slots`, `hunt_reply_ab_test`

Aus der Pick-Telemetrie des 6.9. (381 Picks, 7 % vollendet): Ziele unter −13 dB kamen zu 3 % zurück, −13…−8 dB zu 12 %; fast die Hälfte der Sendezeit ging an Grenzfälle. Deshalb, alle Default `true`:

- `hunt_weak_requires_psk` (Schwelle `hunt_weak_snr_db`, Default −13): schwächere Rufer werden nur angerufen, wenn PSK Reporter sie in `psk_heard_us` führt.
- `hunt_cq_fallback` (nach `hunt_cq_fallback_after_slots`, Default 2, Slots ohne brauchbaren Rufer): die Box ruft selbst CQ, bis der Picker wieder etwas findet; ein brauchbarer Rufer hat Vorrang vor dem eigenen CQ. Zähler `cq_fallback_starts` / `cq_fallback_qsos` in `/api/status`.
- `hunt_cq_fallback_max_cqs` (Default 20) / `hunt_cq_fallback_pause_min` (Default 10): nach so vielen unbeantworteten Fallback-CQs geht die Box für die Pause zurück ins reine Hunting. Der „CQ-Idle ohne Antwort“-Push bleibt im Fallback stumm; er gilt nur für den von Hand gestarteten CQ-Modus.
- `hunt_reply_ab_test`: Antworten abwechselnd auf dem ruhigen Bin und auf der Rufer-Frequenz; `pick_attempt.reply_kind` und `/api/stats/pick-attempts` → `by_reply_kind` liefern die Vollendungsquote je Variante. Abschalten, sobald eine Variante gewonnen hat, und `hunt_reply_quiet_freq` entsprechend setzen.


### Automatischer Bandwechsel (vorbereitet 2026-09-07)

Der Hunt-Autopilot (`autopilot_enabled`, `autopilot_allowed_bands`, Fenster `autopilot_window_min`, Sperre `autopilot_cooldown_min`) bewertet alle 15 Minuten die erlaubten Bänder nach Decodes, Anrufversuchen, Vollendungen, einer Tageszeit-Prior je Band und den hamqsl-Bandbedingungen und wechselt auf das beste Band/Modus-Paar. Neu: Die Antenne trägt `auto_band_switch` (Häkchen „Auto-Band" in der Antennenzeile). Ohne das Häkchen bleibt der Autopilot auf dem Band, auf dem das Rig steht (Dipol mit Tuner-Zwang, wie derzeit `spitzwegstrasse`), nur der Moduswechsel FT8/FT4 bleibt möglich. Mit Häkchen (Multiband-Antenne ohne Abstimmung) wechselt er innerhalb der Bänder, die die Antenne abdeckt und die in `autopilot_allowed_bands` stehen.

Scharfschalten, sobald die Multiband-Antenne hängt: Häkchen an der Antenne setzen, `autopilot_allowed_bands` prüfen, `autopilot_enabled: true`.


### Nachtrag 2026-09-08: adaptiver Fallback, Wiederholungen, Kontinent-Gate

- `hunt_cq_fallback_pause_max_min` (Default 60): jede Fallback-Runde ohne Antwort verdoppelt die Pause (10, 20, 40, 60 min); die erste Antwort auf ein Fallback-CQ setzt zurück. Anlass: nachts 83 Starts, 0 QSOs.
- `qso_max_cq_resends` Default 2 → 1: Telemetrie 0 Wiederholungen 7 %, 1 → 10 %, 2+ → 5 % Vollendung; die zweite Wiederholung kostet nur einen Burst.
- `hunt_continent_gate` / `hunt_continent_gate_pct` (Default an, 5 %): Rufer aus einem Kontinent, dessen Vollendungsquote in der eigenen Telemetrie (14 Tage, ≥ 20 Picks) unter der Schwelle liegt, werden nur angerufen, wenn PSK Reporter sie als „hört uns“ führt. Am 8.9.: EU 18 %, AS 7 %, NA 3 %. Nachteil bewusst in Kauf genommen: die seltenen NA-QSOs bei Nacht.


### Wunschliste im Picker (2026-09-10)

Die Watchlist tat bis v0.86.0 zwei Dinge — Push aufs Handy und Rufzeichen an den Hint-Decoder —, kam in der Auswahl aber nicht vor: `ctx.watchlist_calls` wurde befüllt und von niemandem gelesen. Zugleich fliegen Pile-Up-Stationen **hart** aus der Kandidatenliste, bevor die Priorisierung anläuft. Seltenes DX hat per Definition Pile-Up, die Liste war damit für genau die Stationen wirkungslos, für die man sie anlegt. Gemessen über sieben Tage: Z68PX (Kosovo) rief 16-mal CQ, kein einziger Anrufversuch; V51WH (Namibia) 13 Decodes, nie versucht.

Seit v0.87.0 gilt eine Trennlinie. Übersteuert werden für Stationen der Wunschliste die Gates, die fragen **„lohnt sich das?"** — Pile-Up-Filter, SNR-Floor (`hunt_snr_floor_db`), Kontinent-Quote (`hunt_continent_gate`) und Schwach-Gate (`hunt_weak_requires_psk`). Wer eine Station einträgt, hat diese Abwägung schon getroffen, und seltenes DX ist praktisch immer schwach *und* umlagert. Bestehen bleiben die Gates, die sagen **„geht technisch nicht"** — DT außerhalb des Empfangsfensters, gleiche Slot-Parität — sowie die ausdrückliche Sperre der Soft-Blacklist, die schwerer wiegt als der Wunsch.

Die Liste erkennt volle Rufzeichen (`Z68PX`) und Präfixe (`KH8`, `VP5`), weil beim Import aus dem NG3K-Kalender oft nur das Präfix feststeht.

### Telemetrie eingehender Anrufe (2026-09-10)

`pick_attempt` erfasste bis v0.86.0 ausschließlich Stationen, die der Picker selbst angerufen hat — `hunt_attempt_meta` füllt nur er. Antwortete jemand auf unser CQ, lief das QSO ohne jede Messung durch: ausgerechnet der Fall, in dem die Sequenz-Löcher steckten. Seit v0.87.0 wird auch dieser Weg erfasst, erkennbar an `pick_kind`:

* `inbound_grid` — sie riefen uns mit ihrem Locator
* `inbound_report` — sie riefen uns direkt mit Report (Tail-Ender)
* `inbound_resume` — verspätete Fortsetzung aus dem Nachklang-Speicher
* `cq` / `to_us` / `to_other` — wie bisher, vom Picker gewählt

Damit lassen sich Abschlussquoten getrennt auswerten: eingehend gegen selbst gerufen.

### Sendeleistung nach einem Neustart (geändert 2026-09-09)

Die zuletzt eingestellte Leistung liegt in `runtime_state.json` und überlebt jetzt einen Neustart. Bis v0.84.3 kam die Station mit der halben Leistung hoch — beim Start ist das Band noch unbekannt, der Sicherheits-Floor griff auf `effective_max/2`, und der erste Bandaufschlag warf denselben Floor gleich nochmal (bei Raymonds IC-7300: 70 → 50 W nach jedem Self-Update).

Was bleibt: Der gemerkte Wert wird weiterhin begrenzt, aber auf das, was **Lizenzklasse und Rig auf diesem Band zulassen**, statt auf die Hälfte davon. Echte Bandwechsel danach, Operator- und Rig-Wechsel klemmen unverändert auf den Vorsichtswert; gesendet wird bis zum ersten Rig-Kontakt ohnehin nicht (`rig_link_guard`). Ohne gemerkten Wert — erster Start, gelöschter Zustand — greift der Vorsichtswert wie bisher.

### `boot_mode` (Default `off`, seit 2026-09-09 auch `cq+hunt`)

Welche Modi nach einem Neustart wieder anlaufen. Der Wert wird nicht von Hand gepflegt, sondern vom Orchestrator aus dem tatsächlichen Zustand beider Schalter fortgeschrieben: `auto_cq` (selbst CQ rufen) und `auto_answer` (Hunting — aktiv nach Anrufern suchen, Tail-Ending, Vorrang des Antwortens im CQ-Fallback).

Bis v0.84.2 schrieb jede Bedienhandlung einen festen Wert: CQ-Start `cq`, der Hunting-Schalter `hunt`, Stop `off`. Zwei unabhängige Schalter in einem Feld — die letzte Handlung löschte die Erinnerung an die andere. Wer erst CQ startete und dann Hunting einschaltete, kam nach dem nächsten Neustart in CQ hoch, aber ohne Hunting. Aufgefallen ist das nach dem Multicore-Update: Die Station rief weiter CQ und bediente auch, wer ihr antwortete (dieser Pfad hängt nicht an `auto_answer`) — aber sie suchte nicht mehr selbst nach Anrufern. Seit v0.84.3 wird der Wert abgeleitet, `cq+hunt` deckt beides ab.

Kein Eingabefeld in der Oberfläche; der Wert steht in der `config.yaml` und wird automatisch gepflegt.

### `decoder_late_pass` (2026-09-06, Default an — Pi 5 seit 2026-09-09: aus)

Zweistufiger Decoder: Stufe 1 ist der schnelle Standard-Pass und entscheidet über den Sendestart; der Rest des gewählten `decoder_mode` (Deep, Subtraktion, Hint-Pass, OSD, Feinsync) läuft als Stufe 2 nebenher und reicht nach, was er zusätzlich findet — zu spät für eine Antwort im selben Slot. Auf dem Pi 4B nötig, weil `extreme` dort 2,8 s vor der Sendeentscheidung kostete. Seit die Kandidatenschleifen parallel laufen (v0.84.0), braucht `extreme` am Pi 5 live 0,2–0,7 s; die Station fährt darum `decoder_late_pass: false` — der volle Modus in Stufe 1, jt9 bleibt Stufe 3. Reißt ein dichter Slot dreimal in Folge `tx_latency_max_s`, stellt der Orchestrator zuerst die Zweiteilung wieder her und fällt erst danach auf `standard`. Status: `decoder_late_pass.stage1_last_s` / `stage1_avg_s` / `two_stage`, `decoder_pass_stats.threads`. Messung: `docs/decoder_evolution.md`.

### `decoder_jt9` / `decoder_jt9_depth` (2026-09-08, Default an / 2)

Stufe 3 des Decoders: WSJT-X' `jt9` (Paket `wsjtx`) decodiert den Slot parallel zu Stufe 2. Tiefe 2 braucht am Pi 4B 6 s (max 9 s) und trifft 97,5 % der WSJT-X-Decodes; Tiefe 3 wäre am 4B 11 bis 17 s und passt dort nicht in den Slot; am Pi 5 braucht sie 4,3 s im Korpus und 5,8 s live und ist seit 2026-09-09 dauerhaft aktiv. Ohne installiertes `jt9` bleibt die Stufe still. Status: `decoder_late_pass.jt9` (last/total/duration_s/skipped/failed).


### jt9-Feinheiten (2026-09-08): `decoder_jt9_boost_in_qso`, `decoder_jt9_ft4`, `decoder_jt9_ap`, `decoder_jt9_ap_flags`

- `decoder_jt9_boost_in_qso` (Default an): im QSO und beim CQ-Rufen senden wir jeden zweiten Slot, jt9 hat dann 30 s bis zur nächsten Entscheidung und läuft in Tiefe 3 (Korpus: +1,4 Punkte). Überläuft Tiefe 3 trotzdem, 10 Minuten zurück auf Tiefe 2.
- `decoder_jt9_ft4` (Default an): jt9 mit `-5` auf dem 7,5-s-Slot; gemessen an synthetischen Slots 5/5 Decodes in 0,1 s (x86). jt9 will für FT4 genau 7,5 s Audio, 15 s liefern nichts.
- `decoder_jt9_ap` / `decoder_jt9_ap_flags` (Default an, Flags 1): eigener Call/Grid (`-c`/`-G`) und im QSO der Partner (`-x`/`-g`) plus `-X 1` an jt9, also WSJT-X' eigene AP-Decodierung. Messung 8.9. (synthetisch, Antwort an uns unter der BP-Grenze): `-X 1` findet 2 von 6, `-X 2/3/7` nichts; unsichere AP-Decodes (`?`-Marke) werden verworfen. Das Memo gegen AP-Eigenbau bleibt: das hier ist K1JTs Implementierung, nicht unsere.

### `decoder_pre_decode` (2026-09-11, Vorgabe aus)

Der Decoder beginnt sonst erst an der Slot-Grenze und braucht 0,5–1,0 s.
Die Sendung geht dadurch im Mittel **1,07 s** nach der Grenze raus, während
die Stationen, die wir hören, bei **+0,12 s** liegen — wir kommen also fast
eine Sekunde später an als der Durchschnitt. FT8-Decoder suchen ±2,5 s um
die Grenze, es geht nichts verloren; aber die Marge fehlt bei schwachen
Signalen.

Vor dem Umbau wurde gemessen, wo die Zeit hingeht (`slot_phasen_s` im
Status):

| Abschnitt | Dauer |
|---|---|
| Decode | 0,50–1,16 s |
| Hardware-Abfrage | 0,010 s |
| Kontext-Aufbau | 0,000 s |
| Veröffentlichen (SSE, DB, PSK) | 0,000–0,024 s |
| Zustandsmaschine | 0,000 s |

Damit waren drei Verdächtige erledigt: Die Hardware ist nicht limitiert
(2,4 GHz, kein Throttling), der adaptive LDPC-Faktor ist seit v0.89 bereits
auf 150 % gedeckelt, und die 2,36 s mitdekodierte Stille kosten nichts
Sparbares — `decode_slot` erwartet exakt 180 000 Samples, ein kürzeres
Fenster müsste man mit Nullen auffüllen.

**Wie es funktioniert:** FT8-Sendungen enden nach 12,64 s. Wer pünktlich
sendet, ist lange vor der Grenze vollständig im Ringpuffer. Der
Vorab-Durchgang läuft `decoder_pre_decode_lead_s` (Vorgabe 1,3 s) vor der
Grenze — mit einem Tick, dessen `posix` die *kommende* Grenze trägt. Damit
rechnet `slot_start_posix` von selbst richtig, und `extract_slot` nullt den
noch fehlenden Rest. Kein Eingriff in die Audio-Kette nötig.

**Was getrennt bleibt:** keine Dedup-Tabelle, keine Slot-Metrik, kein
Stufe-2- oder jt9-Anstoß, keine Notch-Aktualisierung (bekannte Störlinien
werden aber gefiltert). Findet der Vorab-Durchgang nichts, entscheidet der
reguläre wie bisher — es kann nur früher werden, nie schlechter.

**Nicht vergessen:** Die Aussendung muss die Slot-Grenze abwarten. Steht die
Entscheidung eine Sekunde zu früh und wird sofort gesendet, ragt der Burst in
den laufenden Slot — live sichtbar als Sendeversatz von 13,9 s statt 0,9 s.
`_do_tx_message` wartet daher die letzten Zehntel ab (Rest ≤ 2,5 s). Dieses
Warten ist der eigentliche Gewinn des Vorab-Decodes.

**Preis:** Stationen mit größerem Zeitversatz als der Vorlauf sind im
frühen Durchgang noch nicht vollständig und fallen dort heraus. Sie kommen
weiterhin über den regulären Durchgang ins Log, treffen aber die
Sendeentscheidung nicht mehr mit. Bei 1,3 s Vorlauf betrifft das nach
eigener Messung rund 8 % der Decodes.

### `decoder_pre_decode_ab` (2026-09-11)

Lässt die Betriebsart **slotweise** wechseln, nicht tageweise. Damit laufen
beide Wege unter denselben Bandbedingungen, und der Vergleich misst den
Umbau statt der Ausbreitung — der Fehler, der bei der Auto-CQ-Auswertung
desselben Tages noch unterlaufen war. `pick_attempt.pre_decode` hält je
Anruf fest, aus welchem Durchgang die Entscheidung kam:

```sql
select pre_decode, count(*), sum(outcome='completed'),
       round(100.0*sum(outcome='completed')/count(*),1)
from pick_attempt where pre_decode is not null group by 1;
```

