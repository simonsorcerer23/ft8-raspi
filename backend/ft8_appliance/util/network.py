"""Thin wrappers around ``nmcli`` for the WLAN management UI.

NetworkManager is the OS-level source of truth for WiFi profiles on Pi OS
Trixie (and most Debian flavors). Rather than poke at config files, we
shell out to ``nmcli`` and parse its terse output. ``sudo nmcli ...`` is
used for modifying commands — the systemd unit drops ``NoNewPrivileges``
so the service user (``sebastian``) can escalate via the NOPASSWD sudoers
rule set up at first boot.

Keep this layer dumb: each function is one shell call, no caching, no
state. The route layer composes them into the API surface.
"""

from __future__ import annotations

import asyncio
import logging
import shlex
from dataclasses import asdict, dataclass

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
@dataclass(slots=True)
class WifiConnection:
    """A saved WiFi profile in NetworkManager."""
    name: str               # profile name (often == SSID)
    ssid: str
    autoconnect: bool
    priority: int
    active: bool            # currently the one wlan0 is connected to


@dataclass(slots=True)
class WifiScanResult:
    """One AP seen by the last scan."""
    ssid: str
    bssid: str
    signal: int             # 0..100
    security: str           # e.g. "WPA2 WPA3", "--" for open
    in_use: bool


# ---------------------------------------------------------------------------
async def _run(cmd: list[str], *, sudo: bool = False, timeout: float = 10.0) -> tuple[int, str, str]:
    """Run *cmd*, return (rc, stdout, stderr)."""
    if sudo:
        cmd = ["sudo", "-n", *cmd]
    log.debug("nmcli: %s", " ".join(shlex.quote(c) for c in cmd))
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        raise
    return proc.returncode or 0, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")


# ---------------------------------------------------------------------------
async def list_connections() -> list[WifiConnection]:
    """Return all saved WiFi profiles, including non-active ones."""
    # -t = terse, -f = field selection. Active flag comes from a second call
    # since it lives in a different column set.
    rc, out, _ = await _run(
        ["nmcli", "-t", "-f", "NAME,TYPE,AUTOCONNECT,AUTOCONNECT-PRIORITY",
         "connection", "show"]
    )
    if rc != 0:
        return []

    profiles: dict[str, dict] = {}
    for line in out.strip().splitlines():
        # Format: name:type:autoconnect:priority
        # Names can contain colons in theory; nmcli escapes them as "\:".
        parts = _split_nmcli_terse(line, expected=4)
        if len(parts) != 4 or parts[1] != "802-11-wireless":
            continue
        name, _type, autoc, prio = parts
        profiles[name] = {
            "name": name,
            "autoconnect": autoc.lower() in ("yes", "true"),
            "priority": int(prio) if prio.lstrip("-").isdigit() else 0,
        }

    if not profiles:
        return []

    # Pull SSID + active flag per profile (one extra call each — small set)
    out_list: list[WifiConnection] = []
    for name, p in profiles.items():
        rc, info, _ = await _run(
            ["nmcli", "-t", "-f", "802-11-wireless.ssid,GENERAL.STATE",
             "connection", "show", name]
        )
        ssid = name
        active = False
        if rc == 0:
            for raw in info.splitlines():
                if raw.startswith("802-11-wireless.ssid:"):
                    ssid = raw.split(":", 1)[1].strip() or name
                elif raw.startswith("GENERAL.STATE:"):
                    active = raw.split(":", 1)[1].strip() == "activated"
        out_list.append(WifiConnection(
            name=name, ssid=ssid,
            autoconnect=p["autoconnect"], priority=p["priority"], active=active,
        ))

    out_list.sort(key=lambda c: (-c.priority, c.name.lower()))
    return out_list


