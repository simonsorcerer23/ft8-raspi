"""PSK-Reciprocity-State — schnelle "hat dieser Call uns recently gehört?"-Abfrage für den Hunting-Picker.

Sebastian v0.10.0 Hunt-Priority-Tiers:
    Nutzt den existierenden ``PskReporterClient.who_heard_me()`` als Datenquelle
    und cached die letzten Reception-Reports in einer flachen Map
    ``rx_call -> newest_received_at``. Picker-Hot-Path fragt nur ab
    ``cache.heard_us_recently(call, band) -> bool`` — O(1) Lookup.

Refresh-Strategie:
    * Periodic refresh durch den Orchestrator (Default 10 min)
    * Pro eigenem Operator-Call (DK9XR + DO3XR) einmal fetchen + mergen
    * Fail-open: bei Netzwerkfehler bleibt der alte Cache stehen
    * Stale-Cutoff im Lookup: nur Spots aus letzten 30 min sind relevant
      (HF-Bedingungen kippen schnell, alte Spots sind ungenau)

Warum nicht direkt im Client:
    * `who_heard_me` returnt fertige Reports — gut für Daten-Konsumenten
    * Aber Picker braucht O(1) "ja/nein"-Frage über Tausende Calls
    * Diese Schicht hält die transformierte Repräsentation
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..integrations.psk_reporter import HeardReport, PskReporterClient


def normalize_call(call: str) -> str:
    """Minimal normalization: uppercase, strip whitespace."""
    return (call or "").strip().upper()

log = logging.getLogger(__name__)

# Wie alt darf ein Spot maximal sein um noch als "uns gerade gehört"
# zu gelten? 30 min ist ein guter Kompromiss:
# * Zu kurz (5 min) → wir verpassen das Fenster zwischen PSK-Aggregation
#   (5 min Slots) und nächstem Refresh-Cycle
# * Zu lang (>1h) → Band-Conditions sind schon gekippt, Information stale
DEFAULT_FRESHNESS_S = 30 * 60

# Wenn ein Band-Filter gesetzt ist, akzeptieren wir Spots auf Frequenzen
# bis zu ±X Hz vom Sub-Band-Mittelpunkt. ±500 kHz deckt einen ganzen
# HF-Band-Bereich ab, ohne über die Band-Grenzen zu rutschen.
BAND_TOLERANCE_HZ = 500_000


@dataclass(slots=True)
class _Entry:
    """Ein Cache-Eintrag pro Empfänger-Call."""
    rx_call: str
    newest_at: datetime
    newest_band: str | None
    newest_snr_db: int | None
    count: int  # Anzahl Spots in der Cache-Window — Signal-Stärke der Reciprocity


@dataclass
class PskReciprocityCache:
    """Schnellabfrage-Cache: hat <call> uns recently gehört?"""

    # Normalisierter rx_call → neuester Spot-Eintrag
    entries: dict[str, _Entry] = field(default_factory=dict)
    last_refresh_at: float = 0.0
    last_refresh_ok: bool = False
    last_error: str | None = None
    total_reports: int = 0

    def heard_us_recently(
        self,
        call: str,
        *,
        now_t: float | None = None,
        freshness_s: int = DEFAULT_FRESHNESS_S,
        band: str | None = None,
    ) -> bool:
        """O(1) Antwort: kennen wir einen frischen Spot von <call>?

        Optional band-Filter: wenn gegeben, muss der jüngste Spot auf
        diesem Band sein. Verhindert "auf 20m gespottet, hunten auf 15m".
        """
        if now_t is None:
            now_t = time.time()
        norm = normalize_call(call)
        entry = self.entries.get(norm)
        if entry is None:
            return False
        age_s = now_t - entry.newest_at.timestamp()
        if age_s > freshness_s or age_s < 0:
            return False
        if band is not None and entry.newest_band is not None and entry.newest_band != band:
            return False
        return True

    def snr_into_partner(self, call: str) -> int | None:
        """Wenn wir wissen mit welchem SNR <call> uns hört, return das.

        Nützlich für UI-Anzeige ("KB1MBX hört uns mit +03 dB").
        """
        norm = normalize_call(call)
        entry = self.entries.get(norm)
        return entry.newest_snr_db if entry else None

    def update_from_reports(self, reports: list[HeardReport]) -> None:
        """In-place merge: aktualisiert entries mit frischen Reports.

        Bei Duplikat-rx_calls (mehrere Spots vom selben Empfänger) wird
        der NEUESTE behalten. Ein count-Counter zeigt wie viele Spots
        wir insgesamt für diesen Empfänger gesehen haben.
        """
        new_entries: dict[str, _Entry] = {}
        for r in reports:
            if not r.rx_call:
                continue
            norm = normalize_call(r.rx_call)
            existing = new_entries.get(norm)
            if existing is None or r.received_at > existing.newest_at:
                # received_at vom HeardReport ist datetime ohne TZ
                # (utcfromtimestamp in _parse_query). Wir behandeln's
                # als UTC.
                rt = r.received_at
                if rt.tzinfo is None:
                    rt = rt.replace(tzinfo=timezone.utc)
                new_entries[norm] = _Entry(
                    rx_call=norm,
                    newest_at=rt,
                    newest_band=r.band,
                    newest_snr_db=r.snr_db,
                    count=(existing.count + 1) if existing else 1,
                )
            elif existing is not None:
                existing.count += 1
        self.entries = new_entries
        self.last_refresh_at = time.time()
        self.last_refresh_ok = True
        self.last_error = None
        self.total_reports = len(reports)

    def mark_error(self, err: str) -> None:
        """Refresh hat nicht geklappt — alten Cache behalten."""
        self.last_error = err
        self.last_refresh_ok = False


async def refresh_from_psk(
    cache: PskReciprocityCache,
    psk_client: PskReporterClient,
    our_calls: list[str],
    hours: int = 1,
) -> None:
    """Fetch fresh reports für alle eigenen Calls + merge in den Cache.

    Wird vom Orchestrator als Background-Task aufgerufen, typisch alle
    10 min. Fehler werden im Cache vermerkt, der Picker arbeitet mit
    der zuletzt-bekannten Liste weiter (fail-open).
    """
    all_reports: list[HeardReport] = []
    last_error: str | None = None
    for call in our_calls:
        if not call:
            continue
        try:
            reports = await psk_client.who_heard_me(call, hours=hours)
            all_reports.extend(reports)
            log.info("psk_reciprocity: %s → %d reports", call, len(reports))
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("psk_reciprocity: fetch failed for %s: %s", call, exc)
            last_error = str(exc)
    if all_reports:
        cache.update_from_reports(all_reports)
    elif last_error:
        cache.mark_error(last_error)
    else:
        # Kein Fehler aber 0 reports — kann valide sein (nachts wenig
        # Stationen senden) → cache leeren statt stale Werte halten
        cache.update_from_reports([])
