# Decoder-Evolution v0.5.2 → v0.8.0

Stand: 2026-05-25. 11 Releases an einem Tag. Hier die Übersicht.

## Sektion 1 — Polish + Bugfixes (v0.5.2 – v0.6.4)

### v0.5.2 — Funkstille / Tamper-Race / Conversation-IDLE-RX
- **Funkstille-Push throttle**: single-shot pro Episode statt alle 15min Spam
- **Mode-Switch Tamper-Push-Race**: 5s-Skip-Period nach `handle_set_freq` damit
  rig-poll nicht "Frequenz wurde verstellt" feuert beim eigenen Mode-Switch
- **Conversation-View RX-Filter**: auch Decodes von Calls die wir in
  letzten 30 TX-Aktionen angesprochen haben, nicht nur aktive QSO-Partner

### v0.5.3 — "Nächste Aktion"-Hint vervollständigt
- QSO_GRACE-State bekam Hint (war stumm)
- IDLE differenziert jetzt 4 Varianten (Hunt+CQ / Hunt-only / CQ-only / off)

### v0.5.4 — DT-Filter im Hunting-Picker (Audit-Lücke 1 vs WSJT-X)
Stationen mit |dt_s| > 2.5s werden übersprungen — ihr RX-Window ist schon
zu Ende wenn unsere Reply ankommt. ~0.6% der Decodes betroffen.

### v0.6.0 — Anti-WSJT-X-Audit Phase A+B+C+D
Sechs technische Builds aus Web-Recherche (groups.io/wsjtx, JTDX-Changelog,
K1JT-QEX) gegen unseren Code:
1. **A1 Late-Slot-Alert**: Decoder-Timing pro Slot, ntfy bei 3+ konsekutiven
   Slots >80% Slot-Länge
2. **A2 DT-Drift-Self-Diagnose**: rolling Median über letzte 200 Decode-DTs,
   ntfy bei systematischem Offset
3. **B C-Shim `decode_slot_v2`**: drei Modi
   - `standard` (osr=2, LDPC=25) — Original
   - `deep` (osr=4, LDPC=50) — JTDX-Deep-Äquivalent
   - `multi` — Pass1 standard + Pass2 deep, dedupe
4. **C Python-Wire** + Config-Field `decoder_mode` + UI-Dropdown +
   CPU-adaptive Fallback bei 3+ Late-Slots → auto auf "standard"
5. **D chrony GPS-Sync**: Konfiguration umgestellt von SOCK auf SHM
   (Permission-Falle vermieden). Resultat: gpsd 3.25 schreibt aber nicht
   in SHM → NTP-Fallback hält <1ms RMS (>2000× besser als FT8-Toleranz).

### v0.6.1 — Multi-Default + YAML-Magic-Bool-Fix
- Default `decoder_mode = "multi"`
- ConfigPanel.svelte: `yq()` quote'd jetzt YAML-1.1-Magic-Boolean-Keywords
  (off/on/yes/no/true/false). Vorher: `boot_mode: off` → bool False →
  Pydantic-Literal-Error
- DT-Drift-Push-Schwelle 0.5 → 1.5s (war zu eager, Audio-Buffer-Offset war
  systemisch ~0.5-0.8s — nicht-actionable)

### v0.6.2 — Permission-Audit Findings
- Berechtigungs-Audit: Backup-Files mit root:root Ownership archiviert
- Stale `/run/chrony.ttyACM0.sock` cleaned
- **Atomic config-write**: tempfile + rename statt direkter write
- **Auto-backup** vor jedem write (single-slot .bak)

### v0.6.3 — decoder_mode + actual_decoder_mode in API
Status surfaced configured-vs-actual mode (für CPU-Fallback-Visibility) +
late_slot_count. Frontend kann auto-fallback erkennen.

### v0.6.4 — Compound-Call-TX-Bug
Live im Monitoring entdeckt: `<RT25KR>` (resolved compound call mit angle
brackets) konnte nicht TX'ed werden. Fix: Pipeline strippt angle brackets,
`<...>` bleibt als unresolvable-marker.

## Sektion 2 — Pi-5-Power-Decoder (v0.7.0 – v0.7.1)

### v0.7.0 — Subtract-and-Rerun + Hint-Decoder + Auto-Notch
Drei Decoder-Builds für Pi-5-Hardware-Reserven (~95% idle CPU):

