"""System-level health probes for the appliance.

Wraps the small set of shell commands the orchestrator needs in order
to populate :class:`HardwareState` and ``/api/healthcheck`` with real
data on the Pi:

* chrony tracking offset (seconds, signed)
* CPU temperature (°C)
* Pi throttling flags (vcgencmd)
* /proc/meminfo (RAM)
* disk free for the appliance data path
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

# Match the "System time : 0.000001234 seconds slow/fast of NTP time" line
_CHRONY_OFFSET_RE = re.compile(
    r"System time\s*:\s*([0-9eE+\-.]+)\s+seconds\s+(slow|fast)\s+of"
)


# ---------------------------------------------------------------------------
@dataclass(slots=True)
class ChronyStatus:
    offset_s: float            # signed; positive = system clock is ahead
    stratum: int | None = None
    rms_offset_s: float | None = None
    leap_status: str | None = None


@dataclass(slots=True)
class PiSystemStatus:
    cpu_temp_c: float | None
    throttled_hex: str | None
    throttled_any: bool
    ram_total_mb: int | None
    ram_avail_mb: int | None
    disk_free_gb: float | None


# ---------------------------------------------------------------------------
async def read_chrony_tracking() -> ChronyStatus | None:
    """Run ``chronyc -n tracking`` and parse the offset.

    Returns ``None`` if chronyc isn't installed (dev machine without
    chrony) or the command fails.
    """
    if shutil.which("chronyc") is None:
        return None
    proc = await asyncio.create_subprocess_exec(
        "chronyc", "-n", "tracking",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=2.0)
    except TimeoutError:
        proc.kill()
        return None
    if proc.returncode != 0:
        return None
    return parse_chrony_tracking(stdout.decode("ascii", errors="replace"))


def parse_chrony_tracking(text: str) -> ChronyStatus | None:
    """Pure-Python parser — testable without invoking chrony."""
    m = _CHRONY_OFFSET_RE.search(text)
    if not m:
        return None
    raw, direction = m.group(1), m.group(2)
    try:
        offset = float(raw)
    except ValueError:
        return None
    # "slow of NTP" means system clock < NTP, i.e. negative offset.
    # "fast of NTP" means system clock > NTP, positive.
    if direction == "slow":
        offset = -offset

    stratum: int | None = None
    sm = re.search(r"Stratum\s*:\s*(\d+)", text)
    if sm:
        stratum = int(sm.group(1))

    rms: float | None = None
    rm = re.search(r"RMS offset\s*:\s*([0-9eE+\-.]+)\s+seconds", text)
    if rm:
        try:
            rms = float(rm.group(1))
        except ValueError:
            pass

    leap: str | None = None
    lm = re.search(r"Leap status\s*:\s*(\S+)", text)
    if lm:
        leap = lm.group(1)

    return ChronyStatus(offset_s=offset, stratum=stratum, rms_offset_s=rms, leap_status=leap)


# ---------------------------------------------------------------------------
def _read_cpu_temp() -> float | None:
    """Read the Pi's CPU temperature in °C."""
    p = Path("/sys/class/thermal/thermal_zone0/temp")
    if not p.exists():
        return None
    try:
        millideg = int(p.read_text().strip())
        return millideg / 1000.0
    except (OSError, ValueError):
        return None


async def _read_throttled() -> tuple[str | None, bool]:
    """Run ``vcgencmd get_throttled`` if available."""
    if shutil.which("vcgencmd") is None:
        return None, False
    proc = await asyncio.create_subprocess_exec(
        "vcgencmd", "get_throttled",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=1.0)
    except TimeoutError:
        proc.kill()
        return None, False
    text = out.decode("ascii", errors="replace").strip()
    # Format: "throttled=0x50000"
    m = re.search(r"0x[0-9a-fA-F]+", text)
    if not m:
        return None, False
    hex_str = m.group(0)
    return hex_str, int(hex_str, 16) != 0


def _read_meminfo() -> tuple[int | None, int | None]:
    p = Path("/proc/meminfo")
    if not p.exists():
        return None, None
    total_kb: int | None = None
    avail_kb: int | None = None
    for line in p.read_text().splitlines():
        if line.startswith("MemTotal:"):
            total_kb = int(line.split()[1])
        elif line.startswith("MemAvailable:"):
            avail_kb = int(line.split()[1])
        if total_kb is not None and avail_kb is not None:
            break
    return (
        total_kb // 1024 if total_kb is not None else None,
        avail_kb // 1024 if avail_kb is not None else None,
    )


def _disk_free_gb(path: Path) -> float | None:
    try:
        st = shutil.disk_usage(str(path))
        return st.free / (1024**3)
    except OSError:
        return None


async def read_pi_status(data_dir: Path = Path("/var/lib/ft8-appliance")) -> PiSystemStatus:
    """Aggregate the Pi-specific health into one object."""
    throttled_hex, throttled_any = await _read_throttled()
    total_mb, avail_mb = _read_meminfo()
    return PiSystemStatus(
        cpu_temp_c=_read_cpu_temp(),
        throttled_hex=throttled_hex,
        throttled_any=throttled_any,
        ram_total_mb=total_mb,
        ram_avail_mb=avail_mb,
        disk_free_gb=_disk_free_gb(data_dir if data_dir.exists() else Path("/")),
    )
