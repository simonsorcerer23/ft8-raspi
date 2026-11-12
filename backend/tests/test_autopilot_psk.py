"""Autopilot mit PSK-Aktivitaet im eigenen Feld (seit 2026-11-12).

Der Autopilot zaehlte nur das Band, auf dem das Rig stand. Am MK2 (DO3XR,
15/10 m an Raymonds Antenne) wechselte er darum erst, wenn 15 m einschlief —
nie, weil 10 m besser lief. psk-freq.pl zeigt die Meldungen je Band im Feld;
daraus schaetzt er die Decodes fuer die anderen Baender.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock

from ft8_appliance.config import (
    AntennaConfig,
    AppConfig,
    BandConfig,
    OperatingConfig,
    OperatorConfig,
)
from ft8_appliance.integrations.psk_bandaktivitaet import parse
from ft8_appliance.rig.rigctld_client import RigSnapshot
from ft8_appliance.runtime import FakeSlotClock, Orchestrator
from ft8_appliance.runtime.orchestrator import AutopilotStats

# Antwort von psk-freq.pl?grid=JN58 am 12.11.2026 um 17:25, gekuerzt
PSK_FREQ_JN58 = """7080000 3559 418 1 2
10140000 1291 215 1 3
14080000 703 95 1 3
21070000 499 61 0 2
21080000 92 45 0 3
28080000 9 6 0 1
28070000 3 2 0 2
2400040000 10 1 1 0
# frequency score #spots #tx #rx
# grid JN58, 5 mins
"""


def test_parse_summiert_meldungen_je_band() -> None:
    spots = parse(PSK_FREQ_JN58)
    assert spots == {"40m": 418, "30m": 215, "20m": 95, "15m": 106, "10m": 8}


def test_parse_uebersteht_muell() -> None:
    assert parse("<html>Fehler</html>\nabc def ghi\n\n") == {}


def _orch(psk_an: bool = True) -> Orchestrator:
    cfg = AppConfig(
        operator=OperatorConfig(callsign="DO3XR", default_locator="JN58ch", license_class="E"),
        bands=[
            BandConfig(name="15m", freq_khz=21074, antenna="gross"),
            BandConfig(name="10m", freq_khz=28074, antenna="gross"),
        ],
        antennas=[AntennaConfig(name="gross", bands=["15m", "10m"], auto_band_switch=True)],
        operating=OperatingConfig(
            autopilot_enabled=True, autopilot_allowed_bands=["15m", "10m"],
            autopilot_allowed_modes=["FT8"], autopilot_psk_aktivitaet=psk_an,
        ),
    )
    gps = AsyncMock()
    gps.snapshot = type("Gps", (), {"mode": 3, "lat": None, "lon": None, "ts": None,
                                    "lock_for_min": None, "satellites_used": None})()

    async def _keine(_tick):
        return []

    o = Orchestrator(config=cfg, rig=AsyncMock(), gps=gps, decode_source=_keine,
                     slot_clock=FakeSlotClock(count=0), db_enabled=False)
    o._active_antenna = "gross"
    o._last_rig = RigSnapshot(freq_hz=21_074_000)
    return o


def _stats(d15: int, d10: int = 0, *, versuche: int = 0, fertig: int = 0):
    return {
        ("15m", "FT8"): AutopilotStats("15m", "FT8", decodes=d15, attempts=versuche, completed=fertig),
        ("10m", "FT8"): AutopilotStats("10m", "FT8", decodes=d10),
    }


def _psk(o: Orchestrator, spots: dict[str, int], alter_s: float = 0.0) -> None:
    o._psk_bandaktivitaet = spots
    o._psk_bandaktivitaet_at = time.monotonic() - alter_s


def test_schaetzt_das_andere_band_hoch() -> None:
    o = _orch()
    _psk(o, {"15m": 50, "10m": 150})
    neu, genutzt = o._autopilot_psk_schaetzung(_stats(100, versuche=4, fertig=1), "15m")
    assert genutzt is True
    assert neu[("10m", "FT8")].decodes == 300
    # das aktive Band und seine echten Versuche bleiben unberuehrt
    assert neu[("15m", "FT8")] == AutopilotStats("15m", "FT8", 100, 4, 1)
    assert neu[("10m", "FT8")].attempts == 0


def test_ohne_schalter_veraltet_oder_zu_duenn_keine_schaetzung() -> None:
    aus = _orch(psk_an=False)
    _psk(aus, {"15m": 50, "10m": 150})
    assert aus._autopilot_psk_schaetzung(_stats(100), "15m") == (_stats(100), False)

    alt = _orch()
    _psk(alt, {"15m": 50, "10m": 150}, alter_s=alt._PSK_AKTIVITAET_MAX_ALTER_S + 1)
    assert alt._autopilot_psk_schaetzung(_stats(100), "15m")[1] is False

    duenn = _orch()
    _psk(duenn, {"15m": 2, "10m": 150})
    assert duenn._autopilot_psk_schaetzung(_stats(100), "15m")[1] is False


def test_wechselt_wenn_das_andere_band_klar_mehr_los_hat() -> None:
    o = _orch()
    _psk(o, {"15m": 20, "10m": 200})
    stats, _ = o._autopilot_psk_schaetzung(_stats(40), "15m")
    entscheidung = o._autopilot_decision("15m", "FT8", stats)
    assert entscheidung is not None and entscheidung.band == "10m"


def test_bleibt_wenn_es_hier_laeuft() -> None:
    """Am 12.11. um 17:25: 15 m 106 Meldungen, 10 m 8 — kein Grund zu wechseln."""
    o = _orch()
    _psk(o, parse(PSK_FREQ_JN58))
    stats, genutzt = o._autopilot_psk_schaetzung(_stats(900, versuche=10, fertig=3), "15m")
    assert genutzt is True
    assert o._autopilot_decision("15m", "FT8", stats) is None


def test_ohne_psk_kein_wechsel_bei_laufendem_band() -> None:
    """Das alte Verhalten bleibt: ohne Schaetzung zaehlt nur das aktive Band."""
    o = _orch(psk_an=False)
    _psk(o, {"15m": 20, "10m": 200})
    stats, _ = o._autopilot_psk_schaetzung(_stats(40), "15m")
    assert o._autopilot_decision("15m", "FT8", stats) is None
