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
                # Marinefunker-Badge (Sebastian v0.9.0): wenn call_from
                # ODER call_to ein aktiver Marinefunker ist, mfnr ins
                # Payload schreiben — Frontend rendert ⚓-Badge an der
                # Zeile. Bei beiden Mitgliedern gewinnt call_from.
                from ..integrations.mf_lookup import get_mf_lookup
                _mf = get_mf_lookup()
                _from = payload.get("call_from")
                _to = payload.get("call_to")
                _hit = (_mf.lookup(_from) if _from else None) or (_mf.lookup(_to) if _to else None)
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
