"""Atomic file write with single-slot backup.

Used for every persisted-config write path so a crash / full disk
mid-write can never leave a truncated config.yaml (Incident 2026-05-30:
a non-atomic write was the latent corruption risk behind the credential
loss). Always: snapshot the current file to ``<name>.bak``, write a
``<name>.tmp`` in the same directory, then ``os.replace`` it over the
target (atomic rename on POSIX).
"""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)


def atomic_write_with_backup(path: Path, text: str) -> None:
    """Write *text* to *path* atomically, keeping a ``.bak`` of the prior
    content.

    - Backup is best-effort (a failure to copy the old file is logged but
      does not abort the write — we still want the new content on disk).
    - The actual write is tempfile + atomic rename, so readers never see a
      half-written file and a crash leaves either the old or the new file
      intact, never a truncated one.
    """
    path = Path(path)
    if path.exists():
        bak = path.with_suffix(path.suffix + ".bak")
        try:
            bak.write_bytes(path.read_bytes())
        except OSError as exc:
            log.warning("atomic_write: backup to %s failed: %s (continuing)",
                        bak, exc)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)  # atomic on POSIX
