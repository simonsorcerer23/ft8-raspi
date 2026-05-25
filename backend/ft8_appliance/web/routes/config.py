"""Read + write the YAML configuration via the web UI."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ...config import AppConfig, get_config
from ...rig import detect_rigs
from ...runtime import Orchestrator
from ..deps import get_orchestrator

router = APIRouter()


@router.get("/config", response_model=AppConfig)
async def read_config() -> AppConfig:
    try:
        return get_config()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.get("/rig/detect")
async def detect_rig() -> dict:
    """Scan /dev/serial/by-id for USB rigs we know how to talk to.

    Returns a list of candidates, high-confidence Icom natives first.
    Empty list = no supported rig is plugged in right now.
    """
    return {"candidates": [d.as_dict() for d in detect_rigs()]}


class SaveConfigRequest(BaseModel):
    yaml_text: str


@router.put("/config", response_model=AppConfig)
async def save_config(
    req: SaveConfigRequest,
    orch: Orchestrator = Depends(get_orchestrator),
) -> AppConfig:
    """Validate + apply a new YAML config + persist to disk.

    Three steps because skipping any one breaks the UX:
      1. parse + Pydantic-validate so a bad YAML never reaches the
         in-memory singleton (it would crash later consumers)
      2. hot-swap the in-memory ``_current`` so /api/config reads the
         new values immediately AND notify the orchestrator
         (active antenna, integration clients, state-machine callsign
         / limits all need a refresh)
      3. write the YAML back to the file the loader read from so the
         settings survive a reboot — without this the user saw their
         changes "disappear" after every restart
    """
    import yaml

    from ...config import set_config_for_tests
    from ...config.loader import get_current_path

    try:
        raw = yaml.safe_load(req.yaml_text) or {}
        cfg = AppConfig.model_validate(raw)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"invalid config: {exc}")

    # Persist before we swap the in-memory cache: if the disk write
    # fails we want to abort cleanly rather than leave the running
    # process disagreeing with the file on disk.
    # v0.6.2 Anti-Corruption: atomic write via tempfile + rename,
    # plus auto-backup der aktuellen Config nach ".bak" (single-slot,
    # ueberschreibt vorigen). Verhindert korrupte Config bei Crash
    # mid-write und gibt User immer einen Rollback-Punkt.
    path = get_current_path()
    if path is not None:
        try:
            # 1. Backup (best-effort — kein hard-fail wenn Source fehlt)
            if path.exists():
                bak_path = path.with_suffix(path.suffix + ".bak")
                try:
                    bak_path.write_bytes(path.read_bytes())
                except OSError as bak_exc:
                    # Backup failure ist not fatal — log und weiter
                    import logging
                    logging.getLogger("ft8_appliance.config").warning(
                        "auto-backup to %s failed: %s (continuing)",
                        bak_path, bak_exc,
                    )
            # 2. Atomic write: tempfile in same dir, dann rename
            #    (rename ist atomic auf POSIX-Filesystems)
            tmp_path = path.with_suffix(path.suffix + ".tmp")
            tmp_path.write_text(req.yaml_text, encoding="utf-8")
            tmp_path.replace(path)  # atomic rename
        except OSError as exc:
            raise HTTPException(
                status_code=500,
                detail=f"config validated but writing {path} failed: {exc}",
            )

    set_config_for_tests(cfg)
    await orch.on_config_changed(cfg)
    return cfg
