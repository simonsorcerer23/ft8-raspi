"""v0.22.0 — DX-Operating-Location API.

GET  /api/operating-location  → aktueller Stand (manuell-Setting, GPS-Detection,
                                Mismatch-Status fuer Dashboard-Banner)
POST /api/operating-location  → setze current_operating_country
                                (null/leer = zurueck auf Heimat)
GET  /api/operating-location/countries → Liste verfuegbarer Country-Codes
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ...integrations.cept import COUNTRIES, cept_compliance, detect_from_gps
from ...runtime import Orchestrator
from ..deps import get_orchestrator

router = APIRouter()


class CountryInfoOut(BaseModel):
    code: str
    name: str
    is_home: bool = False
    cept_class_e_allowed: bool = False
    cept_class_a_max_w: int | None = None


class OperatingLocationOut(BaseModel):
    home_country: str
    home_country_name: str
    current_country: str | None
    current_country_name: str | None
    tx_callsign: str  # was wir tatsaechlich senden (mit DX-Prefix wenn aktiv)
    operator_callsign: str
    # GPS-Detection
    gps_detected_country: str | None
    gps_detected_name: str | None
    gps_fix_mode: int
    # Mismatch-Status fuer Dashboard-Banner
    mismatch: bool
    mismatch_reason: str | None
    # CEPT-Compliance fuer aktuellen Stand
    cept_allowed: bool
    cept_lock_reason: str | None
    # Effektiver Power-Cap (falls aenderung durch CEPT-Cap)
    effective_max_power_w: int | None  # None = wie home


class SetCountryRequest(BaseModel):
    country: str | None  # None / "" = zurueck auf Heimat


class CountriesResponse(BaseModel):
    countries: list[CountryInfoOut]


@router.get("/operating-location", response_model=OperatingLocationOut)
async def get_operating_location(
    orch: Orchestrator = Depends(get_orchestrator),
) -> OperatingLocationOut:
    op = orch.config.operator
    sm = orch.state_machine
    gps = orch.gps.snapshot

    home_info = COUNTRIES.get(op.home_country)
    current_info = COUNTRIES.get(op.current_operating_country) if op.current_operating_country else None

    # GPS-Detection
    gps_country = None
    if gps.mode and gps.mode >= 2 and gps.lat is not None:
        gps_country = detect_from_gps(gps.lat, gps.lon)
    gps_info = COUNTRIES.get(gps_country) if gps_country else None

    # Mismatch-Berechnung
    effective_current = op.current_operating_country or op.home_country
    mismatch = bool(gps_country and gps_country != effective_current)
    mismatch_reason = None
    if mismatch:
        if op.current_operating_country is None or op.current_operating_country == op.home_country:
            mismatch_reason = (
                f"GPS lokalisiert dich in {gps_info.name if gps_info else gps_country} "
                f"({gps_country}), aber du sendest als Heimat ({op.home_country})."
            )
        elif gps_country == op.home_country:
            mismatch_reason = (
                f"GPS sagt du bist daheim, aber du sendest noch als "
                f"{op.current_operating_country}/{op.callsign}."
            )
        else:
            mismatch_reason = (
                f"Du sendest als {op.current_operating_country}/{op.callsign}, "
                f"aber GPS sagt {gps_info.name if gps_info else gps_country} ({gps_country})."
            )

    # CEPT-Compliance
    allowed, lock_reason = cept_compliance(
        op.current_operating_country, op.home_country, op.license_class,
    )

    # Effective max-power wenn aktuell Auslandsbetrieb
    eff_max = None
    if op.current_operating_country and op.current_operating_country != op.home_country:
        eff_max = current_info.cept_class_a_max_w if current_info else None

    return OperatingLocationOut(
        home_country=op.home_country,
        home_country_name=home_info.name if home_info else op.home_country,
        current_country=op.current_operating_country,
        current_country_name=current_info.name if current_info else None,
        tx_callsign=sm.ctx.tx_callsign,
        operator_callsign=op.callsign,
        gps_detected_country=gps_country,
        gps_detected_name=gps_info.name if gps_info else None,
        gps_fix_mode=gps.mode or 0,
        mismatch=mismatch,
        mismatch_reason=mismatch_reason,
        cept_allowed=allowed,
        cept_lock_reason=lock_reason,
        effective_max_power_w=eff_max,
    )


@router.post("/operating-location", response_model=OperatingLocationOut)
async def set_operating_location(
    req: SetCountryRequest,
    orch: Orchestrator = Depends(get_orchestrator),
) -> OperatingLocationOut:
    """Setze current_operating_country fuer den aktiven Operator.

    None oder "" → zurueck auf Heimat (kein DX-Prefix).
    Sonst Country-Code aus COUNTRIES (z.B. "9A", "F", "SV").
    """
    country = (req.country or "").strip().upper() or None
    if country is not None and country not in COUNTRIES:
        raise HTTPException(
            status_code=400,
            detail=f"unbekanntes Land {country!r}. Verfuegbar: {sorted(COUNTRIES.keys())}",
        )
    # Wenn Heimat-Country gewaehlt: gleicher Effekt wie None (kein Prefix)
    op = orch.config.operator
    if country == op.home_country:
        country = None
    # CEPT-Compliance-Check VOR dem Schreiben — wenn Klasse-E + Land
    # nicht erlaubt, sofort 403. UI zeigt das eh als rote Warnung.
    allowed, reason = cept_compliance(country, op.home_country, op.license_class)
    if not allowed:
        raise HTTPException(status_code=403, detail=reason)

    op.current_operating_country = country
    # State-Machine Context spiegeln (greift sofort beim nächsten TX)
    orch.state_machine.ctx.current_operating_country = country
    # Persist nach YAML damit Reboot-fest
    await orch.persist_config()
    # GPS-Push-Throttle resetten — neue manuelle Setzung soll
    # nicht durch alten Push-Cache unterdrueckt werden
    orch._gps_last_pushed_country = None
    orch._gps_country_consistent_since = 0.0

    return await get_operating_location(orch=orch)


@router.get("/operating-location/countries", response_model=CountriesResponse)
async def list_countries() -> CountriesResponse:
    items: list[CountryInfoOut] = []
    for code, info in COUNTRIES.items():
        items.append(CountryInfoOut(
            code=code,
            name=info.name,
            is_home=(code == "DL"),  # heuristisch — Sebastian + Ray sind DL
            cept_class_e_allowed=info.cept_class_e_allowed,
            cept_class_a_max_w=info.cept_class_a_max_w,
        ))
    return CountriesResponse(countries=items)
