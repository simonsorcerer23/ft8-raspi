"""Yaesu FT-817/818 am Digirig Mobile — vorbereitet ohne Geraet (2026-09-14).

Fakten aus hamlib 4.6.2 (rigctl -l / --dump-caps) und digirig.net; was
hier geprueft wird, ist die Umsetzung dieser Fakten, nicht das Geraet.
"""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from ft8_appliance.config import RigConfig
from ft8_appliance.config.models import RIG_COMPUTED_FIELDS, RIG_MODELS
from ft8_appliance.rig import detect_rigs, render_rigctld_envfile
from ft8_appliance.runtime.orchestrator import Orchestrator

WURZEL = Path(__file__).resolve().parents[2]
DIGIRIG = "usb-Silicon_Labs_CP2102_USB_to_UART_Bridge_Controller_0001-if00-port0"
IC7300 = "usb-Silicon_Labs_CP2102_USB_to_UART_Bridge_Controller_IC-7300_03026783-if00-port0"


# ------------------------------------------------------------------ Profil

def test_ft817_profil_aus_hamlib() -> None:
    r = RigConfig(model="ft817", serial_device=f"/dev/serial/by-id/{DIGIRIG}")
    assert r.hamlib_id == 1020 and r.effective_max_power_w == 5
    assert r.effective_cat_baud == 4800 and r.effective_ptt_type == "rts"
    assert not r.profil.power_settable and not r.profil.bandwidth_settable


def test_ft818_ist_eigenes_hamlib_modell() -> None:
    assert RigConfig(model="ft818").hamlib_id == 1041


def test_icom_profil_unveraendert() -> None:
    r = RigConfig(model="ic7300")
    assert (r.hamlib_id, r.effective_cat_baud, r.effective_ptt_type) == (3073, 19200, "cat")
    assert r.profil.power_settable and r.profil.mode_width_hz == 2700


def test_explizite_werte_schlagen_das_profil() -> None:
    r = RigConfig(model="ft817", cat_baud=38400, ptt_type="cat")
    assert r.effective_cat_baud == 38400 and r.effective_ptt_type == "cat"


def test_computed_felder_werden_beim_speichern_ausgeschlossen() -> None:
    """extra=forbid: ein computed-Feld in der YAML macht die Konfig unlesbar."""
    r = RigConfig(model="ft817")
    computed = set(r.model_dump()) - set(r.model_dump(exclude=set(RIG_COMPUTED_FIELDS)))
    assert computed == set(RIG_COMPUTED_FIELDS)
    RigConfig(**r.model_dump(exclude=set(RIG_COMPUTED_FIELDS)))


# ------------------------------------------------------------------ rigctld

def test_envfile_traegt_ptt_ueber_rts() -> None:
    dev = f"/dev/serial/by-id/{DIGIRIG}"
    text = render_rigctld_envfile(RigConfig(model="ft817", serial_device=dev))
    assert "RIG_MODEL=1020\n" in text and "RIG_BAUD=4800\n" in text
    assert f"RIG_PTT_ARGS=--ptt-type=RTS --ptt-file={dev}\n" in text


def test_envfile_ohne_ptt_args_bei_cat() -> None:
    assert "RIG_PTT_ARGS=\n" in render_rigctld_envfile(RigConfig(model="ic7300"))


def test_unit_setzt_ptt_args_ungequotet_ein() -> None:
    for f in ("deploy/systemd/ft8-rigctld.service.in", "deploy/systemd/ft8-rigctld.service"):
        s = (WURZEL / f).read_text()
        assert re.search(r"^\s+\$RIG_PTT_ARGS \\$", s, re.M), f


def test_self_update_gleicht_das_envfile_ab() -> None:
    s = (WURZEL / "scripts" / "self-update.sh").read_text()
    assert s.count("sync_rigctld_envfile\n") >= 3      # latest-Pfad + beide Sync-Zweige
    assert s.index("sync_rigctld_envfile() {") < s.index("    sync_rigctld_envfile\n")
    sud = (WURZEL / "deploy" / "sudoers.d" / "ft8-self-update.in").read_text()
    assert "/etc/default/ft8-rigctld" in sud and "systemctl restart ft8-rigctld.service" in sud


