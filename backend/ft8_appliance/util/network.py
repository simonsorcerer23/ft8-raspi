"""Thin wrappers around ``nmcli`` for the WLAN management UI.

NetworkManager is the OS-level source of truth for WiFi profiles on Pi OS
Trixie (and most Debian flavors). Rather than poke at config files, we
shell out to ``nmcli`` and parse its terse output. Reading needs no root.
Everything that changes something goes through ``sudo -n
/usr/local/sbin/ft8-wlan`` (deploy/scripts/ft8-wlan): the service user may
run only that helper, which knows five fixed actions and touches only WiFi
profiles. Until 2026-11-28 the sudoers rules allowed ``nmcli connection
add|modify|delete|up *``, i.e. any connection with any property.

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
WLAN_HELFER = "/usr/local/sbin/ft8-wlan"


async def _run(cmd: list[str], *, sudo: bool = False, timeout: float = 10.0,
               eingabe: str | None = None) -> tuple[int, str, str]:
    """Run *cmd*, return (rc, stdout, stderr). *eingabe* goes to stdin."""
    if sudo:
        cmd = ["sudo", "-n", *cmd]
    log.debug("nmcli: %s", " ".join(shlex.quote(c) for c in cmd))
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE if eingabe is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(
            proc.communicate(eingabe.encode() if eingabe is not None else None),
            timeout=timeout)
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
        await _run([WLAN_HELFER, "rescan"], sudo=True)
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
    # Das Passwort geht ueber stdin, nicht in die Prozessliste.
    rc, _, err = await _run(
        [WLAN_HELFER, "add", ssid, str(priority), "yes" if autoconnect else "no"],
        sudo=True, eingabe=(psk or "") + "\n",
    )
    if rc != 0:
        return False, err.strip() or f"ft8-wlan add failed ({rc})"
    return True, ssid


# ---------------------------------------------------------------------------
async def delete_connection(name: str) -> tuple[bool, str]:
    rc, _, err = await _run(
        [WLAN_HELFER, "delete", name], sudo=True
    )
    if rc != 0:
        return False, err.strip() or f"ft8-wlan delete failed ({rc})"
    return True, name


async def set_priority(name: str, priority: int) -> tuple[bool, str]:
    rc, _, err = await _run(
        [WLAN_HELFER, "priority", name, str(priority)],
        sudo=True,
    )
    if rc != 0:
        return False, err.strip() or f"ft8-wlan priority failed ({rc})"
    return True, name


async def activate(name: str) -> tuple[bool, str]:
    """Force-activate a saved profile (use after editing or scanning)."""
    rc, _, err = await _run(
        [WLAN_HELFER, "up", name], sudo=True, timeout=30.0,
    )
    if rc != 0:
        return False, err.strip() or f"ft8-wlan up failed ({rc})"
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
