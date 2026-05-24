"""YAML loader for :class:`AppConfig`.

Supports hot-reload by re-reading the file on demand (see :func:`load_config`).
File watching / pushing to the running app belongs in the web layer.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

import yaml

from .models import AppConfig

log = logging.getLogger(__name__)

_lock = threading.Lock()
_current: AppConfig | None = None
_current_path: Path | None = None


def load_config(path: Path | str) -> AppConfig:
    """Read and validate the YAML config at *path*.

    Stores the loaded config (and its source path) as the module-level
    current config, accessible via :func:`get_config`. Backward-Compat
    fuer alte single-operator-YAMLs ist im AppConfig-Model selbst
    (model_validator mode="before") — der Loader bleibt schlank.
    """
    path = Path(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cfg = AppConfig.model_validate(raw)
    with _lock:
        global _current, _current_path
        _current = cfg
        _current_path = path
    log.info("loaded config from %s (active=%s, operators=%d)",
             path, cfg.active_callsign, len(cfg.operators))
    return cfg


def reload_config() -> AppConfig:
    """Re-read the previously loaded config file from disk."""
    if _current_path is None:
        raise RuntimeError("no config has been loaded yet; call load_config() first")
    return load_config(_current_path)


def get_config() -> AppConfig:
    """Return the currently active config. Raises if nothing was loaded yet."""
    if _current is None:
        raise RuntimeError("config not loaded; call load_config() first")
    return _current


def set_config_for_tests(cfg: AppConfig) -> None:
    """Test helper — inject a config object without touching disk."""
    with _lock:
        global _current
        _current = cfg


def get_current_path() -> Path | None:
    """Return the file path the running config was loaded from.

    Used by the /api/config PUT endpoint to write a saved config back
    to disk so changes survive a service restart.
    """
    return _current_path
