"""Auto-detection of USB rigs via /dev/serial/by-id/."""

from __future__ import annotations

from pathlib import Path

from ft8_appliance.rig import detect_rigs


def _seed(tmp: Path, names: list[str]) -> Path:
    """Materialise *names* as zero-byte files in *tmp* and return *tmp*.

    The detector only checks names, not whether they're symlinks, so plain
    files are fine for the test.
    """
    for n in names:
        (tmp / n).touch()
    return tmp


def test_detect_empty_when_dir_missing() -> None:
    assert detect_rigs(by_id_dir=Path("/does/not/exist")) == []


def test_detect_ic705_native(tmp_path: Path) -> None:
    root = _seed(tmp_path, ["usb-Icom_Inc._IC-705-if00"])
    rigs = detect_rigs(by_id_dir=root)
    assert len(rigs) == 1
    assert rigs[0].model == "ic705"
    assert rigs[0].confidence == "high"
    assert rigs[0].serial_device == str(root / "usb-Icom_Inc._IC-705-if00")


def test_detect_ic9700_and_ic7610(tmp_path: Path) -> None:
    root = _seed(tmp_path, [
        "usb-Icom_Inc._IC-9700-if00",
        "usb-Icom_Inc._IC-7610-if00",
    ])
    rigs = detect_rigs(by_id_dir=root)
    models = {r.model for r in rigs}
    assert models == {"ic9700", "ic7610"}
    assert all(r.confidence == "high" for r in rigs)


def test_detect_ic7300_cp210x(tmp_path: Path) -> None:
    """Das IC-7300-Kabel traegt seinen Namen im USB-String — sicher erkannt.

    Bis 2026-09-14 galt jeder CP2102 als IC-7300. Seit der Digirig (auch ein
    CP2102, ohne Rig-Kennung) dazukommt, ist nur noch der benannte sicher."""
    root = _seed(tmp_path, [
        "usb-Silicon_Labs_CP2102_USB_to_UART_Bridge_Controller_IC-7300_03026783-if00-port0",
    ])
    d = detect_rigs(by_id_dir=root)
    assert len(d) == 1
    assert d[0].model == "ic7300" and d[0].confidence == "high"


def test_detect_namenloser_cp210x_ist_kein_sicherer_ic7300(tmp_path: Path) -> None:
    root = _seed(tmp_path, ["usb-Silicon_Labs_CP2102_USB_to_UART_Bridge_Controller_0001-if00-port0"])
    d = detect_rigs(by_id_dir=root)
    assert d[0].confidence == "low" and d[0].model != "ic7300"


def test_detect_qmx_plus(tmp_path: Path) -> None:
    """QRP Labs QMX/QMX+ ist via 'QRP_Labs' im USB-String eindeutig erkennbar."""
    root = _seed(tmp_path, ["usb-QRP_Labs_QMX_Transceiver-if00"])
    rigs = detect_rigs(by_id_dir=root)
    assert len(rigs) == 1
    assert rigs[0].model == "qmx_plus"
    assert rigs[0].confidence == "high"
    assert "QRP Labs" in rigs[0].description


def test_unknown_devices_ignored(tmp_path: Path) -> None:
    root = _seed(tmp_path, [
        "usb-Yaesu_FT-991A-if00",          # not in pattern table
        "usb-Some_GPS_Receiver-if00",      # not in pattern table
        "usb-Icom_Inc._IC-705-if00",       # known
    ])
    rigs = detect_rigs(by_id_dir=root)
    assert len(rigs) == 1
    assert rigs[0].model == "ic705"


def test_as_dict_shape(tmp_path: Path) -> None:
    root = _seed(tmp_path, ["usb-Icom_Inc._IC-705-if00"])
    d = detect_rigs(by_id_dir=root)[0].as_dict()
    assert set(d.keys()) == {"model", "confidence", "serial_device", "description"}
    assert d["description"].startswith("Icom IC-705")
