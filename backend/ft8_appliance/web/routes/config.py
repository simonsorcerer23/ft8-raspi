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


def _fill_missing_secrets(posted: dict, current: dict, keys: list[str]) -> None:
    """Fuelle in ``posted`` fehlende/leere Secret-Felder aus ``current``.

    NUR auffuellen, nie ueberschreiben: ein Wert den das Frontend bewusst
    mitschickt (User hat ihn editiert) bleibt; nur was das Frontend
    weglaesst (weil es ihn gar nicht im Formular hat) wird aus der
    laufenden Config wiederhergestellt.
    """
    for k in keys:
        if not posted.get(k) and current.get(k):
            posted[k] = current[k]


def preserve_secrets(raw: dict, current: AppConfig) -> dict:
    """DATENSCHUTZ-GUARD (Incident 2026-05-30, erweitert v0.34.0).

    Der Frontend-Config-Save serialisiert nur einen Teil der Felder. Wuerde
    das geposteten YAML 1:1 uebernommen, plaettet JEDES "Speichern" auf der
    Konfig-Seite Daten, die das Frontend nicht (vollstaendig) mitschickt:
    die Operator-Liste + alle QRZ/ClubLog-Keys, die WLAN-Liste
    (``network.wifi_priority``) und Integrations-Secrets (HamQTH-Login etc.).

    Strategie:
      * operators + active_callsign: AUTORITATIV aus der laufenden Config;
        aus dem geposteten ``operator:``-Block nur die editierbaren
        Basisfelder des aktiven Operators (Locator/Power/Lizenz/Callsign).
      * Sonstige Secrets (wifi_priority, integrations.qrz/hamqth/psk):
        fehlende Felder aus der laufenden Config auffuellen (nie ueber-
        schreiben — editierbare Werte aus dem Formular bleiben).

    Mutiert ``raw`` in-place und gibt es zurueck.
    """
    cur = current.model_dump()

    # --- 1. Operatoren autoritativ aus der laufenden Config ---------------
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

    # --- 2. WLAN-Liste bewahren (Frontend emittiert nur ap_fallback) ------
    cur_net = cur.get("network") or {}
    if cur_net.get("wifi_priority"):
        net = raw.setdefault("network", {})
        if not net.get("wifi_priority"):
            net["wifi_priority"] = cur_net["wifi_priority"]

    # --- 3. Integrations-Secrets auffuellen (nie ueberschreiben) ----------
    cur_int = cur.get("integrations") or {}
    secret_keys = {
        "qrz": ["user", "password", "logbook_api_key"],
        "hamqth": ["user", "password"],
        "psk_reporter": ["contact_email"],
        "ntfy": ["topic"],
    }
    for sub, keys in secret_keys.items():
        cur_sub = cur_int.get(sub) or {}
        if any(cur_sub.get(k) for k in keys):
            posted_sub = raw.setdefault("integrations", {}).setdefault(sub, {})
            _fill_missing_secrets(posted_sub, cur_sub, keys)

    return raw


# Rueckwaerts-kompatibler Alias (frueherer Name).
preserve_operators = preserve_secrets


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
        # DATENSCHUTZ-GUARD: Operatoren, Credentials, WLAN-Liste + Integra-
        # tions-Secrets NIE aus dem geposteten YAML verlieren — sie kommen
        # autoritativ aus der laufenden Config (Incident 2026-05-30).
        raw = preserve_secrets(raw, orch.config)
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
            # Atomar (tempfile + rename) + .bak via shared helper.
            # v0.33.0: NICHT mehr den rohen req.yaml_text schreiben — der
            # enthaelt den Stub-`operator:`-Block ohne Operatoren/Creds und
            # wuerde die Datei plaetten. Stattdessen die kanonische, merge-
            # korrigierte cfg serialisieren (analog persist_config: computed
            # `operator` + rig-Computed raus).
            from ...util.atomicfile import atomic_write_with_backup
            d = cfg.model_dump(
                exclude_none=True,
                exclude={
                    "rig": {"hamlib_id", "effective_max_power_w"},
                    "operator": True,
                },
            )
            atomic_write_with_backup(
                path,
                yaml.safe_dump(d, default_flow_style=False, sort_keys=False),
            )
        except OSError as exc:
            raise HTTPException(
                status_code=500,
                detail=f"config validated but writing {path} failed: {exc}",
            )

    set_config_for_tests(cfg)
    await orch.on_config_changed(cfg)
    return cfg
