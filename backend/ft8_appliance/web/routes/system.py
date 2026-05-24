"""System-info Endpoint — CPU temp, load, RAM, disk, Pi throttling.

Lightweight, alle Reads aus /proc + /sys + ein subprocess für vcgencmd
(Pi-spezifisch, sauber gekapselt sodass es auf nicht-Pi-Hosts kein
Drama gibt — Werte sind dann einfach None).

Frontend pollt diesen Endpoint alle 30s für das System-Panel.

Enthält zusätzlich Version/Self-Update-Endpoints (`/system/version`,
`/system/self-update`) für die Update-Card auf der Konfig-Seite.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import deps  # noqa: F401 — keeps import-shape consistent

log = logging.getLogger(__name__)

router = APIRouter()

# Repo-Wurzel = zwei Ebenen über backend/. WorkingDirectory der unit
# zeigt auf .../backend, also Parent davon ist die Repo-Wurzel mit .git.
_REPO_ROOT = Path(__file__).resolve().parents[4]


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


# ---------------------------------------------------------------------------
# Version + Self-Update — feeds the "System-Update"-Card auf der Konfig-Seite.
#
# Strategie:
#  * `current_version` kommt aus dem eingecheckten _version.py (Single source
#    of truth — release.sh schreibt es bei jedem Tag-Cut neu).
#  * `latest_version` ist der höchste lokal bekannte v*-Tag (set wird vom
#    Self-Update-Timer alle 10min via `git fetch --tags` aktualisiert).
#    Wir machen hier KEIN Live-Fetch — das wäre langsam und würde bei
#    Netzwerkausfall die UI blockieren.
#  * `update_in_progress` checkt ob ft8-self-update.service gerade aktiv ist.

_SEMVER_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")


def _parse_semver(tag: str) -> tuple[int, int, int] | None:
    m = _SEMVER_RE.match(tag.strip())
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


def _read_installed_version() -> tuple[str, str]:
    """Returns (version, tag). Beide leer-strings falls _version.py nicht da."""
    try:
        from ft8_appliance import _version as v
        return (getattr(v, "__version__", ""), getattr(v, "__tag__", ""))
    except Exception:  # pragma: no cover — defensive
        return ("", "")


def _read_latest_local_tag() -> str | None:
    """Höchster lokal bekannter v*-Tag (ohne network call)."""
    try:
        r = subprocess.run(
            ["git", "-C", str(_REPO_ROOT), "tag", "-l", "v*", "--sort=-v:refname"],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    for line in r.stdout.splitlines():
        line = line.strip()
        if _parse_semver(line):
            return line
    return None


def _last_fetch_at() -> float | None:
    """Mtime von .git/FETCH_HEAD — zeigt wann zuletzt git fetch lief."""
    fh = _REPO_ROOT / ".git" / "FETCH_HEAD"
    try:
        return fh.stat().st_mtime
    except OSError:
        return None


def _git_describe() -> str | None:
    try:
        r = subprocess.run(
            ["git", "-C", str(_REPO_ROOT), "describe", "--tags", "--always", "--dirty"],
            capture_output=True, text=True, timeout=3,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    return r.stdout.strip() or None


def _update_in_progress() -> bool:
    """systemctl is-active ft8-self-update.service → true wenn läuft."""
    try:
        r = subprocess.run(
            ["systemctl", "is-active", "--quiet", "ft8-self-update.service"],
            timeout=3,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0


class VersionInfo(BaseModel):
    current_version: str           # z.B. "0.1.0" oder "0.0.0-dev"
    current_tag: str               # z.B. "v0.1.0" oder "" wenn untagged
    git_describe: str | None       # z.B. "v0.1.0" oder "v0.1.0-3-gabc1234-dirty"
    latest_version: str | None     # höchster lokal bekannter v*-Tag
    update_available: bool
    update_in_progress: bool
    last_fetch_at: float | None    # unix-ts vom letzten git fetch
    repo_is_git: bool              # false wenn rsync-Installation (alte Pis)


@router.get("/system/version", response_model=VersionInfo)
async def get_version() -> VersionInfo:
    version, tag = _read_installed_version()
    latest = _read_latest_local_tag()
    is_git = (_REPO_ROOT / ".git").is_dir()
    desc = _git_describe()

    update_available = False
    if latest is not None:
        current_sem = _parse_semver(tag) if tag else None
        latest_sem = _parse_semver(latest)
        if latest_sem is not None and (current_sem is None or latest_sem > current_sem):
            update_available = True

    return VersionInfo(
        current_version=version,
        current_tag=tag,
        git_describe=desc,
        latest_version=latest,
        update_available=update_available,
        update_in_progress=_update_in_progress(),
        last_fetch_at=_last_fetch_at(),
        repo_is_git=is_git,
    )


class SelfUpdateResponse(BaseModel):
    triggered: bool
    detail: str


@router.post("/system/self-update", response_model=SelfUpdateResponse, status_code=202)
async def trigger_self_update() -> SelfUpdateResponse:
    """Triggert manuell den Self-Update-Service.

    Der Service prüft selbst ob ein neuer Tag verfügbar ist und ob der
    Pi gerade idle ist — wir starten ihn einfach. Reaktion via Polling
    von ``GET /system/version`` (Frontend erkennt update_in_progress
    → false + current_version geändert).
    """
    if not (_REPO_ROOT / ".git").is_dir():
        raise HTTPException(
            status_code=409,
            detail="Pi ist keine git-Installation — siehe docs/self_update.md für Migration",
        )

    if _update_in_progress():
        return SelfUpdateResponse(triggered=False, detail="already in progress")

    # sudoers.d/ft8-self-update erlaubt diesen Aufruf NOPASSWD.
    try:
        r = subprocess.run(
            ["sudo", "-n", "/bin/systemctl", "start", "ft8-self-update.service"],
            capture_output=True, text=True, timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        raise HTTPException(status_code=500, detail=f"systemctl call failed: {e}") from e

    if r.returncode != 0:
        # Häufigste Ursache: sudoers-Snippet nicht installiert oder
        # falsch (visudo-Validierung in install.sh sollte das fangen,
        # aber nicht bei rsync-Pis ohne re-install).
        msg = r.stderr.strip() or r.stdout.strip() or "unknown error"
        raise HTTPException(
            status_code=500,
            detail=f"systemctl start fehlgeschlagen (rc={r.returncode}): {msg}",
        )

    return SelfUpdateResponse(triggered=True, detail="ft8-self-update.service gestartet")
