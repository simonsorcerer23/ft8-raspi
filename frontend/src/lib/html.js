// HTML-Escaping fuer die wenigen Stellen, an denen Text als HTML gesetzt wird
// (Leaflet-Popups und -Tooltips). Svelte-Templates escapen selbst; Leaflets
// bindPopup()/bindTooltip() tun es nicht.
const MAP = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };

/** Escaped einen beliebigen Wert; null/undefined werden zum leeren String. */
export function esc(v) {
  return String(v ?? '').replace(/[&<>"']/g, (c) => MAP[c]);
}
