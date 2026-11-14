# TX-Power Safety-Floor

**Status:** Aktiv seit v0.2.3 (Sebastian-Request 2026-05-24). Boot-Verhalten
geändert am 2026-09-09, Bandwechsel und Operator-/Rig-Wechsel am 2026-11-14
(v0.195.0). Code: `Orchestrator._apply_tx_power_safety_floor` und
`_bandwechsel_pruefen` in `backend/ft8_appliance/runtime/orchestrator.py`.

## Was es macht

Bei jedem **„Reset-Event"** wird die TX-Leistung auf einen sicheren
Default-Wert zurückgeschoben — aber nur als **Floor**, nicht als
Override. Wer freiwillig QRP fährt, bleibt QRP.

```
safe_default = max(1, effective_max_power_w(band) // 2)
```

* `effective_max_power_w(band)` = `min(license_cap, rig_hardware_max)` —
  derselbe Pfad den auch der Power-Slider zur Begrenzung nutzt
  (`config/models.py::AppConfig.effective_max_power_w`).
* `// 2` weil „halbe Maximalleistung" als universeller Safety-Default
  taugt: weit weg von Endstufen-Sättigung, sauberes Signal, deutliche
  Reserve nach oben für bewusstes Hochregeln.
* `max(1, …)` damit nie 0 W rauskommt (bei rig.max=1 W wäre safe sonst
  `1//2 = 0`).

**Ausnahme seit 2026-09-09: die gemerkte Leistung.** Die zuletzt eingestellte
Leistung steht in `runtime_state.json` (`tx_power_w`) und wird beim Start
geladen. Dieser Wert bleibt beim Reset-Event `boot` und beim ersten
Bandaufschlag danach stehen. Begrenzt wird er nur auf das, was Lizenzklasse
und Rig auf dem Band zulassen (`_compute_band_max_power_w`), nicht auf die
Hälfte davon. Vorher kam die Station nach jedem Neustart und jedem
Self-Update mit dem Vorsichtswert hoch (am IC-7300 der Station: 70 → 50 W).
Ab dem zweiten Bandwechsel gilt wieder der Vorsichtswert. Ohne gemerkten
Wert (erster Start, gelöschter Zustand, Wert außerhalb 1 W bis Rig-Maximum)
greift der Vorsichtswert wie bisher schon beim Start.

## Variante B: Clamp-Down-Only

Aus drei diskutierten Varianten hat Sebastian (B) gewählt:

| Variante | Verhalten | Status |
|---|---|---|
| (A) Hart immer auf safe-default setzen | Jeder Reset überschreibt User-Setting auch nach unten | nicht gewählt |
| **(B) Floor: nur runter, nie hoch** | Wenn aktuell ≤ safe → lassen. Wenn > safe → runter clampen | **aktiv** |
| (C) Per-Band-Memory | Beim ersten Mal pro Band safe, danach letzten User-Wert wiederherstellen | nicht gewählt (zu viel State) |

**Konkret heißt das:**
- Du hast 80 W auf 15m → Bandwechsel auf 10m → Power geht auf 50 W (oder
  was der 10m-Cap erlaubt / 2).
- Du fährst bewusst 5 W QRP → Bandwechsel passiert → Power bleibt 5 W.
- Auf einem Band mit Cap 15 W EIRP (z.B. A-Klasse 60m) → safe = 7 W.

## Welche Events triggern

| Event | Wo im Code | Trigger-Reason |
|---|---|---|
| **Boot / Service-Restart** | `Orchestrator.start()` nach `_load_runtime_state()` | `"boot"` |
| **Operator-Wechsel** (z.B. DK9XR → DO3XR per UI) | `switch_operator()` nach Setting des neuen `default_power_w` | `"operator_switch"` |
| **Rig-Wechsel** (User wählt anderes Rig in Config) | `on_config_changed()` bei Wechsel der `rig.hamlib_id` | `"rig_change"` |
| **Bandwechsel** | `_bandwechsel_pruefen()`: nach jedem eigenen Frequenzbefehl (`handle_set_freq`) und je Slot (`_refresh_hardware_state`), dazu wie bisher `status()` | `"band_change"` |

