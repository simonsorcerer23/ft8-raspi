"""Das WLAN-Hilfsskript deploy/scripts/ft8-wlan.

Der Dienstbenutzer darf per sudo nur dieses Skript, nicht nmcli. Bis
2026-11-28 durfte er "nmcli connection add|modify|delete|up *" (Audit vom
28.11.): Wer die Weboberflaeche uebernahm, konnte jede Verbindung umbauen,
auch die kabelgebundene. Die Tests laufen mit einem nachgebauten nmcli, das
seine Aufrufe mitschreibt.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

WURZEL = Path(__file__).resolve().parents[2]
SKRIPT = WURZEL / "deploy" / "scripts" / "ft8-wlan"

pytestmark = pytest.mark.skipif(os.geteuid() == 0, reason="Test-nmcli gilt nie fuer root")

FALSCHES_NMCLI = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$PROTOKOLL"
if [ "$1" = "-g" ] && [ "$2" = "connection.type" ]; then
    case "$6" in
        Heim*) echo 802-11-wireless ;;
        "Wired connection 1") echo 802-3-ethernet ;;
        *) exit 10 ;;
    esac
fi
exit 0
"""


@pytest.fixture
def lauf(tmp_path: Path):
    nmcli = tmp_path / "nmcli"
    nmcli.write_text(FALSCHES_NMCLI)
    nmcli.chmod(0o755)
    protokoll = tmp_path / "aufrufe"
    protokoll.touch()

    def _lauf(*args: str, eingabe: str = "") -> tuple[int, list[str], str]:
        r = subprocess.run(
            ["bash", str(SKRIPT), *args], input=eingabe, capture_output=True, text=True,
            env={"PATH": os.environ["PATH"], "FT8_WLAN_NMCLI": str(nmcli),
                 "PROTOKOLL": str(protokoll)},
        )
        return r.returncode, protokoll.read_text().splitlines(), r.stderr
    return _lauf


def test_neues_wlan_mit_passwort(lauf) -> None:
    rc, aufrufe, _ = lauf("add", "Heim Netz", "50", "yes", eingabe="geheimgeheim\n")
    assert rc == 0
    assert aufrufe[0] == "connection add type wifi con-name Heim Netz ifname wlan0 ssid Heim Netz"
    assert aufrufe[1] == ("connection modify id Heim Netz connection.autoconnect yes "
                          "connection.autoconnect-priority 50 wifi-sec.key-mgmt wpa-psk "
                          "wifi-sec.psk geheimgeheim")


def test_offenes_wlan_ohne_passwort(lauf) -> None:
    rc, aufrufe, _ = lauf("add", "Heim", "10", "no", eingabe="\n")
    assert rc == 0
    assert "wifi-sec" not in aufrufe[1]


@pytest.mark.parametrize("args", [
    ("add", "-x", "50", "yes"),                       # Option statt Name
    ("add", "Heim", "50; reboot", "yes"),             # Prioritaet keine Zahl
    ("add", "Heim", "50", "vielleicht"),
    ("add", "A" * 33, "50", "yes"),                   # SSID zu lang
    ("add", "Heim", "50", "yes", "ipv4.dns", "1.2.3.4"),  # zusaetzliche Eigenschaften
    ("modify", "Heim", "ipv4.method", "manual"),      # keine freie Aenderung
    ("delete", "Wired connection 1"),                 # kein WLAN-Profil
    ("up", "Wired connection 1"),
    ("priority", "Wired connection 1", "100"),
    ("delete", "Unbekannt"),
    ("rescan", "extra"),
    (),
])
def test_abgelehnt_ohne_schreibenden_aufruf(lauf, args) -> None:
    rc, aufrufe, _ = lauf(*args, eingabe="geheimgeheim\n")
    assert rc != 0
    schreibend = [a for a in aufrufe if not a.startswith("-g connection.type")]
    assert schreibend == [], schreibend


def test_zu_kurzes_passwort(lauf) -> None:
    rc, aufrufe, err = lauf("add", "Heim", "50", "yes", eingabe="kurz\n")
    assert rc != 0 and aufrufe == []
    assert "8 bis 63" in err


def test_wlan_profil_loeschen_starten_priorisieren(lauf) -> None:
    assert lauf("delete", "Heim")[0] == 0
    assert lauf("up", "Heim")[0] == 0
    rc, aufrufe, _ = lauf("priority", "Heim", "-5")
    assert rc == 0
    assert "connection delete id Heim" in aufrufe
    assert "connection up id Heim" in aufrufe
    assert aufrufe[-1] == "connection modify id Heim connection.autoconnect-priority -5"


def test_root_nimmt_immer_das_echte_nmcli() -> None:
    s = SKRIPT.read_text()
    assert 'NMCLI=/usr/bin/nmcli' in s
    assert '[ "$(id -u)" -ne 0 ] && [ -n "${FT8_WLAN_NMCLI:-}" ]' in s


def test_self_update_und_install_liefern_das_skript() -> None:
    assert os.access(SKRIPT, os.X_OK)
    su = (WURZEL / "scripts" / "self-update.sh").read_text()
    assert su.count('"deploy/scripts/ft8-wlan"') == 2      # beide Sync-Zweige
    # Erst das sudoers-Snippet, das die Installation erlaubt, dann das Skript.
    assert su.index('"/etc/sudoers.d/ft8-self-update"') < su.index('"/usr/local/sbin/ft8-wlan"')
    assert "/usr/local/sbin/ft8-wlan" in (WURZEL / "deploy" / "install.sh").read_text()
    for name in ("ft8-self-update.in", "ft8-self-update"):
        s = (WURZEL / "deploy" / "sudoers.d" / name).read_text()
        assert "deploy/scripts/ft8-wlan /usr/local/sbin/ft8-wlan" in s, name
        assert "/usr/bin/nmcli" not in s, name