**Build 1 — Subtract-and-Rerun (C-Shim mode=3 "extreme")**
```
Pipeline:
  1. Pass standard (osr=2, LDPC=25)
  2. Pass deep (osr=4, LDPC=50)
  3. Subtract strong decodes (score>=20) vom Signal
  4. Re-decode residual standard + deep
  5. Hint-Pass am Ende
```
Synth-Helper `_ft8_subtract_decoded`: ftx_encode → ft8_encode → synth_gfsk →
in-place subtract bei (freq, dt_s) mit amp=0.4.

**Build 2 — Hint-Decoder (C-Shim `_ft8_hint_pass`)**
- Marginal-Score-Candidates (min_score=5 statt 10)
- LDPC-Iterations 120 (statt 25)
- **Strenge Post-Validation**: decoded text muss einen known-call aus
  `s_hash_table` enthalten
- JTDX Type-2-Filter-Äquivalent
- Population: orchestrator pusht beim Hydrate-from-DB alle worked-Calls
  als known (n22=0 als Validation-Marker)

**Build 3 — Auto-Notch (`audio/notch.py`, numpy-only)**
- `NotchDetector`: rolling 30s Spektrum-Analyse via numpy FFT
- Peaks >15dB über Median-Floor, persistent über 2+ Analysen
- `apply_notches`: pro Slot FFT → zero-out QRM-Bins (Notch-Width 8Hz,
  unter FT8-Tone-Spacing 6.25Hz) → IFFT
- scipy-frei, ~10ms pro Slot auf Pi 5

### v0.7.1 — Default decoder_mode = "extreme"
Pi-5-power als Default. CPU-Adaptive-Fallback bleibt aktiv (Pi 4 / overloaded
Pi 5 schaltet auto auf "standard" zurück bei 3+ konsekutiven Late-Slots).

## Sektion 3 — Decoder-Telemetrie + Self-Tuning (v0.8.0)

### v0.8.0 — Sechs Self-Tuning Builds
**Build A — Hint-Decoder Live-Queue**: bei jedem Decode call_from + call_to
in C-Shim-Hash-Table pushen (nicht nur worked-Calls beim Boot).
JTDX-Recent-Decode-Bias — fängt Stationen die 2-3 Slots vor uns gerufen
haben.

**Build B — DT-Offset Auto-Kalibrierung**: rolling Median letzte 100+
Decode-dt_s-Werte. Bei |median|>0.3s wird Offset als negativ-Korrektur auf
`slot_start_posix` appliziert → Decoder sieht zentrierte DTs → bessere
Time-Window-Treffer. Update alle 5 min, max ±2s clamp. Self-corrected den
0.8s Audio-Buffer-Offset live nach Deploy.

**Build C — Per-Pass Decoder-Statistics**: C-Shim trackt für mode=extreme
- `pass_standard`
- `pass_deep`
- `pass_subtract_residual`
- `pass_hint`
- `slots_decoded`

Exposed via `/api/status.decoder_pass_stats`. Datengetriebener Insight
welcher Pass tatsächlich Mehrwert bringt.

**Build D — Adaptive LDPC-Iterations**: Pipeline misst avg_decode_duration_s
und setzt LDPC-Factor pro Slot via `lib.ft8_shim_set_ldpc_factor(pct)`:
- <15% Slot-Last → 200% Iter (CPU-Reserve nutzen)
- 15-30% → 150% (komfortabel)
- 30-60% → 100% (Standard)
- >60% → 60% (Slot-Drop-Schutz)

**Build H — PSK-Reporter Upload**: existierender `PskReporterClient`
(integrations/psk_reporter.py — war komplett implementiert aber nie
aufgerufen) wird jetzt aus Decode-Pfad mit `upload_decode()` gefüttert.
5min-Flush, IPFIX-binary-Protocol via UDP 4739. Reziproker Community-Wert.
Config: `integrations.psk_reporter.upload_decodes` (Default True).

**Build I — FT4 mode-aware Decoder**: neue `ft4_shim_decode_slot_v2(mode)`.
FT4 unterstützt jetzt deep/multi/extreme — aber ohne Subtract (7.5s-Slot
zu kurz). Standard=osr2/LDPC25, Deep/Multi/Extreme=osr4/LDPC50 + zweite
Pass-Wave. Adaptive LDPC-Factor wirkt auch hier.

## Live-Daten nach v0.8.0 Deploy

Nach ~30 min Live-Decoding (54 slots, 425 decodes) auf ft8 (Pi 5):

```
Decoder-Pass-Verteilung:
  Standard:  94.4%  (Hauptarbeiter)
  Deep:       0.0%  (Band hat keine -22..-24 dB Stationen)
  Subtract:   5.2%  ⭐ JTDX-style — ~1 in 18 Decodes wäre verloren
  Hint:       0.5%  (klein aber existent)
  avg:        9.5 decodes/slot
```

