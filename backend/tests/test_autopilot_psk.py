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
from ft8_appliance.integrations.psk_bandaktivitaet import (
    ModusMeldungen,
    modus_meldungen,
    parse,
    parse_zeilen,
)
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


# ---------------------------------------------------------------------------
# FT4-Versuch nur, wenn im Grossfeld FT4 gemeldet wird (seit 2026-11-14)

# Antwort von psk-freq.pl?grid=JN am 14.11.2026 um 14:35, gekuerzt
PSK_FREQ_JN = """28080000 28026 4727 34 111
21080000 19739 3583 24 83
28070000 8239 1583 14 76
21070000 7632 1337 12 57
21140000 2634 345 3 11
28180000 738 101 2 5
14080000 1214 169 2 3
# frequency score #spots #tx #rx
# grid JN%, 5 mins
"""


def test_trennt_ft8_und_ft4_nach_zeilen() -> None:
    zeilen = parse_zeilen(PSK_FREQ_JN)
    assert modus_meldungen(zeilen, 21074, 21140) == ModusMeldungen(ft8=4920, ft4=345)
    assert modus_meldungen(zeilen, 28074, 28180) == ModusMeldungen(ft8=6310, ft4=101)


def test_keine_trennung_wo_sich_beide_eine_zeile_teilen() -> None:
    """20 m: FT8 14.074 und FT4 14.080 fallen beide in 14080000."""
    assert modus_meldungen(parse_zeilen(PSK_FREQ_JN), 14074, 14080) is None


def _orch_ft4() -> Orchestrator:
    o = _orch()
    o.config.operating.autopilot_allowed_modes = ["FT8", "FT4"]
    return o


def _grossfeld(o: Orchestrator, zeilen: dict[int, int], alter_s: float = 0.0) -> None:
    o._psk_grossfeld_zeilen = zeilen
    o._psk_grossfeld_at = time.monotonic() - alter_s


def _stats_modi(ft8: int, ft4: int = 0, *, ft4_versuche: int = 0, ft4_fertig: int = 0):
    return {
        ("15m", "FT8"): AutopilotStats("15m", "FT8", decodes=ft8),
        ("15m", "FT4"): AutopilotStats("15m", "FT4", decodes=ft4, attempts=ft4_versuche,
                                       completed=ft4_fertig),
    }


def test_kein_ft4_versuch_ohne_ft4_meldungen() -> None:
    o = _orch_ft4()
    _grossfeld(o, {21_070_000: 1337, 21_080_000: 3583, 21_140_000: 2})
    modus, grund = o._autopilot_mode_for_band("15m", ["FT8", "FT4"], _stats_modi(800))
    assert modus == "FT8"
    assert "FT4=2" in grund and "FT8=4920" in grund


def test_ft4_versuch_wenn_ft4_gemeldet_wird() -> None:
    o = _orch_ft4()
    _grossfeld(o, parse_zeilen(PSK_FREQ_JN))
    modus, _ = o._autopilot_mode_for_band("15m", ["FT8", "FT4"], _stats_modi(800))
    assert modus == "FT4"


def test_ohne_frische_meldungen_wie_bisher() -> None:
    """Schalter aus, Daten alt oder kaum FT8 gemeldet: FT8-Dichte entscheidet allein."""
    leer = {21_070_000: 1337, 21_080_000: 3583}
    aus = _orch_ft4()
    aus.config.operating.autopilot_psk_aktivitaet = False
    _grossfeld(aus, leer)
    alt = _orch_ft4()
    _grossfeld(alt, leer, alter_s=alt._PSK_AKTIVITAET_MAX_ALTER_S + 1)
    duenn = _orch_ft4()
    _grossfeld(duenn, {21_070_000: 3})
    nie = _orch_ft4()
    for o in (aus, alt, duenn, nie):
        assert o._autopilot_mode_for_band("15m", ["FT8", "FT4"], _stats_modi(800))[0] == "FT4"


def test_eigene_ft4_zahlen_gehen_vor() -> None:
    """Wer gerade auf FT4 dicht dekodiert oder genug gerufen hat, bleibt nach eigenen Zahlen."""
    o = _orch_ft4()
    _grossfeld(o, {21_070_000: 1337, 21_080_000: 3583})
    dicht = _stats_modi(0, 200)
    assert o._autopilot_mode_for_band("15m", ["FT8", "FT4"], dicht)[0] == "FT4"
    gut = _stats_modi(800, 40, ft4_versuche=6, ft4_fertig=3)
    assert o._autopilot_mode_for_band("15m", ["FT8", "FT4"], gut)[0] == "FT4"


async def test_sperre_steht_einmal_je_cooldown_im_protokoll() -> None:
    o = _orch_ft4()
    _grossfeld(o, {21_070_000: 1337, 21_080_000: 3583})
    o._autopilot_ft4_versuch_merken = AsyncMock()
    stats = _stats_modi(800)
    await o._autopilot_ft4_sperre_melden("15m", "FT8", stats, 1000.0)
    await o._autopilot_ft4_sperre_melden("15m", "FT8", stats, 1060.0)
    o._autopilot_ft4_versuch_merken.assert_awaited_once_with("15m", "gesperrt", 800)
    spaeter = 1000.0 + o.config.operating.autopilot_cooldown_min * 60
    await o._autopilot_ft4_sperre_melden("15m", "FT8", stats, spaeter)
    assert o._autopilot_ft4_versuch_merken.await_count == 2


async def test_keine_sperrmeldung_wenn_ohnehin_kein_versuch_anstand() -> None:
    o = _orch_ft4()
    _grossfeld(o, {21_070_000: 1337, 21_080_000: 3583})
    o._autopilot_ft4_versuch_merken = AsyncMock()
    await o._autopilot_ft4_sperre_melden("15m", "FT8", _stats_modi(100), 1000.0)  # zu duenn
    await o._autopilot_ft4_sperre_melden("15m", "FT4", _stats_modi(800), 1000.0)  # schon FT4
    o._autopilot_ft4_versuch_merken.assert_not_awaited()
