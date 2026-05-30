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


def preserve_operators(raw: dict, current: AppConfig) -> dict:
    """DATENSCHUTZ-GUARD (Incident 2026-05-30).

    Der Frontend-Config-Save serialisiert nur den Legacy-``operator:``-Block
    ohne ``operators:``-Liste + ohne Credentials. Wuerde der direkt
    uebernommen, plaettet JEDES "Speichern" auf der Konfig-Seite die
    komplette Operator-Liste + alle QRZ/ClubLog-Keys + Sende-Call-Logbuecher.

    Daher: Operatoren + active_callsign kommen AUSSCHLIESSLICH aus der
    laufenden Config (autoritativ). Aus dem geposteten ``operator:``-Block
    uebernehmen wir NUR die editierbaren Basisfelder (Locator/Power/Lizenz/
    Callsign) des AKTIVEN Operators. Operator-/Credential-Verwaltung laeuft
    sonst ausschliesslich ueber /api/operators.

    Mutiert ``raw`` in-place und gibt es zurueck.
    """
    posted_op = raw.get("operator") or {}
    raw.pop("operator", None)
    raw.pop("operators", None)
    raw.pop("active_callsign", None)

    ops = [op.model_copy(deep=True) for op in current.operators]
    active = current.active_callsign or (ops[0].callsign if ops else None)
    for op in ops:
        if op.callsign == active:
            if posted_op.get("default_locator") is not None:
                op.default_locator = posted_op["default_locator"]
            if posted_op.get("default_power_w") is not None:
                op.default_power_w = posted_op["default_power_w"]
            if posted_op.get("license_class"):
                op.license_class = posted_op["license_class"]
            new_cs = (posted_op.get("callsign") or "").strip().upper()
            if new_cs and new_cs != op.callsign:
                op.callsign = new_cs
                active = new_cs
            break
    if ops:
        raw["operators"] = [op.model_dump() for op in ops]
        if active:
            raw["active_callsign"] = active
    return raw


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
        # DATENSCHUTZ-GUARD: Operatoren + Credentials NIE aus dem geposteten
        # YAML uebernehmen — sie kommen autoritativ aus der laufenden Config.
        # (Incident 2026-05-30: Config-Save plaettete sonst die Operatoren.)
        raw = preserve_operators(raw, orch.config)
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
            #    (rename ist atomic auf POSIX-Filesystems).
            #    v0.33.0: NICHT mehr den rohen req.yaml_text schreiben —
            #    der enthaelt den Stub-`operator:`-Block ohne Operatoren/
            #    Creds und wuerde die Datei plaetten. Stattdessen die
            #    kanonische, merge-korrigierte cfg serialisieren (analog
            #    persist_config: computed `operator` + rig-Computed raus).
            d = cfg.model_dump(
                exclude_none=True,
                exclude={
                    "rig": {"hamlib_id", "effective_max_power_w"},
                    "operator": True,
                },
            )
            serialized = yaml.safe_dump(d, default_flow_style=False, sort_keys=False)
            tmp_path = path.with_suffix(path.suffix + ".tmp")
            tmp_path.write_text(serialized, encoding="utf-8")
            tmp_path.replace(path)  # atomic rename
        except OSError as exc:
            raise HTTPException(
                status_code=500,
                detail=f"config validated but writing {path} failed: {exc}",
            )

    set_config_for_tests(cfg)
    await orch.on_config_changed(cfg)
    return cfg