**DT-Auto-Kalibrierung Live**: `offset +0.000s → +0.800s` autonom korrigiert
nach 5 Minuten Real-Decode-Sampling.

**Auto-Notch sehr aktiv**: Sebastian's QRM-Umgebung hat Stör-Cluster bei
290Hz, 820Hz, 900Hz, 1230Hz, 1560Hz, 2070Hz, 2730Hz, 2820Hz. Detector hat
sie alle gefunden + gestrippt.

**late_slot_count=0** auf beiden Pis — Pi 5 verkraftet extreme-Mode locker.

## Mode-Übersicht (was wann benutzt)

| Mode | osr | LDPC | Subtract | Hint | CPU vs Standard | Pi-Empfehlung |
|---|---|---|---|---|---|---|
| `standard` | 2 | 25 | nein | nein | 1× | Pi 3/4 |
| `deep` | 4 | 50 | nein | nein | ~1.5-2× | Pi 4/5 |
| `multi` | 2+4 | 25+50 | nein | nein | ~2-2.5× | Pi 5 |
| `extreme` | 2+4 | 25+50 | **ja** | **ja** | ~3-4× | Pi 5 |

Default seit v0.7.1: `extreme`. CPU-Adaptive-Fallback bei Überlast.

## Memo: was wir bewusst NICHT gemacht haben

- **AP-Decoding** — bewusst weggelassen (False-Positive-Quelle, K1JT hat es
  mehrfach mit Filter nachgepatcht; JTDX hat Type-2-Filter eingebaut).
  Hint-Decoder gibt ähnlichen Sensitivity-Boost ohne Phantome.
  *Nachtrag v0.71.0:* OSD ist **kein** AP (keine a-priori-Bits, sondern ein
  besserer Decoder auf denselben Soft-Bits) und läuft nur im Hint-Pass
  hinter CRC, Härte-Schwelle und Known-Call-Gate — siehe Nachtrag unten.
- **GPU/NPU-Acceleration** — Pi 5 VideoCore VII nicht trivial CUDA-style
  für ft8_lib nutzbar. Spielerei.
- **Multi-Threaded Decoder** — Race-Risk hoch, marginal gain.
- **Multi-Pi Cluster-Coordination** — Sebastian abgelehnt.
- **Antenna-Switch-Automation** — Hardware-Info fehlt.
- **Slot-Audio-Snapshot** — Disk-Spam ohne klaren Nutzen.

## Operative Verbesserungen (außerhalb Decoder)

- **Atomic config-write** + auto-bak (v0.6.2)
- **Compound-Call-Stripping** beim Decode (v0.6.4)
- **Live-Pass-Stats** sichtbar in /api/status (v0.6.3 + v0.8.0)
- **DT-Drift-Self-Diagnose** mit neutraler Sprache (v0.6.1)
- **Berechtigungs-Audit** mit Pi-Side cleanup (v0.6.2)
- **chrony NTP-only** (GPS-SHM nicht trivial fixbar, NTP <1ms reicht)

## Nachtrag 2026-09-06 — Kampagne "alles aus dem Pi 4B holen" (v0.68–v0.70)

Messbasis: synthetische Slots (bekannte Wahrheit) und die 22 WSJT-X-
Referenzaufnahmen in `vendor/ft8_lib/test/wav` (353 WSJT-X-Decodes) —
reproduzierbar mit `scripts/bench_decoder_corpus.py` (`--by-snr`, `--knob name=wert`).
Endstand 2026-09-06: standard 268 (76 %), extreme **311 (88 %)**.

### v0.68.0 — zweistufiger Decoder
Stufe 1 (standard, ~0,35 s) entscheidet über TX, Stufe 2 (Rest des Modus)
läuft im Thread nebenher und reicht nach. Vorher lag der Sendestart im
extreme-Modus 2,8 s nach der Slotgrenze (gemessen am Pi 4B).

