"""System-info Endpoint — CPU temp, load, RAM, disk, Pi throttling.

Lightweight, alle Reads aus /proc + /sys + ein subprocess für vcgencmd
(Pi-spezifisch, sauber gekapselt sodass es auf nicht-Pi-Hosts kein
Drama gibt — Werte sind dann einfach None).

Frontend pollt diesen Endpoint alle 30s für das System-Panel.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class SystemInfo(BaseModel):
    pi_model: str | None
    uptime_s: int
    cpu_temp_c: float | None
    cpu_load_1m: float
    cpu_load_5m: float
    cpu_load_15m: float
    mem_used_mb: int
    mem_total_mb: int
    disk_used_gb: float
    disk_total_gb: float
    # Pi-specific throttling-state. vcgencmd get_throttled returns a hex bitfield;
    # 0x0 = healthy. Non-zero = under-voltage, freq-capped, or thermally throttled.
    # Field is None on non-Pi hosts where vcgencmd isn't available.
    throttled_hex: str | None
    throttled_healthy: bool | None


def _read_first(path: str) -> str | None:
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None


def _cpu_temp() -> float | None:
    raw = _read_first("/sys/class/thermal/thermal_zone0/temp")
    if raw is None:
        return None
    try:
        return int(raw) / 1000.0
    except ValueError:
        return None


def _pi_model() -> str | None:
    raw = _read_first("/proc/device-tree/model")
    if raw is None:
        return None
    # device-tree files are NUL-terminated, strip it
    return raw.replace("\x00", "").strip()


def _uptime_s() -> int:
    raw = _read_first("/proc/uptime")
    if raw is None:
        return 0
    try:
        return int(float(raw.split()[0]))
    except (ValueError, IndexError):
        return 0


def _loadavg() -> tuple[float, float, float]:
    raw = _read_first("/proc/loadavg")
    if raw is None:
        return (0.0, 0.0, 0.0)
    parts = raw.split()
    try:
        return (float(parts[0]), float(parts[1]), float(parts[2]))
    except (ValueError, IndexError):
        return (0.0, 0.0, 0.0)


def _meminfo() -> tuple[int, int]:
    """Returns (used_mb, total_mb) using MemAvailable for the "free" sense."""
    raw = _read_first("/proc/meminfo")
    if raw is None:
        return (0, 0)
    fields: dict[str, int] = {}
    for line in raw.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].endswith(":"):
            key = parts[0][:-1]
            try:
                fields[key] = int(parts[1])  # value in kB
            except ValueError:
                continue
    total_kb = fields.get("MemTotal", 0)
    avail_kb = fields.get("MemAvailable", fields.get("MemFree", 0))
    used_kb = max(0, total_kb - avail_kb)
    return (used_kb // 1024, total_kb // 1024)


def _diskinfo() -> tuple[float, float]:
    """Returns (used_gb, total_gb) for rootfs."""
    try:
        st = os.statvfs("/")
    except OSError:
        return (0.0, 0.0)
    total = st.f_blocks * st.f_frsize
    free = st.f_bavail * st.f_frsize
    used = total - free
    gb = 1024**3
    return (round(used / gb, 1), round(total / gb, 1))


def _throttled() -> tuple[str | None, bool | None]:
    """Pi-only: vcgencmd get_throttled.

    Bit 0  = under-voltage now
    Bit 1  = ARM freq capped now
    Bit 2  = currently throttled
    Bit 3  = soft temp limit active
    Bit 16 = under-voltage occurred
    Bit 17 = ARM freq cap occurred
    Bit 18 = throttling occurred
    Bit 19 = soft temp limit occurred

    Currently-active bits (0-3) are the "live" health. Historical bits
    (16-19) are nice-to-know but don't immediately worry us.
    """
    try:
        r = subprocess.run(
            ["vcgencmd", "get_throttled"],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return (None, None)
    if r.returncode != 0:
        return (None, None)
    # output: "throttled=0x0" or "throttled=0x50005"
    out = r.stdout.strip()
    if "=" not in out:
        return (None, None)
    hex_str = out.split("=", 1)[1].strip()
    try:
        val = int(hex_str, 16)
    except ValueError:
        return (None, None)
    # Live-Bits 0-3 only: bits 16+ sind nur Historie und nicht alarming.
    live_mask = 0xF
    healthy = (val & live_mask) == 0
    return (hex_str, healthy)


@router.get("/system/info", response_model=SystemInfo)
async def get_system_info() -> SystemInfo:
    used_mb, total_mb = _meminfo()
    used_gb, total_gb = _diskinfo()
    load = _loadavg()
    throttled_hex, throttled_healthy = _throttled()
    return SystemInfo(
        pi_model=_pi_model(),
        uptime_s=_uptime_s(),
        cpu_temp_c=_cpu_temp(),
        cpu_load_1m=load[0],
        cpu_load_5m=load[1],
        cpu_load_15m=load[2],
        mem_used_mb=used_mb,
        mem_total_mb=total_mb,
        disk_used_gb=used_gb,
        disk_total_gb=total_gb,
        throttled_hex=throttled_hex,
        throttled_healthy=throttled_healthy,
    )