Was die Events mit der Leistung tun:

| Event | gemerkter Wert aus `runtime_state` | sonst |
|---|---|---|
| `boot` | bleibt, höchstens auf Klassen- und Rig-Maximum begrenzt | Vorsichtswert, falls darüber |
| erster `band_change` nach dem Start | bleibt, höchstens auf das Maximum des Bands begrenzt | Vorsichtswert, falls darüber |
| jeder weitere `band_change` | Vorsichtswert, falls darüber | Vorsichtswert, falls darüber |
| `operator_switch`, `rig_change` | Vorsichtswert, falls darüber; der Zielwert geht in jedem Fall ans Gerät | ebenso |

**Operator- und Rig-Wechsel (seit 2026-11-14):** Liegt die Leistung des neuen
Operators schon unter dem Vorsichtswert, ging bis dahin kein Befehl ans
Gerät. Das Rig stand weiter auf dem Wert des Vorgängers (DO3XR sendete mit
den 100 W von DK9XR), und der nächste Poll übernahm ihn als „extern
verstellt". Jetzt schickt `_rig_auf_leistung` den Zielwert bei diesen beiden
Events immer hinaus, sofern das Rig die Leistung per CAT setzen kann.

### Bandwechsel-Detail

**Vor dem ersten Burst (seit 2026-11-14):** Bis dahin bemerkte nur
`status()` einen Bandwechsel, und das erst nach den Aussendungen des Slots.
Ohne offene Weboberfläche ging der erste Burst auf dem neuen Band mit der
alten Leistung hinaus, etwa 70 W auf 60 m, wo 15 W gelten. Jetzt prüft
`_bandwechsel_pruefen` an zwei Stellen: sofort nach jedem eigenen
Frequenzbefehl (Band aus der Bandplan-Zuordnung der befohlenen Frequenz)
und bei jeder Auffrischung des Hardware-Zustands für die Guards
(`_refresh_hardware_state`: je Slot und vor Bedienhandlungen). Der Floor
wird dort abgewartet, nicht als Hintergrundaufgabe gestartet.

`status()` erkennt den Wechsel weiterhin über die `freq_hz`-Toleranz
(±50 kHz um die FT8- und FT4-Dials, gleicher Mechanismus wie für die
UI-Anzeige) und startet den Floor als Hintergrundaufgabe. Hat
`_bandwechsel_pruefen` das Band schon vermerkt, passiert dort nichts mehr.

Ein Übergang `None → X` (erste Frequenz-Erkennung nach Boot) zählt auch.
Der Floor läuft dann mit dem nun bekannten Per-Band-Cap; ein gemerkter Wert
aus `runtime_state` bleibt bei diesem ersten Bandaufschlag stehen (siehe
oben).

Ein Übergang `X → None` (Rig parkt auf einer Frequenz die zu keinem
konfigurierten Band passt, z.B. WWV-Empfang) löst **nicht** aus — wir
haben dann keinen sinnvollen Cap.

### Was NICHT triggert

* Rig dreht physisch am Power-Regler → der bestehende Tamper-Sync
  übernimmt den manuellen Wert (Front-Panel ist Source-of-Truth, siehe
  den Block „TX-Power bidirektional syncen" im Rig-Poll in
  `orchestrator.py`)
* User bewegt Power-Slider in der UI → das ist explizit gewollt
* Antennen-Wechsel ohne Bandwechsel → Antennen-Logik hat eigene Guards
  (band-lockout), kein Power-Reset

## Edge-Cases