### v0.70.0 — dt-Kalibrierung, kohärente Subtraktion, Crash-Fix
- **dt war pass-abhängig falsch.** ft8_lib misst die Kandidatenzeit am Ende
  des Hann-Analysefensters; der wahre Symbolstart liegt
  `(block/2 + nfft/2 − subblock)` Samples früher (+0,16 s bei osr 2/2,
  +0,36 s bei osr 4/4). Dazu fehlte die WSJT-X-Konvention (DT relativ zum
  nominalen Sendestart 0,5 s nach der Slotgrenze; Primärquelle
  `ft8_decode.f90`: `xdt=xdt-0.5`, `ft4_decode.f90`: `xdt=ibest/666.67-0.5`).
  Jetzt: Abweichung zu WSJT-X auf den Referenzaufnahmen −0,005 s (σ 0,038 s).
  Vorher +0,66 s (std) bzw. +0,86 s (deep). Folgen der alten Werte: die
  „ALSA-Latenz 0,5–0,8 s" aus v0.6.1 war der Decoder, nicht ALSA; die
  DT-Auto-Kalibrierung schob das Slotfenster um +0,8 s; der |dt|≤2,5-s-
  Filter im Hunting war schief.
- **Subtraktion war keine.** `_ft8_subtract_decoded` zog eine GFSK-Welle mit
  fester Amplitude 0,4 und zufälliger Phase ab — ein zweiter Störer. Jetzt
  kohärent nach dem Muster von `subtractft8.f90`: komplexe Referenz,
  Feinsuche ±1,5 Hz / ±0,04 s, gleitende komplexe Amplitude über 0,33 s,
  `Re(A(t)·ref)` abziehen. Restenergie eines starken Signals: −26 dB
  (auf Rauschniveau). Benchmark „5 starke Signale maskieren 5 schwache in
  20–30 Hz Abstand": vorher 5/10, jetzt 10/10.
- **Zweite Subtract-Runde** (JTDX fährt 2–3), Hint-Ringtabelle 256 → 1024.
- **Stack-Überlauf**: `shim_lookup_hash` schrieb 14 Bytes in ft8_libs
  `char[12]` — jeder decodierte Hash-Call mit Tabellentreffer hätte den
  Controller mit „stack smashing detected" beendet. Gefunden per ASan im
  Benchmark, nie live aufgetreten (Journal geprüft).
- Stand gegen WSJT-X auf den Referenzaufnahmen: standard trifft 73 %,
  extreme 79 % (11 Decodes nur bei uns, alle plausibel). Lücke liegt unter
  −13 dB (≈50 %): dort arbeitet WSJT-X mit OSD — nächster Schritt.

### v0.71.0 — OSD im Hint-Pass
Ordered statistics decoding (Fossorier/Lin; WSJT-X `osd174_91.f90`): wenn
Belief Propagation scheitert, werden die 91 zuverlässigsten linear
unabhängigen Codewort-Positionen als Informationsmenge genommen, das
Generator-System per Gauß-Jordan gelöst und Ordnung 1 (91 Einzel-Flips)
plus Ordnung 2 über die 60 unsichersten Infobits probiert; die 16 besten
Kandidaten nach Soft-Metrik bekommen die CRC-Prüfung. Absicherung gegen
Phantome (ein OSD-Ergebnis ist immer ein gültiges Codewort): CRC-14,
Hamming-Abstand zur harten Entscheidung ≤ 32 und Metrik ≤ 60, und das
Known-Call-Gate des Hint-Passes — das vorher zirkulär war, weil
`ftx_message_decode` die gerade entpackten Calls selbst in die Tabelle
schrieb (jetzt Lese-Interface bis nach dem Gate). Korpus: 279 → 285
Treffer (+2 %), alle OSD-Decodes mit WSJT-X-Bestätigung oder plausiblen
Calls; x86 +0,16 s/Slot in Stufe 2. Eigene Export-Funktion
`ftx_extract_llr()` in `vendor/ft8_lib/ft8/decode.c`.

### v0.72.0 — Analysefenster pro Pass, Hint-Pass auf osr 4
Befund auf dem Korpus: der deep-Pass (osr 4/4) fand **null** Decodes — in
allen Messungen, auch live am Pi (0 von 380). Ursache: ft8_lib legt das
Hann-Fenster über `nfft = block · freq_osr`, bei osr 4 also über **vier
Symbole**; die Symbolenergie verschmiert. Jetzt bekommt `monitor_config_t`
ein `window_mode`: kürzeres Hann-Fenster am Frame-Ende, zero-padded auf
`nfft` (Frequenz-Interpolation ohne Schmieren). Gemessen (extreme, Korpus):

| std / deep / hint Fenster        | Treffer | deep-Pass |
|----------------------------------|---------|-----------|
| Original (2 / 4 / 2 Symbole)     | 285     | 0         |
| deep 1,5 Symbole                 | 295     | 14        |
| deep 2 Symbole                   | 294     | 14        |
| deep 2, hint 2 Symbole, hint osr 4 | **299** | 14      |
| Rechteck 1 Symbol überall        | 289 (+5 Phantome) | 22 |

