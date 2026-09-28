"""Der Controller — und mit ihm die Weboberflaeche — laeuft unter einem
Dienstbenutzer ohne sudo.

Bis 2026-09-28 lief er als Anmeldebenutzer, und der hatte auf der Station
"NOPASSWD: ALL". Wer die Weboberflaeche uebernahm, war root. Die Tests
halten fest, was den Dienstbenutzer von root trennt: welche Befehle er per
sudo darf (nur die, die die Oberflaeche ausloest) und dass rigctld, dessen
Argumente aus seiner config.yaml stammen, nicht unter einem Benutzer mit
mehr Rechten laeuft.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

WURZEL = Path(__file__).resolve().parents[2]
RENDER = WURZEL / "deploy" / "render-install-files.sh"


def _render(tmp_path: Path, **env: str) -> Path:
    install_env = tmp_path / "install.env"
    zeilen = {"APP_USER": "admin", "APP_GROUP": "admin", "APP_HOME": "/home/admin",
              "APP_DIR": "/home/admin/ft8-raspi", **env}
    install_env.write_text("".join(f"{k}={v}\n" for k, v in zeilen.items()))
    aus = tmp_path / "out"
    subprocess.run(["bash", str(RENDER), str(aus), str(install_env)], check=True,
                   env={**os.environ, "PATH": os.environ.get("PATH", "")})
    return aus


def _dienst_regeln(sudoers: str, benutzer: str) -> list[str]:
    """Alle Befehle, die *benutzer* per sudo darf (Fortsetzungszeilen zusammengefuegt)."""
    text = re.sub(r"\\\n\s*", " ", sudoers)
    befehle = []
    for zeile in text.splitlines():
        if zeile.startswith(f"{benutzer} ALL=(root) NOPASSWD:"):
            befehle += [b.strip() for b in zeile.split("NOPASSWD:", 1)[1].split(",")]
    return befehle


def test_ohne_dienstbenutzer_bleibt_alles_wie_es_war(tmp_path: Path) -> None:
    aus = _render(tmp_path)
    unit = (aus / "systemd" / "ft8-controller.service").read_text()
    assert "User=admin\n" in unit and "Group=admin\n" in unit


def test_controller_und_rigctld_laufen_als_dienstbenutzer(tmp_path: Path) -> None:
    aus = _render(tmp_path, SERVICE_USER="ft8", SERVICE_GROUP="ft8")
    for unit in ("ft8-controller.service", "ft8-rigctld.service"):
        text = (aus / "systemd" / unit).read_text()
        assert "User=ft8\n" in text, unit
        assert "User=admin" not in text, unit
    # Self-Update bleibt beim App-Benutzer — er baut und installiert.
    assert "User=admin\n" in (aus / "systemd" / "ft8-self-update.service").read_text()


def test_dienstbenutzer_darf_nur_was_die_oberflaeche_ausloest(tmp_path: Path) -> None:
    aus = _render(tmp_path, SERVICE_USER="ft8", SERVICE_GROUP="ft8")
    regeln = _dienst_regeln((aus / "sudoers.d" / "ft8-self-update").read_text(), "ft8")
    assert regeln, "keine Regeln fuer den Dienstbenutzer"
    for r in regeln:
        assert not r.startswith(("/usr/bin/install", "/usr/sbin/visudo")), r
        assert r != "ALL" and "ALL" not in r.split(), r
        assert r.startswith(("/bin/systemctl ", "/sbin/shutdown ", "/usr/bin/nmcli ")), r
    # Kein Neustart beliebiger Units, kein Stoppen des Controllers von aussen
    assert not any("systemctl restart ft8-rigctld" in r for r in regeln)


def test_jeder_sudo_aufruf_im_code_ist_erlaubt(tmp_path: Path) -> None:
    """Fehlt eine Regel, scheitert der Knopf in der Oberflaeche erst an der
    Station — genau so fiel 2026-09-06 der Hotspot aus."""
    aus = _render(tmp_path, SERVICE_USER="ft8", SERVICE_GROUP="ft8")
    regeln = _dienst_regeln((aus / "sudoers.d" / "ft8-self-update").read_text(), "ft8")
    noetig = [
        "/bin/systemctl restart --no-block ft8-controller.service",   # Demo-Schalter
        "/bin/systemctl start --no-block ft8-self-update.service",    # "Jetzt updaten"
        "/bin/systemctl start ft8-ap-fallback.service",
        "/bin/systemctl stop ft8-ap-fallback.service",
        "/sbin/shutdown -h +0 *", "/sbin/shutdown -r +0 *",
        "/usr/bin/nmcli device wifi rescan",
    ]
    for n in noetig:
        assert n in regeln, n
    code = "\n".join(p.read_text() for p in (WURZEL / "backend" / "ft8_appliance").rglob("*.py"))
    assert '"systemctl", action, "ft8-ap-fallback.service"' in code
    assert '"/bin/systemctl", "restart", "--no-block",' in code


def test_umstellung_schuetzt_install_env() -> None:
    s = (WURZEL / "deploy" / "dienstbenutzer-einrichten.sh").read_text()
    # root-eigenes Verzeichnis mit Sticky-Bit: config.yaml schreibbar,
    # install.env nicht ersetzbar
    assert 'chown root:"${DIENST}" "${ETC}"' in s and 'chmod 1775 "${ETC}"' in s
    assert 'chown root:root "${INSTALL_ENV}"' in s
    # nicht in sudo und nicht in der Gruppe des Anmeldebenutzers
    assert "for g in sudo adm" in s


@pytest.mark.skipif(shutil.which("visudo") is None, reason="visudo fehlt")
def test_sudoers_ist_gueltig(tmp_path: Path) -> None:
    aus = _render(tmp_path, SERVICE_USER="ft8", SERVICE_GROUP="ft8")
    r = subprocess.run(["visudo", "-c", "-f", str(aus / "sudoers.d" / "ft8-self-update")],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
