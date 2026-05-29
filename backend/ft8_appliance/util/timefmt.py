"""Datetime-Serialisierung fuer API-Responses.

Problem (Sebastian 2026-05-28, Live-Konversation Anzeige-Bug):

SQLite speichert datetime-Werte als TEXT ohne Timezone-Info. Wir schreiben
sie zwar als ``datetime(tz=UTC)`` rein, beim Auslesen via SQLAlchemy
kommen sie aber als NAIVE datetime zurueck (tzinfo=None). Wenn wir dann
``dt.isoformat()`` aufrufen, fehlt der ``+00:00``-Suffix → das Frontend
``new Date(iso)`` interpretiert den String als LOKALZEIT statt UTC,
zeigt z.B. ``12:31`` statt ``14:31`` (CEST).

Lösung: alle datetime → ISO Konversionen die ueber die API rausgehen
nutzen diese Helper. Naive datetimes werden als UTC interpretiert (das
ist der Vertrag — alles im Backend ist UTC) und mit ``+00:00`` markiert
ausgegeben. TZ-aware datetimes werden in UTC normalisiert.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from pydantic import PlainSerializer


def iso_utc(dt: datetime | None) -> str | None:
    """Datetime → ISO-String mit explizitem ``+00:00``-TZ-Marker.

    - None → None (durchgereicht)
    - naive datetime → als UTC interpretiert, mit ``+00:00`` ausgegeben
    - tz-aware datetime → in UTC konvertiert, ISO mit ``+00:00``

    Bsp:
        >>> iso_utc(datetime(2026, 5, 28, 12, 37, 15))
        '2026-05-28T12:37:15+00:00'
        >>> iso_utc(datetime(2026, 5, 28, 12, 37, 15, tzinfo=UTC))
        '2026-05-28T12:37:15+00:00'
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    else:
        dt = dt.astimezone(UTC)
    return dt.isoformat()


# v0.23.0 — Pydantic-Annotated-Typ fuer Response-Model-Felder. Statt
# ``ts: datetime`` (Pydantic serialisiert naive ohne TZ-Suffix → Frontend-
# Bug) schreibt man ``ts: UtcDateTime`` und kriegt automatisch
# ``...+00:00``. Naive werden als UTC interpretiert (Backend-Vertrag).
#
# when_used='json' damit interne Python-Nutzung (z.B. Vergleiche in Tests)
# den datetime-Typ behaelt — nur die JSON-Serialisierung wird angefasst.
UtcDateTime = Annotated[
    datetime,
    PlainSerializer(iso_utc, return_type=str, when_used="json"),
]
UtcDateTimeOpt = Annotated[
    datetime | None,
    PlainSerializer(iso_utc, return_type=(str | None), when_used="json"),
]