Der std-Pass bleibt beim Original (2-Symbol-Fenster ist dort das Optimum:
256 vs. 254/238/231). Recall nach SNR (WSJT-X-Decodes als Wahrheit):
unter −13 dB jetzt 57–70 % (vorher ≈ 50 %), ab −12 dB 83–100 %.
Gesamt: **298–299 / 353 = 84 %** (Sessionbeginn: 279 = 79 %; standard: 257 = 73 %).

Negativ getestet und verworfen (keine Änderung der Trefferzahl):
mehr Kandidaten (300 → 600 → 1200), Mindestscore 10 → 8 → 6, deep-LDPC
50 → 100, BP mit vier LLR-Skalierungen (WSJT-X llra..llrd), dritte
Subtract-Runde, Subtraktion ab Score 0/10 statt 20 (±0). Die Knöpfe bleiben
als `ft8_shim_set_knob()` für weitere Messungen im Shim.

### v0.72.1 — std-Pass mit time_osr 4, Dedupe über die Nutzlast, Stufe 2 mit 150 %
- **std-Pass (Stufe 1, entscheidet über TX) mit time_osr 4**: Korpus 257 → 266
  Decodes im reinen Standardmodus, x86 +13 ms/Slot (Pi: ~+0,15 s beim
  Sendestart, Limit 1,5 s). Mehr Ziele im Antwortmodus, ohne Stufe 2 abzuwarten.
- **Dedupe über die 77-Bit-Nutzlast statt CRC-14**: bei ~30 Decodes/Slot
  kollidierten zwei verschiedene Nachrichten in ~3 % der Slots im 14-Bit-Hash,
  die zweite fiel stumm weg.
- **Stufe-2-LDPC-Faktor 250 → 150 %**: auf dem Korpus identische Trefferzahl
  bei 400 %, 250 %, 150 % und 100 %; 150 % spart ~15 % Stufe-2-Zeit am Pi 4B.
- Live am Pi 4B nach v0.72.0 (8 Slots): deep-Pass 4 Decodes — vorher in 380
  Slots null. OSD lieferte in v0.71.0 live 22 Decodes in 76 Slots (~7 %).

### v0.73.0 — Feinsynchronisation + symbolsynchrone Demodulation
Nach dem Muster von WSJT-X `ft8b` (sync8 → Feinsync → DFT pro Symbol):
ft8_lib holt die Soft-Bits aus dem Wasserfall-Raster; bei osr 4 liegt der
wahre Symbolstart bis 0,02 s und die Frequenz bis 0,78 Hz daneben, was
schwache Signale Energie kostet. Jetzt im Hint-Pass, wenn BP und OSD auf
dem Raster scheitern: 9×9-Raster (60 Samples / 0,2 Hz) um den Kandidaten
mit Costas-Energie als Maß, dann 79 Symbole × 8 Töne mit Rechteckfenster
über genau ein Symbol (orthogonale Töne), LLR wie `ft8_extract_symbol`,
BP + OSD. Feinsync-Decodes per BP+CRC brauchen keinen bekannten Call
(so vertrauenswürdig wie der std-Pass), OSD-Ergebnisse weiterhin schon.
Max. 40 Kandidaten pro Slot (120 brachten nichts mehr, 2,7× Zeit).
Korpus: 300 → **309 / 353 = 87,5 %**, x86 +0,3 s/Slot in Stufe 2
(Pi 4B geschätzt +1,5 s; Budget 12 s). Neue Statistik `pass_refine`.

### v0.75.0 — FT4: Analysefenster und kohärente Subtraktion
Synthetischer FT4-Korpus (8 Signale, 6 Slots, Maskierung + schwach):
std 25/48; deep/extreme mit Original-Fenster **13/48** — der osr-4-Pass war
für FT4 bisher *schlechter* als standard (4-Symbol-Fenster bei 48-ms-
Symbolen). Mit Hann-2-Symbol-Fenster 27/48, mit kohärenter Subtraktion
(`_ft4_subtract_decoded`, 2 Runden, Feinsuche ±1 Hz/±144 Samples) 28/48.
FT4-dt gegen die Synthese-Wahrheit: ±0,01 s (WSJT-X-Konvention −0,5 s).
Stufe-2-Pässe deduplizieren jetzt gegen die Ergebnisse früherer Pässe
(vorher konnten Duplikate an die Pipeline gehen).
