<script>
  // v0.48.0 — Demo-Modus-Schalter. demo_mode bestimmt die Decode-Quelle
  // (FT8-Simulator mit fiktiven Calls statt echtem ALSA-RX) und greift erst
  // nach Service-Neustart → der Button persistiert + startet neu.
  import { onMount } from 'svelte';
  import { api } from '../lib/api.js';
  import { t } from '../lib/i18n.svelte.js';

  let enabled = $state(false);
  let busy = $state(false);
  let msg = $state(null);
  let loaded = $state(false);

  async function load() {
    try {
      const c = await api.config();
      enabled = !!c.demo_mode;
      loaded = true;
    } catch { /* ignore */ }
  }
  onMount(load);

  async function toggle() {
    if (busy) return;
    const target = !enabled;
    if (!confirm(target
        ? t('demo.confirm_on')
        : t('demo.confirm_off'))) return;
    busy = true; msg = null;
    try {
      await api.setDemoMode(target);
      enabled = target;
      msg = t('demo.restarting');
    } catch (e) {
      msg = `${t('common.error')}: ${e.message}`;
    } finally { busy = false; }
  }
</script>

<div class="panel">
  <h3>{t('demo.title')}</h3>
  {#if loaded}
    <div class="row">
      <span class="state {enabled ? 'on' : 'off'}">{enabled ? t('demo.on') : t('demo.off')}</span>
      <button class="btn" class:danger={enabled} onclick={toggle} disabled={busy}>
        {busy ? '…' : (enabled ? t('demo.turn_off') : t('demo.turn_on'))}
      </button>
    </div>
    {#if msg}<div class="msg">{msg}</div>{/if}
  {:else}
    <div class="msg">{t('demo.loading')}</div>
  {/if}
</div>

<style>
  .panel { background: var(--panel); border-radius: 8px; padding: 0.8rem; }
  h3 { margin: 0 0 0.6rem; color: var(--accent); font-size: 0.95rem; }
  .row { display: flex; align-items: center; gap: 0.8rem; flex-wrap: wrap; }
  .state { font-weight: 700; font-family: ui-monospace, monospace; font-size: 0.85rem;
           padding: 0.2rem 0.6rem; border-radius: 999px; }
  .state.on  { color: #fbbf24; background: rgba(245,158,11,0.16); }
  .state.off { color: #4ade80; background: rgba(34,197,94,0.14); }
  .btn { background: rgba(56,189,248,0.12); border: 1px solid #334155; color: var(--accent);
         border-radius: 6px; padding: 0.35rem 0.8rem; cursor: pointer; font-size: 0.85rem; }
  .btn:hover { border-color: var(--accent); }
  .btn.danger { color: #fbbf24; }
  .btn:disabled { opacity: 0.5; cursor: not-allowed; }
  .msg { margin-top: 0.5rem; font-size: 0.8rem; color: #94a3b8; }
</style>
