"""Server-Sent Events streams — push live decodes + status to the frontend.

We use ``sse_starlette`` so we get correct heartbeat / disconnect
handling for free.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import asdict

from fastapi import APIRouter, Depends, Request
from sse_starlette.sse import EventSourceResponse

from ...runtime import Orchestrator
from ..deps import get_orchestrator

router = APIRouter()


@router.get("/sse/decodes")
async def stream_decodes(
    request: Request, orch: Orchestrator = Depends(get_orchestrator)
) -> EventSourceResponse:
    queue = orch.subscribe_decodes()

    async def gen() -> AsyncIterator[dict[str, str]]:
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=15.0)
                except TimeoutError:
                    yield {"event": "heartbeat", "data": ""}
                    continue
                payload = asdict(msg) if hasattr(msg, "__dataclass_fields__") else msg
                # ts is a datetime — make it JSON-serialisable
                if "ts" in payload and hasattr(payload["ts"], "isoformat"):
                    payload["ts"] = payload["ts"].isoformat()
                # Marinefunker-Badge (Sebastian v0.9.0): wenn FREMDER
                # call_from oder call_to ein aktiver Marinefunker ist,
                # mfnr ins Payload schreiben. Eigene Operator-Calls
                # (DK9XR, DO3XR…) werden ausgefiltert — sonst zeigt das
                # Badge auch bei Decodes die AN UNS gerichtet sind weil
                # DK9XR selbst MF #1039 ist (v0.9.2 Sebastian-Wunsch).
                from ...integrations.mf_lookup import get_mf_lookup
                _mf = get_mf_lookup()
                _my_calls = {
                    op.callsign.upper()
                    for op in orch.config.operators
                    if op.callsign
                } if orch.config.operators else {orch.config.operator.callsign.upper()}
                _from = (payload.get("call_from") or "").upper()
                _to = (payload.get("call_to") or "").upper()
                _hit = None
                if _from and _from not in _my_calls:
                    _hit = _mf.lookup(_from)
                if _hit is None and _to and _to not in _my_calls:
                    _hit = _mf.lookup(_to)
                payload["mf_mfnr"] = _hit.mfnr if _hit else None
                yield {"event": "decode", "data": json.dumps(payload, default=str)}
        finally:
            orch.unsubscribe_decodes(queue)

    return EventSourceResponse(gen())


@router.get("/sse/status")
async def stream_status(
    request: Request, orch: Orchestrator = Depends(get_orchestrator)
) -> EventSourceResponse:
    queue = orch.subscribe_status()

    async def gen() -> AsyncIterator[dict[str, str]]:
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    snap = await asyncio.wait_for(queue.get(), timeout=15.0)
                except TimeoutError:
                    yield {"event": "heartbeat", "data": ""}
                    continue
                payload = {
                    "callsign": snap.callsign,
                    "state": snap.state,
                    "last_lock_reason": snap.last_lock_reason,
                    "cq_count": snap.cq_count,
                    "current_qso_call": snap.current_qso_call,
                    "last_slot_index": snap.last_slot_index,
                    "last_decodes": snap.last_decodes,
                }
                yield {"event": "status", "data": json.dumps(payload)}
        finally:
            orch.unsubscribe_status(queue)

    return EventSourceResponse(gen())
