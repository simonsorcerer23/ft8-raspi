<script>
  import { statusStore } from '../lib/stores.svelte.js';

  const state = $derived(statusStore.value.state ?? 'UNKNOWN');
  const callsign = $derived(statusStore.value.callsign ?? '—');

  const labels = {
    IDLE:        'BEREIT',
    CQ_CALLING:  'CQ läuft',
    QSO_RESPOND: 'antworte…',
    QSO_REPORT:  'Report…',
    QSO_LOG:     'logge…',
    TX_LOCKED:   'TX gesperrt',
    UNKNOWN:     '…',
  };

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
