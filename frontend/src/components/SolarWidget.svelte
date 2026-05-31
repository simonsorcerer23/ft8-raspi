<script>
  // Space-Weather-Header: aktuelle Solar-/Geomagnetik-Indizes mit
  // erklärenden Tooltips. Werte kommen von hamqsl.com, alle 30 min
  // refreshed (Quelle updated ~3h cycle).
  import { onMount } from 'svelte';
  import { api } from '../lib/api.js';

  let data = $state({ available: false });

  async function refresh() {
    try {
      data = await api.get('/solar');
    } catch { data = { available: false }; }
  }

  onMount(() => {
    refresh();
    const t = setInterval(refresh, 30 * 60_000);
    return () => clearInterval(t);
  });

  function sfiQuality(s) {
    if (s == null) return { color: '#94a3b8', label: '—' };
    if (s < 80)  return { color: '#ef4444', label: 'schlecht' };
    if (s < 120) return { color: '#fbbf24', label: 'mässig' };
    if (s < 180) return { color: '#22c55e', label: 'gut' };
    return { color: '#38bdf8', label: 'top' };
  }
  function kQuality(k) {
    if (k == null) return { color: '#94a3b8', label: '—' };
    if (k <= 2) return { color: '#22c55e', label: 'ruhig' };
    if (k <= 3) return { color: '#fbbf24', label: 'unruhig' };
    if (k <= 4) return { color: '#f59e0b', label: 'aktiv' };
    return { color: '#ef4444', label: 'Sturm' };
  }
  function aQuality(a) {
    if (a == null) return { color: '#94a3b8', label: '—' };
    if (a <= 15)  return { color: '#22c55e', label: 'ruhig' };
    if (a <= 30)  return { color: '#fbbf24', label: 'erhöht' };
    return { color: '#ef4444', label: 'gestört' };
  }
  function snQuality(n) {
    if (n == null) return { color: '#94a3b8', label: '—' };
    if (n < 30)   return { color: '#ef4444', label: 'wenig' };
    if (n < 80)   return { color: '#fbbf24', label: 'moderat' };
    if (n < 150)  return { color: '#22c55e', label: 'viel' };
    return { color: '#38bdf8', label: 'maximum' };
  }
  function xQuality(x) {
    if (!x || x === 'A0.0') return { color: '#22c55e', label: 'ruhig' };
    const cls = x[0];
    if (cls === 'A' || cls === 'B') return { color: '#22c55e', label: 'ruhig' };
    if (cls === 'C') return { color: '#fbbf24', label: 'mild' };
    if (cls === 'M') return { color: '#f59e0b', label: 'flare' };
    if (cls === 'X') return { color: '#ef4444', label: 'starker Flare' };
    return { color: '#94a3b8', label: '?' };
  }

  const sfiTip = (s) =>
`SFI = Solar Flux Index (10.7cm-Radiostrahlung der Sonne)
Aktuell: ${s ?? '?'} — ${sfiQuality(s).label}

Skala:
  < 80    schlechte HF-Bedingungen
  80-120  mässig (20m/40m gehen, höhere Bänder zäh)
  120-180 gut (15m/10m offen)
  > 180   top (alle Bänder offen, DX leicht)`;

  const kTip = (k) =>
`K-Index = aktuelle geomagnetische Störung (live, 0-9 Skala)
Aktuell: ${k ?? '?'} — ${kQuality(k).label}

Hoher K = Polarlichter, gestörtes Magnetfeld,
HF-Propagation leidet (besonders Nordrouten).

Skala:
  0-2  ruhig — Bedingungen wie erwartet
  3    unruhig
  4    aktiv (HF wird wackelig)
  5-9  Sturm (HF zusammengebrochen, NVIS gestört)`;

  const aTip = (a) =>