| Fall | Verhalten |
|---|---|
| Rig nicht erreichbar beim Boot (`rigctld` noch nicht up) | `set_rfpower` schlägt fehl, **interner `_tx_power_w` wird trotzdem geclamped**. Beim nächsten erfolgreichen Set-Befehl (Slider/Tamper) wird das ans Rig synchronisiert. |
| Band noch unbekannt beim Boot (Rig still meldet keine Freq) | Ohne gemerkten Wert: Fallback auf `rig.effective_max_power_w / 2` — konservativ, kein Per-Band-Cap. Mit gemerktem Wert gilt bis zum ersten Bandaufschlag das Rig-Maximum als Grenze. |
| `effective_max = 0` (Band nicht für Klasse erlaubt) | Seit v0.4.5 kein Floor: `_compute_safe_default_power_w` liefert `None`, die Leistung bleibt unverändert. Senden ist ohnehin durch den `license_guard` geblockt. Vorher setzte der Floor 1 W, und die Station blieb nach der Rückkehr aufs erlaubte Band bei 1 W stehen. |
| Rig ohne setzbare Leistung (FT-817/818) | Kein CAT-Befehl. Im Log steht die Bitte, am Gerät auf höchstens den Zielwert zu stellen; der interne Wert bleibt, weil der Poll den Ist-Wert vom Gerät liest. |
| Mehrere `status()`-Calls im selben Slot | `_last_active_band` wird vor dem `create_task` gesetzt — kein Task-Spam |
| `status()` ohne laufenden asyncio-Loop (sync Test) | `RuntimeError` wird gefangen, kein Crash, kein Floor-Trigger (Tests sind ohnehin nicht sicherheitsrelevant) |

## Echo-Detection-Integration

Der Helper ruft direkt nach dem erfolgreichen `set_rfpower`-Call
`_register_app_command("rfpower_norm", safe/max_w)`, damit der nächste
Rig-Poll das als unseren eigenen Befehl erkennt und **keinen** Tamper-Push
„Power extern verstellt" rausschickt. Echo-Fenster ist 3 s
(`_ECHO_WINDOW_S`).

## ntfy-Push?

Bewusst **nicht** dabei. Bandwechsel + Boot sind häufige Events; ntfy
für jedes Floor-Event würde nerven. Wenn etwas Auffälliges passiert
(Rig-Wechsel im laufenden Betrieb), erscheint das ohnehin im Log
(`tx-power safety-floor (rig_change): clamp 100W -> 2W (band=15m)`).

## Tests

`backend/tests/test_tx_power_safety.py`, 17 Tests:

- 5× compute (band-cap, fallback, 1W-floor, QMX+ 5W, nicht erlaubtes Band)
- 6× apply (nicht erlaubtes Band, clamp-down, QRP-bleibt, exact-noop, rig-failure, band-override)
- 2× rig-change (trigger bei hamlib_id-Wechsel, kein-trigger bei gleichem Rig)
- 4× gemerkte Leistung (überlebt Boot und ersten Bandaufschlag, wird auf das
  Klassenlimit geklemmt, ohne gemerkten Wert klemmt der Boot-Floor weiter,
  Operator- und Rig-Wechsel klemmen unverändert)

`backend/tests/test_audit_2026_11_14_sicherheit.py`, 3 Tests zur Leistung:
Grenze gilt für den ersten Burst auf dem neuen Band, der Frequenzbefehl setzt
sie sofort, der Operatorwechsel stellt die Leistung auch am Gerät.

## Bezug zu anderen Audits

- **`wsjtx_qso_state_audit.md`** — keine Überschneidung (QSO-State-
  Logic, nicht Hardware-Power)
- **`self_update.md`** — Self-Update macht Service-Restart → Boot-
  Trigger feuert. Seit 2026-09-09 bleibt die gemerkte Leistung dabei
  stehen; geklemmt wird nur, wenn sie über dem Klassen- oder Rig-Maximum
  liegt oder kein gemerkter Wert vorhanden ist.
- **`flags.md`**, Abschnitt „Sendeleistung nach einem Neustart".
