"""WiFi management — list, scan, add, delete, prioritise NetworkManager profiles.

Mounted under ``/api/network``. The actual nmcli plumbing lives in
:mod:`ft8_appliance.util.network`; this layer only translates HTTP to it.

Plus AP-Fallback config endpoint: SSID + PSK des Notfall-WLANs das
hochkommt wenn der Pi sich nirgendwo verbinden kann. Lebt hier auf der
WLAN-Seite statt im Config-Panel weil's semantisch zum Netzwerk-Setup
gehört (User-Feedback Sebastian).
"""

from __future__ import annotations

import logging

import yaml
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ...config import get_config, set_config_for_tests
from ...config.loader import get_current_path
from ...config.models import AppConfig
from ...runtime import Orchestrator
from ...util import network as net
from ..deps import get_orchestrator

log = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
class WifiConnectionOut(BaseModel):
    name: str
    ssid: str
    autoconnect: bool
    priority: int
    active: bool


class WifiScanOut(BaseModel):
    ssid: str
    bssid: str
    signal: int
    security: str
    in_use: bool


class WifiOverview(BaseModel):
    connections: list[WifiConnectionOut]
    scan: list[WifiScanOut]


class AddConnectionRequest(BaseModel):
    ssid: str = Field(min_length=1, max_length=32)
    psk: str | None = None       # None = open WiFi
    priority: int = 50
    autoconnect: bool = True


class SetPriorityRequest(BaseModel):
    priority: int = Field(ge=-999, le=999)


# ---------------------------------------------------------------------------
@router.get("/network/wifi", response_model=WifiOverview)
async def wifi_overview() -> WifiOverview:
    """Return saved profiles + the latest scan in one shot.

    The scan refreshes synchronously (~2s); call /network/wifi/connections
    if you only need the saved list and want a snappier response.
    """
    conns = await net.list_connections()
    scan = await net.scan_networks(rescan=True)
    return WifiOverview(
        connections=[WifiConnectionOut(**net._as_dict(c)) for c in conns],
        scan=[WifiScanOut(**net._as_dict(s)) for s in scan],
    )


@router.get("/network/wifi/connections", response_model=list[WifiConnectionOut])
async def list_wifi_connections() -> list[WifiConnectionOut]:
    conns = await net.list_connections()
    return [WifiConnectionOut(**net._as_dict(c)) for c in conns]


@router.get("/network/wifi/scan", response_model=list[WifiScanOut])
async def scan_wifi() -> list[WifiScanOut]:
    scan = await net.scan_networks(rescan=True)
    return [WifiScanOut(**net._as_dict(s)) for s in scan]


@router.post("/network/wifi/connections", status_code=201)
async def add_wifi(req: AddConnectionRequest) -> dict:
    ok, msg = await net.add_connection(
        req.ssid, req.psk, priority=req.priority, autoconnect=req.autoconnect,
    )
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"name": msg, "ssid": req.ssid}


@router.delete("/network/wifi/connections/{name}")
async def delete_wifi(name: str) -> dict:
    ok, msg = await net.delete_connection(name)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"deleted": name}


@router.put("/network/wifi/connections/{name}/priority")
async def set_wifi_priority(name: str, req: SetPriorityRequest) -> dict:
    ok, msg = await net.set_priority(name, req.priority)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"name": name, "priority": req.priority}


@router.post("/network/wifi/connections/{name}/activate")
async def activate_wifi(name: str) -> dict:
    ok, msg = await net.activate(name)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"name": name, "activated": True}


# ---------------------------------------------------------------------------
# AP-Fallback (Notfall-Hotspot wenn alle bekannten WLANs unerreichbar).
# Lebt hier statt im ConfigPanel — Sebastian's UX-Wunsch:
# "Netzwerk-Settings alle an einem Ort".
class APFallbackOut(BaseModel):
    ssid: str
    psk: str


class APFallbackRequest(BaseModel):
    ssid: str = Field(min_length=1, max_length=32)
    psk: str = Field(min_length=8, max_length=63)  # WPA-PSK 8..63 Zeichen


@router.get("/network/ap-fallback", response_model=APFallbackOut)
async def get_ap_fallback() -> APFallbackOut:
    cfg = get_config()
    if cfg.network is None or cfg.network.ap_fallback is None:
        # Fallback-Defaults wenn Config-Block fehlt — User kann das
        # dann im UI auf gewünschte Werte überschreiben.
        return APFallbackOut(ssid="ft8-hochgericht", psk="ft8setup1")
    return APFallbackOut(
        ssid=cfg.network.ap_fallback.ssid,
        psk=cfg.network.ap_fallback.psk,
    )


@router.put("/network/ap-fallback", response_model=APFallbackOut)
async def set_ap_fallback(
    req: APFallbackRequest,
    orch: Orchestrator = Depends(get_orchestrator),
) -> APFallbackOut:
    """Schreibt SSID + PSK des AP-Fallbacks ins config.yaml.

    Geht über denselben Hot-Reload-Pfad wie /api/config — Pydantic
    validiert (Längen-Constraints), File-Write atomisch, dann
    Orchestrator.on_config_changed damit der nftables/hostapd-
    Reload-Hook das neue AP-Profil verteilt.
    """
    cfg = get_config()
    # Pydantic computed_fields werden von model_dump() mit-emittiert,
    # aber von model_validate() unter extra='forbid' rejected. Strip
    # sie raus bevor wir re-validieren. Sebastian 2026-05-27 Bug: AP-
    # Fallback-Save crashte mit "rig.hamlib_id Extra inputs not permitted".
    new_dict = cfg.model_dump(exclude={
        "operator": True,  # computed (mirrors operators[active_callsign])
        "rig": {"hamlib_id", "effective_max_power_w"},  # computed
    })
    new_dict.setdefault("network", {})
    new_dict["network"]["ap_fallback"] = {
        "ssid": req.ssid,
        "psk": req.psk,
    }
    try:
        new_cfg = AppConfig.model_validate(new_dict)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"invalid: {exc}")

    path = get_current_path()
    if path is not None:
        try:
            path.write_text(
                yaml.safe_dump(new_dict, sort_keys=False, allow_unicode=True),
                encoding="utf-8",
            )
        except OSError as exc:
            raise HTTPException(
                status_code=500, detail=f"writing config failed: {exc}"
            )

    set_config_for_tests(new_cfg)
    await orch.on_config_changed(new_cfg)
    return APFallbackOut(ssid=req.ssid, psk=req.psk)