`A-Index = Tagesschnitt der geomagnetischen Aktivität
Aktuell: ${a ?? '?'} — ${aQuality(a).label}

Abgeleitet aus den 8 K-Werten des Tages.
Lange-Zeitraum-Indikator (K zeigt Live-Zustand).

Skala:
  ≤ 15  ruhig — gutes Long-Path-DX möglich
  16-30 erhöht
  > 30  gestört — Magnetfeld lädiert`;

  const snTip = (n) =>
`SN = Sunspot Number (sichtbare Sonnenflecken)
Aktuell: ${n ?? '?'} — ${snQuality(n).label}

Mehr Flecken = mehr UV/Röntgen → stärkere F-Layer-
Ionisation → bessere HF-Reflexion auf höheren Bändern.

Im aktuellen Solar-Cycle (24/25) typisch:
  < 30   Cycle-Minimum
  30-80  steigende/fallende Phase
  80-150 normales Maximum
  > 150  Peak-Cycle`;

  const xTip = (x) =>
`X-Ray-Flux = aktuelle Solar-Röntgenstrahlung (Flare-Klasse)
Aktuell: ${x ?? '?'} — ${xQuality(x).label}

Stärkere Flares (M/X) ionisieren die D-Schicht
übermässig → tiefe Bänder (80m/40m) bleiben kurz-
zeitig blockiert (Tagsüber, "SID"-Effekt).

Klassen-Skala:
  A/B  ruhig — alles offen
  C    mild — kaum Auswirkung
  M    mittlerer Flare — 10-30 Min D-Layer-Absorption
  X    starker Flare — Blackout möglich`;
</script>

{#if data.available}
  <div class="solar" title="Space-Weather — Quelle: hamqsl.com, Update alle 30 min">
    <div class="cell" style="color: {sfiQuality(data.sfi).color}" title={sfiTip(data.sfi)}>
      <span class="icon">☀️</span>
      <span class="lbl">SFI</span>
      <strong>{data.sfi ?? '?'}</strong>
    </div>
    <div class="cell" style="color: {kQuality(data.k_index).color}" title={kTip(data.k_index)}>
      <span class="icon">🧲</span>
      <span class="lbl">K</span>
      <strong>{data.k_index ?? '?'}</strong>
    </div>
    <div class="cell" style="color: {aQuality(data.a_index).color}" title={aTip(data.a_index)}>
      <span class="icon">📅</span>
      <span class="lbl">A</span>
      <strong>{data.a_index ?? '?'}</strong>
    </div>
    <div class="cell" style="color: {snQuality(data.sunspots).color}" title={snTip(data.sunspots)}>
      <span class="icon">⚫</span>
      <span class="lbl">SN</span>
      <strong>{data.sunspots ?? '?'}</strong>
    </div>
    {#if data.x_ray}
      <div class="cell" style="color: {xQuality(data.x_ray).color}" title={xTip(data.x_ray)}>
        <span class="icon">⚡</span>
        <span class="lbl">X</span>
        <strong>{data.x_ray}</strong>
      </div>
    {/if}
  </div>
{/if}

<style>
  .solar {
    display: flex; gap: 0.6rem;
    background: rgba(15,23,42,0.7); border: 1px solid #334155;
    border-radius: 8px; padding: 0.4rem 0.7rem;
    font-size: 0.9rem;
    cursor: help;
  }
  .cell {
    display: flex; align-items: center; gap: 0.3rem;
    line-height: 1;
    cursor: help;
  }
  .icon { font-size: 1.05rem; }
  .lbl {
    color: #94a3b8;
    font-size: 0.7rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    font-weight: 600;
  }
  .cell strong {
    font-family: ui-monospace, monospace;
    font-weight: 700;
    font-size: 1.05rem;
  }
  @media (max-width: 640px) {
    .solar { gap: 0.4rem; padding: 0.3rem 0.5rem; font-size: 0.8rem; }
    .lbl { display: none; }    /* nur Icon + Wert auf Mobile */
    .icon { font-size: 0.95rem; }
    .cell strong { font-size: 0.95rem; }
  }
</style>
