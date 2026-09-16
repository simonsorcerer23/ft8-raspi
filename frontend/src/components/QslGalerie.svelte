<script>
  // eQSL-Posteingang durchblaettern, ohne sich bei eQSL anzumelden.
  //
  // Die Bilder holt der Pi im Hintergrund — langsamer als sechs je Minute,
  // so verlangt es eQSL. Karten ohne Bild bekommen deshalb einen
  // Platzhalter mit den Verbindungsdaten statt eines toten Rahmens: Die
  // Angaben sind vollstaendig, nur das Motiv fehlt noch.
  import { onMount } from 'svelte';
  import { api } from '../lib/api.js';
  import QslBild from './QslBild.svelte';
  import { t } from '../lib/i18n.svelte.js';

  let karten = $state([]);
  let stand = $state(null);
  let laden = $state(true);
  let fehler = $state(null);
  let offset = $state(0);
  let gesamt = $state(0);
  let suche = $state('');
  let nurMitBild = $state(false);
  let gross = $state(null);      // aufgeschlagene Karte

  const PRO_SEITE = 60;

  async function hole(neu = false) {
    laden = true; fehler = null;
    try {
      if (neu) offset = 0;
      const r = await api.qslListe({
        limit: PRO_SEITE, offset,
        call: suche.trim() || undefined,
        nur_mit_bild: nurMitBild || undefined,
      });
      karten = r.karten ?? [];
      gesamt = r.gesamt ?? 0;
      stand = await api.qslStand();
    } catch (e) {
      fehler = e.message;
    } finally {
      laden = false;
    }
  }

  onMount(hole);

  function blaettern(richtung) {
    const neu = offset + richtung * PRO_SEITE;
    if (neu < 0 || neu >= gesamt) return;
    offset = neu;
    hole();
  }

  // Tastatur im Grossbild: blaettern und schliessen.
  function taste(e) {
    if (!gross) return;
    const i = karten.findIndex(k => k.id === gross.id);
    if (e.key === 'Escape') gross = null;
    else if (e.key === 'ArrowRight' && i < karten.length - 1) gross = karten[i + 1];
    else if (e.key === 'ArrowLeft' && i > 0) gross = karten[i - 1];
  }

  function datum(s) {
    if (!s || s.length !== 8) return '';
    return `${s.slice(6, 8)}.${s.slice(4, 6)}.${s.slice(0, 4)}`;
  }
  function zeit(s) {
    return s && s.length >= 4 ? `${s.slice(0, 2)}:${s.slice(2, 4)}` : '';
  }
</script>

<svelte:window onkeydown={taste} />

