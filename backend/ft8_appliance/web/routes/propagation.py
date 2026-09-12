"""Ausbreitungskarten als Kartenebene.

Bisher liefert die Station Ausbreitungsdaten nur als Punkt-zu-Punkt-Werte
(``PathPrediction``, zwanzig Zielfelder). Hier kommt die Flaeche dazu: die
stuendlich gerechnete MUF-Weltkarte von prop.kc2g.com, ausgeliefert als
Bild in gleichabstaendiger Zylinderprojektion, das die Karte im Frontend
direkt ueber die Erdkanten legen kann.

Das Bild wird traege geholt (erst beim Einschalten der Ebene) und
hoechstens stuendlich erneuert — siehe ``integrations.muf_map``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Response
from pydantic import BaseModel

from ...integrations.muf_map import MufKartenCache

router = APIRouter()

_cache = MufKartenCache(Path("/var/lib/ft8-appliance/muf_map.png"))


class MufKartenStand(BaseModel):
    """Woher das Bild stammt und wie alt es ist.

    Ohne diese Angaben waere eine veraltete Karte von einer aktuellen nicht
    zu unterscheiden — sie sieht ja genauso aus.
    """

    verfuegbar: bool
    stand: str | None = None        # ISO-UTC des Abrufs
    alter_s: float | None = None
    veraltet: bool = False          # aelter als die stuendliche Neurechnung
    breite: int | None = None
    hoehe: int | None = None
    quelle: str = "prop.kc2g.com"


@router.get("/propagation/muf-map", response_model=MufKartenStand)
def muf_karten_stand() -> MufKartenStand:
    karte = _cache.hole()
    if karte is None:
        return MufKartenStand(verfuegbar=False)
    return MufKartenStand(
        verfuegbar=True,
        stand=datetime.fromtimestamp(karte.geholt_at, UTC).isoformat(),
        alter_s=round(karte.alter_s, 1),
        veraltet=karte.alter_s > 3600.0,
        breite=karte.breite,
        hoehe=karte.hoehe,
    )


@router.get("/propagation/muf-map.png")
def muf_karte_bild() -> Response:
    karte = _cache.hole()
    if karte is None:
        # 503 statt 404: Die Ebene gibt es, nur gerade kein Bild. Das
        # Frontend unterscheidet daran „noch nie geladen" von „Pfad falsch".
        return Response(status_code=503, content=b"", media_type="image/png")
    return Response(
        content=karte.png,
        media_type="image/png",
        headers={
            # Der Browser darf es eine Viertelstunde behalten; laenger nicht,
            # sonst ueberlebt ein altes Bild die stuendliche Neurechnung.
            "Cache-Control": "public, max-age=900",
            "X-MUF-Alter-S": f"{karte.alter_s:.0f}",
        },
    )
