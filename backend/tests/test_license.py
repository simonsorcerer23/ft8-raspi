"""Tests für das License-Framework — Band-Allowlists + Power-Caps.

Stand der Tabellen: BNetzA AFuV nach Reform Juni 2024 (siehe
``config/license.py``).
"""

from __future__ import annotations

import pytest

from ft8_appliance.config.license import (
    LICENSE_BANDS,
    is_band_allowed,
    max_power_for,
)


# ---------------------------------------------------------------------------
# Klasse A — Volllizenz, Ray (DK9XR)
# ---------------------------------------------------------------------------
class TestKlasseA:
    def test_alle_haeufigen_baender_erlaubt(self) -> None:
        for band in ["160m", "80m", "40m", "30m", "20m", "17m", "15m",
                     "12m", "10m", "6m", "2m", "70cm"]:
            assert is_band_allowed("A", band), f"Klasse A soll {band} dürfen"

    def test_60m_erlaubt_aber_15w_cap(self) -> None:
        """Sonderfall: 60m ist sekundär, hartes EIRP-Limit 15W."""
        assert is_band_allowed("A", "60m")
        assert max_power_for("A", "60m") == 15

    def test_default_750w_auf_anderen_baendern(self) -> None:
        for band in ["80m", "40m", "20m", "10m", "2m"]:
            assert max_power_for("A", band) == 750


# ---------------------------------------------------------------------------
# Klasse E — Einsteiger, das ist Sebastians Klasse
# ---------------------------------------------------------------------------
class TestKlasseE:
    def test_erlaubte_baender(self) -> None:
        assert LICENSE_BANDS["E"] == frozenset(
            {"80m", "15m", "10m", "2m", "70cm"}
        )

    def test_nicht_erlaubte_baender(self) -> None:
        """40m, 30m, 20m, 17m, 12m, 60m, 160m, 6m — alle gesperrt für E."""
        for band in ["160m", "60m", "40m", "30m", "20m", "17m", "12m", "6m"]:
            assert not is_band_allowed("E", band), \
                f"Klasse E darf {band} NICHT — Bug in der Allowlist!"
            assert max_power_for("E", band) is None

    def test_100w_auf_hf(self) -> None:
        assert max_power_for("E", "80m") == 100
        assert max_power_for("E", "15m") == 100
        assert max_power_for("E", "10m") == 100

    def test_75w_auf_vhf_uhf(self) -> None:
        assert max_power_for("E", "2m") == 75
        assert max_power_for("E", "70cm") == 75


# ---------------------------------------------------------------------------
# Klasse N — Newcomer (seit Juni 2024)
# ---------------------------------------------------------------------------
class TestKlasseN:
    def test_erlaubte_baender_sehr_eingeschraenkt(self) -> None:
        assert LICENSE_BANDS["N"] == frozenset({"160m", "2m", "70cm"})

    def test_10m_nicht_erlaubt_weil_ft8_freq_ausserhalb_n_segment(self) -> None:
        """N darf 10m nur 29.510-29.700 MHz — übliche FT8 ist 28.074 MHz."""
        assert not is_band_allowed("N", "10m")

    def test_10w_cap_ueberall(self) -> None:
        for band in ["160m", "2m", "70cm"]:
            assert max_power_for("N", band) == 10


# ---------------------------------------------------------------------------
# AppConfig-Integration: effective_max_power_w() und can_tx_on()
# ---------------------------------------------------------------------------
def _make_config(license_class: str, rig_model: str, antenna_bands: list[str]):
    """Helper — kompakter AppConfig-Aufbau für die Integrations-Tests."""
    from ft8_appliance.config.models import (
        AntennaConfig,
        AppConfig,
        BandConfig,
        OperatorConfig,
        RigConfig,
    )

    return AppConfig(
        operator=OperatorConfig(
            callsign="DK9XR",
            license_class=license_class,  # type: ignore[arg-type]
            default_power_w=100,
        ),
        bands=[
            BandConfig(name="80m", freq_khz=3573),
            BandConfig(name="60m", freq_khz=5357),
            BandConfig(name="40m", freq_khz=7074),
            BandConfig(name="20m", freq_khz=14074),
            BandConfig(name="15m", freq_khz=21074),
            BandConfig(name="10m", freq_khz=28074),
        ],
        antennas=[AntennaConfig(name="antall", bands=antenna_bands)],
        rig=RigConfig(model=rig_model),  # type: ignore[arg-type]
    )


def test_klasse_a_ic7300_kann_20m() -> None:
    cfg = _make_config("A", "ic7300", ["80m", "40m", "20m", "15m", "10m"])
    assert cfg.can_tx_on("20m")
    # MIN(license=750, rig=100, operator=100) = 100
    assert cfg.effective_max_power_w("20m") == 100


def test_klasse_a_60m_gedeckelt_auf_15w() -> None:
    cfg = _make_config("A", "ic7300", ["60m"])
    assert cfg.can_tx_on("60m")
    assert cfg.effective_max_power_w("60m") == 15


def test_klasse_e_20m_gesperrt_trotz_antenne() -> None:
    """Klasse E darf 20m NICHT — auch wenn Antenne das könnte."""
    cfg = _make_config("E", "ic7300", ["20m", "15m", "10m"])
    assert not cfg.can_tx_on("20m")
    assert cfg.effective_max_power_w("20m") == 0


def test_klasse_e_15m_erlaubt() -> None:
    cfg = _make_config("E", "ic7300", ["15m"])
    assert cfg.can_tx_on("15m")
    # MIN(license=100, rig=100, operator=100) = 100
    assert cfg.effective_max_power_w("15m") == 100


def test_klasse_e_qmx_plus_15m() -> None:
    """Sebastians wahrscheinliches Setup: Klasse E + QMX+ → 5W cap."""
    cfg = _make_config("E", "qmx_plus", ["80m", "15m", "10m"])
    assert cfg.can_tx_on("15m")
    # MIN(license=100, rig=5, operator=100) = 5
    assert cfg.effective_max_power_w("15m") == 5


def test_antenne_fehlt_blockiert_tx() -> None:
    """can_tx_on muss auch dann False sein wenn das Band zwar erlaubt
    aber keine passende Antenne dranhängt."""
    cfg = _make_config("A", "ic7300", ["80m"])  # nur 80m-Antenne
    assert not cfg.can_tx_on("20m"), "ohne 20m-Antenne kein TX"
