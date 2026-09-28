"""Der Notfall-Hotspot sendet mit der Passphrase aus der config.yaml — nie
mit dem Platzhalter aus dem oeffentlichen Repository.

Bis 2026-09-28 kopierte install.sh deploy/hostapd/ap.conf unveraendert nach
/etc/hostapd/ft8-ap.conf, und hostapd las genau diese Datei. Die zufaellige
Passphrase, die die Oberflaeche anzeigte, stand nur in der config.yaml.
Auf der laufenden Station war das nachweislich so: hostapd hatte den
Platzhalter, config.yaml etwas anderes. Fiel das Netz aus, haette die
Station einen Hotspot mit einer Passphrase aufgemacht, die jeder im Repo
nachlesen kann.
"""
from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

WURZEL = Path(__file__).resolve().parents[2]
SKRIPT = WURZEL / "deploy" / "scripts" / "render-ap-conf.py"
VORLAGE = WURZEL / "deploy" / "hostapd" / "ap.conf"


def _lauf(tmp_path: Path, ap: dict | None) -> tuple[int, Path]:
    import yaml
    konfig = tmp_path / "config.yaml"
    konfig.write_text(yaml.safe_dump({"network": {"ap_fallback": ap}} if ap is not None else {}))
    ziel = tmp_path / "ft8-ap.conf"
    r = subprocess.run([sys.executable, str(SKRIPT), str(VORLAGE), str(konfig), str(ziel)],
                       capture_output=True, text=True)
    return r.returncode, ziel


def test_passphrase_und_ssid_kommen_aus_der_konfiguration(tmp_path: Path) -> None:
    rc, ziel = _lauf(tmp_path, {"ssid": "stationsnetz", "psk": "Zufall-mit-20-Zeichen"})
    assert rc == 0
    text = ziel.read_text()
    assert "wpa_passphrase=Zufall-mit-20-Zeichen\n" in text
    assert "ssid=stationsnetz\n" in text
    assert "changeme-please" not in text
    assert stat.S_IMODE(os.stat(ziel).st_mode) == 0o600


@pytest.mark.parametrize("ap", [
    {"ssid": "x", "psk": "changeme-please"},   # der Platzhalter aus dem Repo
    {"ssid": "x", "psk": "ft8setup1"},         # die fruehere Vorgabe im Code
    {"ssid": "x", "psk": "kurz"},              # WPA verlangt 8 Zeichen
    {"ssid": "x"},                             # gar keine
    None,                                      # ganzer Block fehlt
    {"ssid": "x", "psk": "gutepassphrase\nctrl_interface=/tmp/x"},  # Zeile einschleusen
])
def test_ohne_gueltige_passphrase_kein_hotspot(tmp_path: Path, ap) -> None:
    rc, ziel = _lauf(tmp_path, ap)
    assert rc == 1
    assert not ziel.exists()


def test_hostapd_liest_nur_die_erzeugte_datei() -> None:
    unit = (WURZEL / "deploy" / "systemd" / "ft8-hostapd.service").read_text()
    assert "ExecStart=/usr/sbin/hostapd /run/ft8-ap.conf" in unit
    assert "/etc/hostapd/ft8-ap.conf\n" not in unit.split("[Service]", 1)[1].split("ExecStart=", 1)[1]


def test_startskript_erzeugt_vor_dem_eingriff_ins_wlan() -> None:
    """Scheitert das Erzeugen, darf wlan0 nicht schon im AP-Modus haengen —
    sonst waere die Station ohne Hotspot UND ohne WLAN."""
    s = (WURZEL / "deploy" / "scripts" / "start-ap-fallback.sh").read_text()
    assert s.index("render-ap-conf.py") < s.index("nmcli device set")


def test_platzhalterlisten_stimmen_ueberein() -> None:
    """Das Skript laeuft ohne App-Umgebung und fuehrt die Liste selbst."""
    from ft8_appliance.web.auth import PLATZHALTER_PSK
    s = SKRIPT.read_text()
    for p in PLATZHALTER_PSK:
        assert f'"{p}"' in s, p


def test_neue_passphrase_ist_zufaellig_und_gueltig() -> None:
    from ft8_appliance.web.auth import PLATZHALTER_PSK, neue_hotspot_passphrase
    a, b = neue_hotspot_passphrase(), neue_hotspot_passphrase()
    assert a != b and 8 <= len(a) <= 63 and a not in PLATZHALTER_PSK
