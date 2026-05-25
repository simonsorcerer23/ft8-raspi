"""Read-only status endpoint backed by the orchestrator."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select

from ...db import session_scope
from ...db.models import Decode
from ...runtime import Orchestrator
from ..deps import get_orchestrator

router = APIRouter()


class RigSnapshotOut(BaseModel):
    freq_hz: int | None = None
    mode: str | None = None
    bandwidth_hz: int | None = None
    ptt: bool | None = None
    swr: float | None = None
    rfpower_norm: float | None = None
    rfpower_meter: float | None = None
    s_meter_db: int | None = None
    alc: float | None = None
    af_gain: float | None = None
    rf_gain: float | None = None
    nr_level: float | None = None
    preamp_on: bool | None = None
    att_on: bool | None = None
    nb_on: bool | None = None
    agc_mode: str | None = None
    vfo: str | None = None
    split_on: bool | None = None
    battery_v: float | None = None
    internal_temp_c: float | None = None


class GpsSnapshotOut(BaseModel):
    mode: int
    lat: float | None
    lon: float | None
    alt: float | None
    time_iso: str | None
    sats_seen: int
    sats_used: int


class StatusResponse(BaseModel):
    callsign: str | None
    state: str
    last_lock_reason: str | None = None
    cq_count: int = 0
    current_qso_call: str | None = None
    # Flag-Emoji des current_qso_call (Sebastian v0.3.0). Leer wenn kein
    # QSO aktiv oder Call nicht in DXCC-Tabelle mapbar.
    current_qso_flag: str = ""
    last_slot_index: int = -1
    last_decodes: int = 0
    auto_answer: bool = False
    auto_cq: bool = False
    tx_power_w: int = 10
    active_antenna: str | None = None
    worked_count: int = 0
    blacklist_count: int = 0
    rig: RigSnapshotOut
    gps: GpsSnapshotOut
    # License-Framework: surface fürs Frontend damit der Slider den
    # richtigen Max-Wert pro Band setzen kann.
    license_class: str = "A"
    active_band: str | None = None
    effective_max_power_w: int | None = None
    # RX-Audio-Pegel in dBFS (-∞..0), aus ALSA-Capture-RMS berechnet.
    # Workaround für broken Hamlib-STRENGTH bei IC-7300/PKTUSB.
    rx_audio_dbfs: float | None = None
    # ALC-Closed-Loop-Telemetrie. audio_gain ist der live trimmend
    # geführte Pegel-Faktor (0.05..1.0); last_alc_pct ist die letzte
    # ALC-Lesung in % die der Loop sah. Frontend rendert beides im
    # RigPanel als kleine Status-Zelle.
    audio_gain: float | None = None
    last_alc_pct: int | None = None
    # Digital-Mode (FT8/FT4) — wird im RigPanel als Tag angezeigt damit
    # der Operator sieht in welchem Slot-Tempo der Pi laeuft.
    # Sebastian-Bug v0.4.1: das UI-Tag las statusStore.value.mode aber
    # das Feld war nie im API-Output -> fiel immer auf 'FT8' default.
    mode: str = "FT8"
    # CQ-Direction-Tag (Audit F7 v0.3.4) — leer = klassischer CQ, sonst
    # "DX"/"EU"/"POTA"/... fuer Anzeige im RigPanel.
    cq_directed: str = ""
    # v0.6.3: Decoder-Mode sichtbar. decoder_mode = was im Config
    # gewuenscht; actual_decoder_mode = was die Pipeline jetzt wirklich
    # benutzt (kann durch CPU-adaptive Fallback abweichen).
    decoder_mode: str = "standard"
    actual_decoder_mode: str = "standard"
    decoder_late_slot_count: int = 0


@router.get("/status", response_model=StatusResponse)
async def get_status(orch: Orchestrator = Depends(get_orchestrator)) -> StatusResponse:
    from ...integrations.flags import flag_for_call as _flag_for_call
    s = orch.status()
    # getattr-Guard: FakeOrchestrator in den tests hat kein integrations-Feld
    cty = getattr(getattr(orch, "integrations", None), "cty", None)
    current_qso_flag = _flag_for_call(s.current_qso_call, cty)
    return StatusResponse(
        callsign=s.callsign,
        state=s.state,
        last_lock_reason=s.last_lock_reason,
        cq_count=s.cq_count,
        current_qso_call=s.current_qso_call,
        current_qso_flag=current_qso_flag,
        last_slot_index=s.last_slot_index,
        last_decodes=s.last_decodes,
        auto_answer=s.auto_answer,
        auto_cq=s.auto_cq,
        tx_power_w=s.tx_power_w,
        active_antenna=s.active_antenna,
        worked_count=s.worked_count,
        blacklist_count=s.blacklist_count,
        rig=RigSnapshotOut(
            freq_hz=s.rig.freq_hz, mode=s.rig.mode,
            bandwidth_hz=s.rig.bandwidth_hz, ptt=s.rig.ptt,
            swr=s.rig.swr, rfpower_norm=s.rig.rfpower_norm,
            rfpower_meter=s.rig.rfpower_meter,
            s_meter_db=s.rig.s_meter_db, alc=s.rig.alc,
            af_gain=s.rig.af_gain, rf_gain=s.rig.rf_gain,
            nr_level=s.rig.nr_level,
            preamp_on=s.rig.preamp_on, att_on=s.rig.att_on,
            nb_on=s.rig.nb_on, agc_mode=s.rig.agc_mode,
            vfo=s.rig.vfo, split_on=s.rig.split_on,
            battery_v=s.rig.battery_v,
            internal_temp_c=s.rig.internal_temp_c,
        ),
        gps=GpsSnapshotOut(
            mode=s.gps.mode,
            lat=s.gps.lat,
            lon=s.gps.lon,
            alt=s.gps.alt,
            time_iso=s.gps.time_iso,
            sats_seen=s.gps.sats_seen,
            sats_used=s.gps.sats_used,
        ),
        license_class=s.license_class,
        active_band=s.active_band,
        effective_max_power_w=s.effective_max_power_w,
        rx_audio_dbfs=s.rx_audio_dbfs,
        audio_gain=s.audio_gain,
        last_alc_pct=s.last_alc_pct,
        # getattr-Guard wie bei den Flag-Lookups: FakeOrchestrator
        # in den Tests hat kein config-Attribut.
        mode=getattr(getattr(orch, "config", None), "operating", None)
              and orch.config.operating.mode or "FT8",
        cq_directed=(getattr(getattr(orch, "config", None), "operating", None)
                     and (orch.config.operating.cq_directed or "").upper() or ""),
        # v0.6.3: Decoder-Mode (configured + actual)
        decoder_mode=getattr(s, "decoder_mode", "standard"),
        actual_decoder_mode=getattr(s, "actual_decoder_mode", "standard"),
        decoder_late_slot_count=getattr(s, "decoder_late_slot_count", 0),
    )


# ---------------------------------------------------------------------------
# QSO conversation — the running dialog the user is *part of* right now.
class ConvEntry(BaseModel):
    direction: str   # "tx" | "rx" | "next"
    ts: str | None   # ISO timestamp (None for "next" predictions)
    message: str
    kind: str | None = None  # cq | respond_grid | respond_report | r_report | rr73 | log | …


class ConversationResponse(BaseModel):
    op_mode: str           # "cq" | "hunt" | "off"
    state: str
    partner_call: str | None
    partner_grid: str | None
    partner_snr_received: int | None  # SNR they reported to us
    our_snr_sent: int | None          # SNR we reported to them
    started_at: str | None
    entries: list[ConvEntry]
    next_action_hint: str | None      # what we'll do next slot
    # Flag-Emoji des Partner-Callsigns (Sebastian v0.3.0). Leer wenn
    # kein Partner aktiv oder Call nicht mapbar.
    partner_flag: str = ""


@router.get("/qso/conversation", response_model=ConversationResponse)
async def conversation(
    orch: Orchestrator = Depends(get_orchestrator),
) -> ConversationResponse:
    s = orch.status()
    sm = orch.state_machine
    qso = sm.qso
    state = sm.state.name

    op_mode = (
        "cq" if state in ("CQ_CALLING", "QSO_RESPOND", "QSO_REPORT")
        else "hunt" if sm.ctx.auto_answer
        else "off"
    )

    entries: list[ConvEntry] = []
    my_call = sm.ctx.callsign
    partner = qso.their_call if qso else None

    # Our recent TX_MESSAGEs from the action log. Dedupe identical
    # consecutive (message, kind) Paare — die State-Machine repeated
    # die gleiche Message wenn der Partner nicht antwortet, im Feed
    # sah das aus wie zwei identische Einträge übereinander.
    # Timestamp aus dem LoggedAction-Wrapper — vorher hatten alle TX-
    # Eintraege ``now_iso`` (API-Call-Zeit), wodurch im UI 7 verschiedene
    # TX-Slots alle die gleiche Uhrzeit zeigten und chronologisch falsch
    # neben den echten RX-Decode-Timestamps standen.
    tx_actions = [a for a in orch._action_log[-30:] if a.kind == "TX_MESSAGE"]
    seen_last: tuple[str, str | None] | None = None
    for a in tx_actions:
        msg = a.payload.get("message", "")
        kind = a.payload.get("kind")
        if seen_last == (msg, kind):
            continue
        seen_last = (msg, kind)
        entries.append(ConvEntry(
            direction="tx", ts=a.ts.isoformat(), message=msg, kind=kind,
        ))

    # RX-Decodes für die Konversation. Wechsel von In-Memory-Cache
    # (`_last_decodes`, limit 30) auf DB-Pull — der In-Memory-Buffer
    # ist nach ~1.5 Slots schon wieder voll mit anderen Stationen,
    # und ein QSO das 4-5 Slots dauert verliert seine eigene
    # Historie. DB hat alle Decodes seit Service-Start.
    if qso is not None and qso.started:
        # Laufendes QSO: ab QSO-Start, alles vom Partner + alles
        # an uns gerichtet.
        since = qso.started - timedelta(seconds=30)  # 2 slots Puffer
    else:
        # Kein QSO aktiv — letzte 5 Min Decodes an uns (für
        # "wer hat mich grade gerufen" Visibility)
        since = datetime.now(UTC) - timedelta(minutes=5)

    async with session_scope() as sess:
        stmt = (
            select(Decode.ts, Decode.call_from, Decode.call_to, Decode.message)
            .where(Decode.ts >= since)
            .order_by(Decode.ts.desc())
            .limit(50)
        )
        rows = (await sess.execute(stmt)).all()
    # Sebastian v0.5.2: Im IDLE-State zusaetzlich RX-Decodes von allen
    # Stationen einbeziehen die wir in den letzten Minuten angesprochen
    # haben. Sonst sieht der Operator im IDLE nur eine TX-only-Liste
    # ohne die Partner-Antworten (Reports, RR73s) die nach unserem
    # Hunting-Anstoss kamen. Mit dieser Liste wird die Conversation-
    # Anzeige auch zwischen QSOs lesbar.
    recent_tx_targets: set[str] = set()
    for a in tx_actions:
        msg = a.payload.get("message", "")
        parts = msg.split()
        if parts:
            recent_tx_targets.add(parts[0])
    for row in rows:
        if (
            row.call_to == my_call
            or (partner and row.call_from == partner)
            or (row.call_from and row.call_from in recent_tx_targets)
        ):
            entries.append(ConvEntry(
                direction="rx",
                ts=row.ts.isoformat() if hasattr(row.ts, "isoformat") else str(row.ts),
                message=row.message,
            ))

    # Sort newest-first so the operator sees the latest exchange at the top
    # of the panel without scrolling. Within a QSO this still reads "newer
    # turn above older turn" which matches the Telegram/Slack-style mental
    # model people have for live feeds.
    entries.sort(key=lambda e: e.ts or "", reverse=True)

    # Predict next action based on state
    # Sebastian v0.5.3: QSO_GRACE-Branch ergaenzt (vorher hint=None,
    # leer im UI) + IDLE differenziert auto_cq vs. komplett-off.
    hint = None
    if state == "CQ_CALLING":
        hint = f"sendet weiter CQ {my_call} bis jemand antwortet"
    elif state == "QSO_RESPOND" and qso:
        hint = f"erwartet Signal-Report von {qso.their_call}"
    elif state == "QSO_REPORT" and qso:
        hint = f"erwartet RR73 von {qso.their_call}"
    elif state == "QSO_GRACE":
        grace_partner = getattr(sm, "_grace_partner_call", None) or "Partner"
        hint = (
            f"QSO mit {grace_partner} abgeschlossen — lauscht noch einen Slot "
            "ob er Wiederholung schickt"
        )
    elif state == "IDLE":
        if sm.ctx.auto_answer and sm.ctx.auto_cq:
            hint = "Hunt + CQ aktiv: hört auf CQs und ruft selber wenn nichts kommt"
        elif sm.ctx.auto_answer:
            hint = "hört auf hörbare CQs zum Beantworten"
        elif sm.ctx.auto_cq:
            hint = "wartet auf naechsten Slot um CQ zu rufen"
        else:
            hint = "wartet — drücke CQ oder Antworten"
    elif state == "TX_LOCKED":
        hint = f"TX gesperrt: {sm.ctx.last_lock_reason}"

    partner_call_val = qso.their_call if qso else None
    from ...integrations.flags import flag_for_call as _flag_for_call
    cty = getattr(getattr(orch, "integrations", None), "cty", None)
    partner_flag = _flag_for_call(partner_call_val, cty)
    return ConversationResponse(
        op_mode=op_mode,
        state=state,
        partner_call=partner_call_val,
        partner_grid=qso.their_grid if qso else None,
        partner_snr_received=qso.our_snr_received if qso else None,
        our_snr_sent=qso.their_snr if qso else None,
        started_at=qso.started.isoformat() if qso else None,
        entries=entries[-20:],  # last 20 entries
        next_action_hint=hint,
        partner_flag=partner_flag,
    )