<section class="panel">
  <h3>{t('qsl.title')}</h3>

  {#if stand}
    <div class="stand">
      <span>{stand.mit_bild} / {stand.gesamt}</span>
      <span class="leiste">
        <i style="width: {stand.gesamt ? (100 * stand.mit_bild / stand.gesamt) : 0}%"></i>
      </span>
      {#if stand.offen > 0}
        <span class="rest">{t('qsl.offen', { n: stand.offen })}</span>
      {/if}
    </div>
  {/if}

  <div class="werkzeug">
    <input type="search" bind:value={suche} placeholder={t('qsl.suche')}
           onkeydown={(e) => e.key === 'Enter' && hole(true)} />
    <label class="hakerl">
      <input type="checkbox" bind:checked={nurMitBild} onchange={() => hole(true)} />
      {t('qsl.nur_mit_bild')}
    </label>
    <button class="btn" onclick={() => hole(true)} disabled={laden}>
      {laden ? '…' : t('qsl.suchen')}
    </button>
  </div>

  {#if fehler}
    <div class="fehler">{fehler}</div>
  {:else if laden && karten.length === 0}
    <div class="hinweis">{t('qsl.laedt')}</div>
  {:else if karten.length === 0}
    <div class="hinweis">{t('qsl.leer')}</div>
  {:else}
    <div class="gitter">
      {#each karten as k (k.id)}
        <button class="karte" class:ohne={!k.bild_da} onclick={() => gross = k}>
          {#if k.bild_da}
            <QslBild id={k.id} alt={k.call} />
          {:else}
            <span class="platzhalter">
              <b>{k.call}</b>
              <i>{datum(k.qso_date)}</i>
              <i>{k.band} · {k.mode}</i>
              <em>{t('qsl.bild_folgt')}</em>
            </span>
          {/if}
          <span class="unterschrift">{k.call}</span>
        </button>
      {/each}
    </div>

    <div class="blaettern">
      <button class="btn" onclick={() => blaettern(-1)} disabled={offset === 0}>←</button>
      <span>{offset + 1}–{Math.min(offset + PRO_SEITE, gesamt)} {t('qsl.von')} {gesamt}</span>
      <button class="btn" onclick={() => blaettern(1)}
              disabled={offset + PRO_SEITE >= gesamt}>→</button>
    </div>
  {/if}
</section>

{#if gross}
  <div class="lupe" onclick={() => gross = null} role="presentation">
    <div class="lupe-inhalt" onclick={(e) => e.stopPropagation()} role="presentation">
      {#if gross.bild_da}
        <span class="lupe-bild"><QslBild id={gross.id} alt={gross.call} /></span>
      {:else}
        <div class="lupe-leer">{t('qsl.bild_folgt')}</div>
      {/if}
      <div class="lupe-daten">
        <b>{gross.call}</b>
        <span>{datum(gross.qso_date)} {zeit(gross.time_on)} UTC</span>
        <span>{gross.band} · {gross.mode}{gross.gridsquare ? ' · ' + gross.gridsquare : ''}</span>
        {#if gross.nachricht}<span class="gruss">„{gross.nachricht}"</span>{/if}
      </div>
      <button class="zu" onclick={() => gross = null} aria-label={t('qsl.schliessen')}>×</button>
    </div>
  </div>
{/if}

<style>
  .panel { background: var(--panel); border-radius: 8px; padding: 0.8rem; }
  h3 { margin: 0 0 0.6rem; color: var(--accent); font-size: 0.95rem; }

  .stand { display: flex; align-items: center; gap: 0.6rem; margin-bottom: 0.6rem;
           font-size: 0.78rem; color: #94a3b8; }
  .leiste { flex: 1; height: 4px; background: #1e293b; border-radius: 2px; overflow: hidden; }
  .leiste i { display: block; height: 100%; background: var(--accent);
              transition: width .6s ease; }
  .rest { white-space: nowrap; }

  .werkzeug { display: flex; gap: 0.5rem; align-items: center; flex-wrap: wrap;
              margin-bottom: 0.7rem; }
  .werkzeug input[type="search"] { flex: 1; min-width: 8rem; }
  .hakerl { display: flex; align-items: center; gap: 0.3rem; font-size: 0.78rem;
            color: #94a3b8; white-space: nowrap; }

  .gitter { display: grid; gap: 0.5rem;
            grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); }

  .karte { position: relative; padding: 0; border: 1px solid #1e293b;
           background: #0f172a; border-radius: 4px; overflow: hidden;
           cursor: pointer; aspect-ratio: 14 / 9; }
  .karte:hover { border-color: var(--accent); }
  .karte img { width: 100%; height: 100%; object-fit: cover; display: block; }

  .karte.ohne { cursor: default; }
  .platzhalter { display: flex; flex-direction: column; align-items: center;
                 justify-content: center; height: 100%; gap: 0.15rem;
                 color: #64748b; font-size: 0.72rem; }
  .platzhalter b { color: #94a3b8; font-size: 0.95rem; letter-spacing: 0.03em; }
  .platzhalter i { font-style: normal; }
  .platzhalter em { font-style: normal; font-size: 0.65rem; color: #475569;
                    margin-top: 0.25rem; }

  .unterschrift { position: absolute; left: 0; right: 0; bottom: 0;
                  background: rgba(2,6,23,0.75); color: #e2e8f0;
                  font-size: 0.7rem; padding: 0.15rem 0.3rem;
                  font-family: ui-monospace, monospace; }
  .karte.ohne .unterschrift { display: none; }

  .blaettern { display: flex; align-items: center; justify-content: center;
               gap: 0.8rem; margin-top: 0.7rem; font-size: 0.78rem;
               color: #94a3b8; }

  .hinweis, .fehler { color: #64748b; font-size: 0.82rem; padding: 0.6rem 0; }
  .fehler { color: #f87171; }

  .lupe { position: fixed; inset: 0; background: rgba(2,6,23,0.88);
          display: flex; align-items: center; justify-content: center;
          z-index: 100; padding: 1rem; }
  .lupe-inhalt { position: relative; max-width: min(900px, 96vw); }
  .lupe-bild { display: block; max-height: 74vh; }
  .lupe-inhalt :global(img) { max-width: 100%; max-height: 74vh; display: block;
                              border-radius: 4px; width: auto; height: auto;
                              object-fit: contain; }
  .lupe-leer { width: min(620px, 90vw); aspect-ratio: 14/9; display: flex;
               align-items: center; justify-content: center;
               background: #0f172a; border: 1px solid #1e293b; color: #64748b; }
  .lupe-daten { margin-top: 0.6rem; display: flex; gap: 0.9rem; flex-wrap: wrap;
                align-items: baseline; color: #94a3b8; font-size: 0.82rem; }
  .lupe-daten b { color: var(--accent); font-size: 1.1rem;
                  font-family: ui-monospace, monospace; }
  .gruss { font-style: italic; color: #cbd5e1; }
  .zu { position: absolute; top: -0.6rem; right: -0.6rem; width: 2rem; height: 2rem;
        border-radius: 50%; background: #1e293b; color: #e2e8f0; border: 0;
        font-size: 1.2rem; line-height: 1; cursor: pointer; }
  .zu:hover { background: #334155; }
</style>
