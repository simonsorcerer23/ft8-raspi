"""Atomic file write with single-slot backup.

Used for every persisted-config write path so a crash / full disk
mid-write can never leave a truncated config.yaml (Incident 2026-05-30:
a non-atomic write was the latent corruption risk behind the credential
loss). Always: snapshot the current file to ``<name>.bak``, write a
``<name>.tmp`` in the same directory, then ``os.replace`` it over the
target (atomic rename on POSIX).
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

# DATA-M2 (Audit 2026-05-30): serialisiert alle Config-Schreiber im Prozess
# (persist_config, PUT /api/config, ap-fallback), damit der .bak-Snapshot
# zweier interleavter Writer nicht eine Zwischenversion sichert.
_write_lock = asyncio.Lock()


async def async_atomic_write_with_backup(path: Path, text: str, *, mode: int = 0o600) -> None:
    """Wie :func:`atomic_write_with_backup`, aber unter einem prozessweiten
    Lock — fuer die konkurrierenden Config-Schreibpfade."""
    async with _write_lock:
        atomic_write_with_backup(path, text, mode=mode)


def atomic_write_with_backup(path: Path, text: str, *, mode: int = 0o600) -> None:
    """Write *text* to *path* atomically, keeping a ``.bak`` of the prior
    content, with fsync durability and restrictive permissions.

    - Backup is best-effort (a failure to copy the old file is logged but
      does not abort the write — we still want the new content on disk).
    - tempfile + ``fsync(file)`` + ``fsync(dir)`` + atomic rename: a crash
      / power loss leaves either the old or the *fully-written* new file,
      never a truncated/empty one (the dir-fsync makes the rename durable).
    - *mode* defaults to ``0o600`` so config files holding plaintext
      secrets (QRZ/ClubLog keys, WiFi PSKs) are not world-readable
      (SEC-H2, Audit 2026-05-30).
    """
    path = Path(path)
    if path.exists():
        bak = path.with_suffix(path.suffix + ".bak")
        try:
            bak.write_bytes(path.read_bytes())
            os.chmod(bak, mode)
        except OSError as exc:
            log.warning("atomic_write: backup to %s failed: %s (continuing)",
                        bak, exc)
    tmp = path.with_suffix(path.suffix + ".tmp")
    # write + fsync the tmp file so its contents are durable before rename
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        os.write(fd, text.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)  # atomic on POSIX
    # fsync the directory so the rename itself survives a power loss
    try:
        dir_fd = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError as exc:
        log.debug("atomic_write: dir-fsync of %s skipped: %s", path.parent, exc)
    # ensure final mode (O_CREAT respects umask; chmod makes it explicit)
    try:
        os.chmod(path, mode)
    except OSError as exc:
        log.warning("atomic_write: chmod %o on %s failed: %s", mode, path, exc)
