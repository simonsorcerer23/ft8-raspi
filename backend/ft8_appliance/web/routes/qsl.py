"""eQSL-Posteingang: durchblaettern, was andere uns bestaetigt haben.

Die Metadaten stehen in der Datenbank, die Kartenbilder liegen als Dateien
daneben. Beides fuellt der Posteingangs-Loop im Orchestrator; hier wird
nur gelesen.

Warum die Bilder ueber uns gehen und nicht direkt von eQSL: Deren
Grafiken werden im Moment des Abrufs erzeugt, liegen in einem temporaeren
Ordner und werden nach Stunden geloescht. Eine von dort verlinkte Adresse
zeigt also bald ins Leere. Dazu kommt das Tempolimit von sechs Karten je
Minute — das haelt kein Seitenbesuch ein, wohl aber ein Hintergrundloop,
der sich Zeit lassen darf.
"""

from __future__ import annotations


from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy import func, select

from ...db import session_scope
from ...db.models import QslKarte
from ...runtime import Orchestrator
from ..deps import get_orchestrator

router = APIRouter()


class QslOut(BaseModel):
    id: int
    call: str
    qso_date: str
    time_on: str
    band: str
    mode: str
    empfangen_am: str | None = None
    gridsquare: str | None = None
    nachricht: str | None = None
    # Liegt das Bild schon vor? Sonst zeigt die Oberflaeche einen
    # Platzhalter statt eines toten Bildes — die Karte kommt nach.
    bild_da: bool = False
    bytes: int | None = None


class QslListe(BaseModel):
    karten: list[QslOut]
    gesamt: int
    mit_bild: int
    offset: int


@router.get("/qsl", response_model=QslListe)
async def liste(
    limit: int = Query(60, ge=1, le=300),
    offset: int = Query(0, ge=0),
    call: str | None = Query(None, description="nach Rufzeichen filtern"),
    band: str | None = Query(None),
    nur_mit_bild: bool = Query(False),
    orch: Orchestrator = Depends(get_orchestrator),
) -> QslListe:
    """Die Bestaetigungen, neueste zuerst.

    Sortiert nach dem Eingangsdatum bei eQSL, nicht nach dem QSO-Datum:
    Wer den Posteingang aufschlaegt, will sehen, was gerade hereinkam —
    auch wenn die Verbindung selbst Jahre zurueckliegt.
    """
    bedingungen = []
    if call:
        bedingungen.append(QslKarte.call == call.upper().strip())
    if band:
        bedingungen.append(QslKarte.band == band.upper().strip())
    if nur_mit_bild:
        bedingungen.append(QslKarte.datei.is_not(None))

    async with session_scope() as s:
        grund = select(QslKarte)
        for b in bedingungen:
            grund = grund.where(b)
        reihen = list((await s.execute(
            grund.order_by(QslKarte.empfangen_am.desc(), QslKarte.id.desc())
            .limit(limit).offset(offset)
        )).scalars())

        zaehler = select(func.count()).select_from(QslKarte)
        for b in bedingungen:
            zaehler = zaehler.where(b)
        gesamt = (await s.execute(zaehler)).scalar() or 0
        mit_bild = (await s.execute(
            select(func.count()).select_from(QslKarte)
            .where(QslKarte.datei.is_not(None))
        )).scalar() or 0

        return QslListe(
            karten=[
                QslOut(
                    id=k.id, call=k.call, qso_date=k.qso_date, time_on=k.time_on,
                    band=k.band, mode=k.mode, empfangen_am=k.empfangen_am,
                    gridsquare=k.gridsquare, nachricht=k.nachricht,
                    bild_da=k.datei is not None, bytes=k.bytes,
                )
                for k in reihen
            ],
            gesamt=gesamt, mit_bild=mit_bild, offset=offset,
        )


@router.get("/qsl/{karten_id}/bild")
async def bild(
    karten_id: int,
    orch: Orchestrator = Depends(get_orchestrator),
) -> Response:
    """Das Kartenmotiv ausliefern.

    404 heisst hier zweierlei: die Karte gibt es nicht, oder ihr Bild ist
    noch nicht geholt. Beides ist fuer den Aufrufer dasselbe — er soll
    den Platzhalter zeigen und spaeter wiederkommen.
    """
    async with session_scope() as s:
        k = (await s.execute(
            select(QslKarte).where(QslKarte.id == karten_id)
        )).scalar_one_or_none()
        if k is None or not k.datei:
            raise HTTPException(status_code=404, detail="Karte noch nicht vorhanden")
        wurzel = orch.qsl_verzeichnis().resolve()
        pfad = (wurzel / k.datei).resolve()
        # Der Dateiname kommt aus fremden Rufzeichen. Ein Eintrag mit
        # "../" darin duerfte nie entstehen, aber pruefen kostet nichts —
        # und der Fehler waere ein Dateizugriff ausserhalb des Ordners.
        if not pfad.is_relative_to(wurzel) or not pfad.is_file():
            raise HTTPException(status_code=404, detail="Datei fehlt")
        return Response(
            content=pfad.read_bytes(),
            media_type=k.medientyp or "image/jpeg",
            headers={
                # Die Bilder aendern sich nie — einmal geholt, bleibt es
                # dieselbe Karte. Ein Tag Cache spart beim Blaettern.
                "Cache-Control": "private, max-age=86400",
                "Content-Disposition": f'inline; filename="{k.schluessel}.jpg"',
            },
        )


class QslStand(BaseModel):
    gesamt: int
    mit_bild: int
    offen: int
    fehlerhaft: int
    neueste: str | None = None


@router.get("/qsl/stand", response_model=QslStand)
async def stand(orch: Orchestrator = Depends(get_orchestrator)) -> QslStand:
    """Wie weit der Abruf ist — fuer die Fortschrittsanzeige."""
    async with session_scope() as s:
        gesamt = (await s.execute(
            select(func.count()).select_from(QslKarte))).scalar() or 0
        mit_bild = (await s.execute(
            select(func.count()).select_from(QslKarte)
            .where(QslKarte.datei.is_not(None)))).scalar() or 0
        fehlerhaft = (await s.execute(
            select(func.count()).select_from(QslKarte)
            .where(QslKarte.fehler.is_not(None)))).scalar() or 0
        neueste = (await s.execute(
            select(func.max(QslKarte.empfangen_am)))).scalar()
    return QslStand(
        gesamt=gesamt, mit_bild=mit_bild,
        offen=gesamt - mit_bild - fehlerhaft,
        fehlerhaft=fehlerhaft, neueste=neueste,
    )
