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
    # PTT ausserhalb von CAT: der Digirig schaltet PTT ueber RTS desselben
    # seriellen Ports. rigctld bekommt das als --ptt-type/--ptt-file; die
    # Unit setzt $RIG_PTT_ARGS ungequotet ein, damit es zwei Argumente werden.
    ptt = rig.effective_ptt_type
    ptt_args = "" if ptt == "cat" else f"--ptt-type={ptt.upper()} --ptt-file={rig.serial_device}"
    return (
        f"RIG_MODEL={rig.hamlib_id}\n"
        f"RIG_DEVICE={rig.serial_device}\n"
        f"RIG_BAUD={rig.effective_cat_baud}\n"
        f"RIG_PTT_ARGS={ptt_args}\n"
    )


def write_rigctld_envfile(rig: RigConfig, path: Path | str) -> None:
    """Write the env file to *path*, creating parent dirs as needed."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(render_rigctld_envfile(rig))