def test_installer_laesst_gpsd_nicht_an_usb_ports() -> None:
    """Digirig-Doku: gpsd mit USBAUTO greift den CP2102 und wackelt an RTS
    — RTS ist die PTT. Mit gepinntem DEVICES ist USBAUTO ohnehin unnoetig."""
    s = (WURZEL / "deploy" / "install.sh").read_text()
    assert 'USBAUTO="false"' in s and 'USBAUTO="true"' not in s


# ------------------------------------------------------------------ Erkennung

def test_ic7300_kabel_bleibt_sicher_erkannt(tmp_path: Path) -> None:
    (tmp_path / IC7300).touch()
    d = detect_rigs(by_id_dir=tmp_path)
    assert [(x.model, x.confidence) for x in d] == [("ic7300", "high")]


def test_digirig_wird_als_yaesu_kandidat_gemeldet(tmp_path: Path) -> None:
    (tmp_path / DIGIRIG).touch()
    d = detect_rigs(by_id_dir=tmp_path)
    assert [(x.model, x.confidence) for x in d] == [("ft817", "low")]
    assert "Digirig" in d[0].description


def test_beide_zugleich_icom_zuerst(tmp_path: Path) -> None:
    (tmp_path / DIGIRIG).touch(); (tmp_path / IC7300).touch()
    assert [x.model for x in detect_rigs(by_id_dir=tmp_path)] == ["ic7300", "ft817"]


# ------------------------------------------------------------ Orchestrator

@pytest.mark.asyncio
async def test_leistung_wird_bei_ft817_nicht_gesetzt() -> None:
    aufrufe: list[float] = []

    class _Rig:
        async def set_rfpower(self, norm):
            aufrufe.append(norm)
    o = SimpleNamespace(
        config=SimpleNamespace(rig=RigConfig(model="ft817")),
        rig=_Rig(), _tx_power_w=5,
        _legal_max_power_w=lambda: 5,
        _register_app_command=lambda *a, **k: None,
    )
    await Orchestrator.handle_tx_power(o, 2)
    assert aufrufe == [] and o._tx_power_w == 5


def test_poll_meldet_bei_ft817_keinen_fremdeingriff() -> None:
    """Der Poll-Block muss vor dem Tamper-Vergleich das Profil fragen."""
    s = (WURZEL / "backend" / "ft8_appliance" / "runtime" / "orchestrator.py").read_text()
    i = s.index("if not _rig_profil_von(self.config).power_settable:\n                    # Leistung wird am Geraet gewaehlt")
    assert i < s.index('elif abs(rig_watts - self._tx_power_w) >= max(1, max_w // 20):')
    assert "_notify_power_over_cap(rig_watts, legal)" in s
    assert "_rig_profil_von(self.config).bandwidth_settable and rig_bw is not None" in s


def test_restore_nimmt_modus_und_breite_aus_dem_profil() -> None:
    s = (WURZEL / "backend" / "ft8_appliance" / "runtime" / "orchestrator.py").read_text()
    assert 'handle_set_mode("PKTUSB", 2700)' not in s
    assert "handle_set_mode(_pf.digital_mode, _pf.mode_width_hz)" in s


# -------------------------------------------------------- Frontend-Paritaet

def test_frontend_listen_kennen_jedes_backend_modell() -> None:
    """Drei Listen im Frontend, eine Tabelle im Backend — dieser Test ist
    die Klammer, bis das Frontend die Liste vom Backend holt."""
    fe = WURZEL / "frontend" / "src" / "components"
    for f, muster in (("ControlPanel.svelte", r"const RIG_MAX_W = \{([^}]*)\}"),
                      ("RigPanel.svelte", r"const MODEL_LABELS = \{([^}]*)\}"),
                      ("RigPanel.svelte", r"const MODEL_MAX_W = \{([^}]*)\}"),
                      ("FirstBootWizard.svelte", r"const RIG_DEFAULTS = \{(.*?)\n  \};")):
        block = re.search(muster, (fe / f).read_text(), re.S)
        assert block, (f, muster)
        keys = set(re.findall(r"(?:^|[{,\s])([a-z0-9_]+)\s*:", block.group(1), re.M))
        assert keys >= set(RIG_MODELS), (f, set(RIG_MODELS) - keys)
