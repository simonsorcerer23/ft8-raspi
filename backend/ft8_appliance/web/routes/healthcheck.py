"""``/api/healthcheck`` — structured JSON twin of ``scripts/pi-check.sh``.

Reads from the orchestrator's snapshot of hardware state + a fresh
read of the Pi-side system health module.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ...runtime import Orchestrator
from ...util.system_health import read_chrony_tracking, read_pi_status
from ..deps import get_orchestrator

log = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
class HealthSection(BaseModel):
    status: str  # "ok" | "warn" | "fail" | "unknown"
    details: dict[str, Any] = {}


class HealthCheckResponse(BaseModel):
    overall: str  # "green" | "yellow" | "red"
    generated_at: float
    uptime_s: float
    sections: dict[str, HealthSection]


# ---------------------------------------------------------------------------
_started_at = time.monotonic()


@router.get("/healthcheck", response_model=HealthCheckResponse)
async def healthcheck(
    orch: Orchestrator = Depends(get_orchestrator),
) -> HealthCheckResponse:
    sections: dict[str, HealthSection] = {}

    # ---- system
    pi = await read_pi_status()
    if pi.cpu_temp_c is not None and pi.cpu_temp_c > 80.0:
        sys_status = "fail"
    elif pi.throttled_any:
        sys_status = "warn"
    else:
        sys_status = "ok"
    sections["system"] = HealthSection(
        status=sys_status,
        details={
            "uptime_s": time.monotonic() - _started_at,
            "pid": os.getpid(),
            "cpu_temp_c": pi.cpu_temp_c,
            "throttled": pi.throttled_hex,
            "throttled_any": pi.throttled_any,
            "ram_total_mb": pi.ram_total_mb,
            "ram_avail_mb": pi.ram_avail_mb,
            "disk_free_gb": pi.disk_free_gb,
        },
    )

    # ---- time
    chrony = await read_chrony_tracking()
    if chrony is None:
        sections["time"] = HealthSection(status="unknown", details={"note": "chrony not running"})
    else:
        st = "ok" if abs(chrony.offset_s) < 0.5 else "fail"
        sections["time"] = HealthSection(
            status=st,
            details={
                "offset_s": chrony.offset_s,
                "stratum": chrony.stratum,
                "rms_offset_s": chrony.rms_offset_s,
                "leap_status": chrony.leap_status,
            },
        )

    # ---- gps
    # gpsd fix modes: 0=unknown, 1=no fix, 2=2D fix, 3=3D fix.
    # The pill should reflect *fix quality*, not "is gpsd running" —
    # otherwise an indoor Pi with the daemon up but no satellites shows
    # green and misleads the operator. Mode 1 = no fix → fail.
    gps = orch.gps.snapshot
    if gps.mode <= 1:
        gps_status = "fail"
    elif gps.mode == 2:
        gps_status = "warn"
    else:
        gps_status = "ok"
    sections["gps"] = HealthSection(
        status=gps_status,
        details={
            "fix_mode": gps.mode,
            "lat": gps.lat,
            "lon": gps.lon,
            "alt": gps.alt,
            "sats_seen": gps.sats_seen,
            "sats_used": gps.sats_used,
            "time_iso": gps.time_iso,
        },
    )

    # ---- rig
    rig = orch._last_rig
    if rig.freq_hz is None:
        rig_status = "fail"
    else:
        rig_status = "ok"
    sections["rig"] = HealthSection(
        status=rig_status,
        details={
            "freq_hz": rig.freq_hz,
            "mode": rig.mode,
            "ptt": rig.ptt,
            "swr": rig.swr,
            "rfpower_norm": rig.rfpower_norm,
        },
    )

    # ---- audio: derive from the decode source. If it's the wired
    # production DecodePipeline its metrics tell us slot-by-slot drift
    # and how many decodes the last pass yielded. The noop source has
    # neither, so it shows up as "fail" — that's correct: no capture
    # means we'll never decode anything.
    # getattr-Guard: FakeOrchestrator in den Tests hat kein decode_source.
    # None → audio_status bleibt "fail" (kein metrics-Objekt), korrekt.
    decode_source = getattr(orch, "decode_source", None)
    audio_status = "fail"
    audio_details: dict[str, object] = {"note": "no capture wired"}
    metrics = getattr(decode_source, "metrics", None)
    if metrics is not None:
        # |drift| > 50 samples per slot is the FT8-lib soft limit. We use
        # 200 here so brief USB hiccups (zero-padding) don't fire warn.
        drift = getattr(metrics, "last_drift_samples", 0)
        if abs(drift) > 200:
            audio_status = "warn"
        else:
            audio_status = "ok"
        audio_details = {
            "slots_decoded": metrics.slots_decoded,
            "decodes_total": metrics.decodes_total,
            "last_decode_count": metrics.last_decode_count,
            "decodes_per_min": metrics.decodes_per_min,
            "last_drift_samples": drift,
        }
    sections["audio"] = HealthSection(
        status=audio_status,
        details=audio_details,
    )

    # ---- statemachine
    sm_status = "ok"
    if orch.state_machine.state.name == "TX_LOCKED":
        sm_status = "warn"
    sections["statemachine"] = HealthSection(
        status=sm_status,
        details={
            "state": orch.state_machine.state.name,
            "lock_reason": orch.state_machine.ctx.last_lock_reason,
            "cq_count": orch.state_machine.ctx.cq_count,
        },
    )

    return HealthCheckResponse(
        overall=_aggregate(sections),
        generated_at=time.time(),
        uptime_s=time.monotonic() - _started_at,
        sections=sections,
    )


def _aggregate(sections: dict[str, HealthSection]) -> str:
    """Aggregate single-section statuses to an overall traffic-light.

    GPS-fail wird besonders behandelt: solange Zeit-Sync über chrony
    läuft, ist der Pi voll funktionsfähig (decoder + TX-Pipeline brauchen
    nur die ~100ms-Slot-Genauigkeit die chrony locker liefert). Wir
    werten den Pi NUR DANN als "red" wenn entweder ein anderer
    kritischer Section-Fail vorliegt, oder BEIDE Zeitquellen (gps +
    chrony) tot sind.
    """
    fails = {k for k, s in sections.items() if s.status == "fail"}
    if fails - {"gps"}:
        # Mindestens ein nicht-GPS-Fail → harter Rot-Alarm
        return "red"
    if "gps" in fails:
        # Nur GPS down — als Warning, nicht als kritisch
        return "yellow"
    if any(s.status == "warn" for s in sections.values()):
        return "yellow"
    if all(s.status == "ok" for s in sections.values()):
        return "green"
    return "yellow"
