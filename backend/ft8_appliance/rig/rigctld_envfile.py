"""Render the ``/etc/default/ft8-rigctld`` env file from :class:`RigConfig`.

The systemd unit ``ft8-rigctld.service`` reads this file and substitutes the
values into the ``rigctld`` command line. Regenerating the file (and asking
systemd to restart the unit) is how we switch between IC-705 and IC-7300 at
runtime without editing service files by hand.
"""

from __future__ import annotations

from pathlib import Path

from ..config import RigConfig


def render_rigctld_envfile(rig: RigConfig) -> str:
    """Return the env-file content (one ``KEY=VALUE`` per line)."""
    return (
        f"RIG_MODEL={rig.hamlib_id}\n"
        f"RIG_DEVICE={rig.serial_device}\n"
        f"RIG_BAUD={rig.cat_baud}\n"
    )


def write_rigctld_envfile(rig: RigConfig, path: Path | str) -> None:
    """Write the env file to *path*, creating parent dirs as needed."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(render_rigctld_envfile(rig))
