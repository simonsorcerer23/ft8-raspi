<script>
  // Verbindungsverlust zur Station sichtbar machen (Audit 14.11.2026, F5).
  // Vorher las keine Komponente statusStore.error/lastFetch: Fiel die Box
  // aus, stand die Oberflaeche mit den letzten Werten einfach still.
  //
  // Massstab ist das Alter des letzten ERFOLGREICHEN Abrufs, nicht der
  // Fehler des letzten Versuchs — ein haengender Abruf meldet keinen Fehler.
  import { statusStore } from '../lib/stores.svelte.js';
  import { t } from '../lib/i18n.svelte.js';

  const STALE_MS = 15_000;   // drei verpasste 5-s-Abrufe

  let now = $state(Date.now());
  // Im Hintergrund drosselt der Browser die Abrufe. Kommt der Tab zurueck,
  // zaehlt die Frist neu — sonst blitzt die Meldung bei jedem Tab-Wechsel auf.
  let visibleSince = $state(Date.now());

  $effect(() => {
    const id = setInterval(() => { now = Date.now(); }, 1_000);
    const onVisible = () => {
      if (document.visibilityState === 'visible') {
        visibleSince = Date.now();
        now = visibleSince;
        statusStore.refresh();
      }
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      clearInterval(id);
      document.removeEventListener('visibilitychange', onVisible);
    };
  });

  // Bezugspunkt: letzter erfolgreicher Abruf; gab es noch keinen, der erste Versuch.
  const since = $derived(statusStore.lastFetch || statusStore.firstAttempt);
  const lost = $derived(since > 0 && now - Math.max(since, visibleSince) > STALE_MS);
  const sinceUtc = $derived(since ? new Date(since).toISOString().slice(11, 19) : '');

  function age(ms) {
    const s = Math.max(0, Math.round(ms / 1000));
    if (s < 60) return `${s} s`;
    const m = Math.floor(s / 60);
    if (m < 60) return `${m} min ${s % 60} s`;
    return `${Math.floor(m / 60)} h ${m % 60} min`;
  }
</script>

{#if lost}
  <div class="conn-lost" role="alert">
    <strong>
      {statusStore.lastFetch
        ? t('conn.lost_since', { time: sinceUtc, age: age(now - since) })
        : t('conn.never', { time: sinceUtc, age: age(now - since) })}
    </strong>
    {#if statusStore.error}<span class="why">{statusStore.error}</span>{/if}
  </div>
{/if}

<style>
  .conn-lost {
    position: sticky;
    top: 0;
    z-index: 1100;
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    gap: 0.3rem 0.8rem;
    padding: 0.6rem 0.9rem;
    border-radius: 8px;
    background: #7f1d1d;
    border: 1px solid var(--danger, #ef4444);
    color: #fee2e2;
  }
  .why {
    font-family: ui-monospace, monospace;
    font-size: 0.78rem;
    color: #fecaca;
    word-break: break-word;
  }
</style>
