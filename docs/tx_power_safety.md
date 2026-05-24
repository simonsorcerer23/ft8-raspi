# TX-Power Safety-Floor

**Status:** Aktiv seit v0.2.3 (Sebastian-Request 2026-05-24).

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
| **Bandwechsel** | `status()` bei `active_band != _last_active_band` (X→Y mit beiden nicht-None) | `"band_change"` |

### Bandwechsel-Detail

Erkannt wird der Wechsel über die `freq_hz`-Toleranz (±50 kHz, gleicher
Mechanismus wie für die UI-Anzeige). Ein Übergang `None → X` (erste
Frequenz-Erkennung nach Boot) zählt auch — ist redundant zum Boot-
Trigger (der mit Rig-Max als Fallback rechnete), aber idempotent: der
Floor läuft erneut mit dem nun bekannten Per-Band-Cap und korrigiert
ggf. nach unten.

Ein Übergang `X → None` (Rig parkt auf einer Frequenz die zu keinem
konfigurierten Band passt, z.B. WWV-Empfang) löst **nicht** aus — wir
haben dann keinen sinnvollen Cap.

### Was NICHT triggert

* Rig dreht physisch am Power-Regler → der bestehende Tamper-Sync
  übernimmt den manuellen Wert (Front-Panel ist Source-of-Truth, siehe
  `orchestrator.py`-Kommentar rund um Zeile 1820)
* User bewegt Power-Slider in der UI → das ist explizit gewollt
* Antennen-Wechsel ohne Bandwechsel → Antennen-Logik hat eigene Guards
  (band-lockout), kein Power-Reset

## Edge-Cases

| Fall | Verhalten |
|---|---|
| Rig nicht erreichbar beim Boot (`rigctld` noch nicht up) | `set_rfpower` schlägt fehl, **interner `_tx_power_w` wird trotzdem geclamped**. Beim nächsten erfolgreichen Set-Befehl (Slider/Tamper) wird das ans Rig synchronisiert. |
| Band noch unbekannt beim Boot (Rig still meldet keine Freq) | Fallback auf `rig.effective_max_power_w / 2` — konservativ, kein Per-Band-Cap |
| `effective_max = 0` (Band nicht für Klasse erlaubt) | `safe = max(1, 0) = 1 W`. Senden ist ohnehin durch `can_tx_on()` geblockt; der 1 W ist nur ein sauberer Fallback-Wert. |
| Mehrere `status()`-Calls im selben Slot | `_last_active_band` wird vor dem `create_task` gesetzt — kein Task-Spam |
| `status()` ohne laufenden asyncio-Loop (sync Test) | `RuntimeError` wird gefangen, kein Crash, kein Floor-Trigger (Tests sind ohnehin nicht sicherheitsrelevant) |

## Echo-Detection-Integration

Der Helper ruft `_register_app_command("rfpower_norm", safe/max_w)`
**vor** dem `set_rfpower`-Call, damit der nächste Rig-Poll das als
unseren eigenen Befehl erkennt und **keinen** Tamper-Push „Power
extern verstellt" rausschickt. Echo-Fenster ist 3 s — reicht weil der
Rig-Poll typischerweise jede Sekunde läuft.

## ntfy-Push?

Bewusst **nicht** dabei. Bandwechsel + Boot sind häufige Events; ntfy
für jedes Floor-Event würde nerven. Wenn etwas Auffälliges passiert
(Rig-Wechsel im laufenden Betrieb), erscheint das ohnehin im Log
(`tx-power safety-floor (rig_change): clamp 100W -> 2W (band=15m)`).

## Tests

Alle in `backend/tests/test_tx_power_safety.py`, 11 Tests:

- 4× compute (band-cap, fallback, 1W-floor, QMX+ 5W)
- 5× apply (clamp-down, QRP-bleibt, exact-noop, rig-failure, band-override)
- 2× rig-change (trigger bei hamlib_id-Wechsel, kein-trigger bei gleichem Rig)

## Bezug zu anderen Audits

- **`wsjtx_qso_state_audit.md`** — keine Überschneidung (QSO-State-
  Logic, nicht Hardware-Power)
- **`self_update.md`** — Self-Update macht Service-Restart → Boot-
  Trigger feuert → Floor läuft. Nach jedem Update könnte also die
  Power neu geclamped werden (wenn vorher zu hoch). Bewusst so.
