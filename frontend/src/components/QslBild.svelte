<script>
  // Ein einzelnes Kartenmotiv, nachgeladen sobald es in Sicht kommt.
  //
  // Warum nicht einfach <img src="/api/qsl/…">: Die Schnittstelle liegt
  // hinter der Token-Pruefung, und ein img-Element kann keinen
  // Authorization-Header setzen — es bekaeme 401. Das Token in die
  // Adresse zu haengen verbietet sich, es stuende dann in jedem
  // Server-Log und im Verlauf des Browsers. Also holt api.qslBildUrl die
  // Daten selbst und liefert eine blob:-Adresse.
  //
  // Die ist asynchron. Genau daran ist die erste Fassung gescheitert:
  // src={api.qslBildUrl(id)} setzte ein Promise als Adresse ein, und jede
  // Kachel zeigte ihren Alt-Text.
  //
  // Der Beobachter spart den Rest: Bei 60 Kacheln je Seite waeren das 60
  // gleichzeitige Abrufe, von denen die meisten unter dem Bildschirmrand
  // liegen. Geladen wird, was zu sehen ist.
  import { onDestroy } from 'svelte';
  import { api } from '../lib/api.js';

  let { id, alt = '' } = $props();

  let url = $state(null);
  let fehler = $state(false);
  let el = $state(null);

  function freigeben() {
    if (url) { URL.revokeObjectURL(url); url = null; }
  }

  async function laden() {
    try {
      url = await api.qslBildUrl(id);
    } catch {
      fehler = true;
    }
  }

  // Sichtbarkeit beobachten. Ohne IntersectionObserver (alte Browser,
  // Testumgebungen) wird sofort geladen — lieber etwas zu viel als nichts.
  $effect(() => {
    if (!el || url || fehler) return;
    if (typeof IntersectionObserver !== 'function') { laden(); return; }
    const beobachter = new IntersectionObserver((eintraege) => {
      for (const e of eintraege) {
        if (e.isIntersecting) { beobachter.disconnect(); laden(); }
      }
    }, { rootMargin: '300px' });
    beobachter.observe(el);
    return () => beobachter.disconnect();
  });

  onDestroy(freigeben);
</script>

<span class="huelle" bind:this={el}>
  {#if url}
    <img src={url} {alt} />
  {:else if fehler}
    <span class="leer">—</span>
  {:else}
    <span class="leer"><i></i></span>
  {/if}
</span>

<style>
  .huelle, .huelle img { display: block; width: 100%; height: 100%; }
  .huelle img { object-fit: cover; }
  .leer { display: flex; align-items: center; justify-content: center;
          height: 100%; color: #334155; }
  /* Ein ruhiger Puls, solange das Bild unterwegs ist — kein Spinner,
     der bei sechzig Kacheln flimmert. */
  .leer i { width: 28px; height: 3px; background: #1e293b; border-radius: 2px;
            animation: atmen 1.6s ease-in-out infinite; }
  @keyframes atmen {
    0%, 100% { opacity: 0.35; }
    50%      { opacity: 0.9; }
  }
  @media (prefers-reduced-motion: reduce) {
    .leer i { animation: none; }
  }
</style>
