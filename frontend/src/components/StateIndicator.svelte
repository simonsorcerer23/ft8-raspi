<script>
  import { statusStore } from '../lib/stores.svelte.js';
  import { t } from '../lib/i18n.svelte.js';

  const state = $derived(statusStore.value.state ?? 'UNKNOWN');
  const callsign = $derived(statusStore.value.callsign ?? '—');

  const labels = $derived({
    IDLE: t('stateind.IDLE'), CQ_CALLING: t('stateind.CQ_CALLING'),
    QSO_RESPOND: t('stateind.QSO_RESPOND'), QSO_REPORT: t('stateind.QSO_REPORT'),
    QSO_LOG: t('stateind.QSO_LOG'), TX_LOCKED: t('stateind.TX_LOCKED'),
    UNKNOWN: t('stateind.UNKNOWN'),
  });

  const cls = $derived(state.toLowerCase().replace('_', '-'));
</script>

<div class="banner {cls}">
  <div class="call">{callsign}</div>
  <div class="state-name">{labels[state] ?? state}</div>
</div>

<style>
  .banner {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 0.6rem 1rem;
    border-radius: 8px;
    background: var(--panel);
    border-left: 5px solid #475569;
  }
  .banner.cq-calling, .banner.qso-respond, .banner.qso-report, .banner.qso-log {
    border-left-color: var(--accent);
  }
  .banner.tx-locked { border-left-color: var(--danger); background: rgba(239,68,68,0.1); }
  .banner.idle { border-left-color: var(--ok); }
  .call { font-family: ui-monospace, monospace; font-size: 1.3rem; font-weight: 700; }
  .state-name { font-size: 1rem; color: #cbd5e1; text-transform: uppercase; letter-spacing: 0.1em; }
</style>
