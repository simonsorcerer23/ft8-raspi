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