# ---------------------------------------------------------------------------
async def scan_networks(*, rescan: bool = True) -> list[WifiScanResult]:
    """Trigger a fresh WiFi scan and return what we saw."""
    if rescan:
        # Best-effort rescan; ignore errors (radio off, rate-limit, …)
        await _run(["nmcli", "device", "wifi", "rescan"], sudo=True)
        await asyncio.sleep(2.0)  # let scan complete before listing
    rc, out, _ = await _run(
        ["nmcli", "-t", "-f", "IN-USE,SSID,BSSID,SIGNAL,SECURITY",
         "device", "wifi", "list"]
    )
    if rc != 0:
        return []
    seen: dict[str, WifiScanResult] = {}  # dedup by ssid, keep strongest
    for line in out.strip().splitlines():
        parts = _split_nmcli_terse(line, expected=5)
        if len(parts) != 5:
            continue
        in_use_flag, ssid, bssid, signal, security = parts
        if not ssid:  # hidden SSIDs come back blank
            continue
        try:
            sig = int(signal)
        except ValueError:
            sig = 0
        existing = seen.get(ssid)
        if existing is None or sig > existing.signal:
            seen[ssid] = WifiScanResult(
                ssid=ssid, bssid=bssid, signal=sig,
                security=security or "--",
                in_use=in_use_flag.strip() == "*",
            )
    return sorted(seen.values(), key=lambda r: -r.signal)


# ---------------------------------------------------------------------------
async def add_connection(ssid: str, psk: str | None, *, priority: int = 50,
                          autoconnect: bool = True) -> tuple[bool, str]:
    """Add a new WiFi profile to NetworkManager and bring it up.

    Returns (ok, message). On success message is the profile name.
    """
    if not ssid:
        return False, "empty SSID"
    # nmcli "add" doesn't let us set priority+autoconnect in one go; do it
    # in two steps (idempotent enough for this use case).
    cmd_add = [
        "nmcli", "connection", "add", "type", "wifi",
        "con-name", ssid, "ifname", "wlan0", "ssid", ssid,
    ]
    rc, _, err = await _run(cmd_add, sudo=True)
    if rc != 0:
        return False, err.strip() or f"nmcli add failed ({rc})"

    mods = [
        "connection.autoconnect", "yes" if autoconnect else "no",
        "connection.autoconnect-priority", str(priority),
    ]
    if psk:
        mods.extend(["wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", psk])

    rc, _, err = await _run(
        ["nmcli", "connection", "modify", ssid, *mods], sudo=True
    )
    if rc != 0:
        # Clean up the half-added profile so the next attempt is fresh.
        await _run(["nmcli", "connection", "delete", ssid], sudo=True)
        return False, err.strip() or f"nmcli modify failed ({rc})"
    return True, ssid


# ---------------------------------------------------------------------------
async def delete_connection(name: str) -> tuple[bool, str]:
    rc, _, err = await _run(
        ["nmcli", "connection", "delete", name], sudo=True
    )
    if rc != 0:
        return False, err.strip() or f"nmcli delete failed ({rc})"
    return True, name


async def set_priority(name: str, priority: int) -> tuple[bool, str]:
    rc, _, err = await _run(
        ["nmcli", "connection", "modify", name,
         "connection.autoconnect-priority", str(priority)],
        sudo=True,
    )
    if rc != 0:
        return False, err.strip() or f"nmcli modify failed ({rc})"
    return True, name


async def activate(name: str) -> tuple[bool, str]:
    """Force-activate a saved profile (use after editing or scanning)."""
    rc, _, err = await _run(
        ["nmcli", "connection", "up", name], sudo=True, timeout=30.0,
    )
    if rc != 0:
        return False, err.strip() or f"nmcli up failed ({rc})"
    return True, name


# ---------------------------------------------------------------------------
def _split_nmcli_terse(line: str, *, expected: int) -> list[str]:
    """Split nmcli's -t output handling escaped colons.

    nmcli emits ``foo\\:bar:rest`` where ``\\:`` is a literal colon inside
    a field. We split on unescaped ``:`` only, then unescape.
    """
    parts: list[str] = []
    buf: list[str] = []
    i = 0
    while i < len(line):
        c = line[i]
        if c == "\\" and i + 1 < len(line) and line[i + 1] == ":":
            buf.append(":")
            i += 2
        elif c == ":":
            parts.append("".join(buf))
            buf = []
            i += 1
        else:
            buf.append(c)
            i += 1
    parts.append("".join(buf))
    if len(parts) < expected:
        # pad so callers can unpack confidently
        parts += [""] * (expected - len(parts))
    return parts


def _as_dict(obj) -> dict:
    return asdict(obj)
