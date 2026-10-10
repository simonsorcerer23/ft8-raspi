"""Das Rig wird abends ausgeschaltet und morgens wieder ein.

Am 02.10.2026 fiel das IC-7300 aus; am 10.10. hing das Ersatzgeraet dran,
und die Station kam nicht von selbst zurueck: rigctld hatte sich sauber
beendet und wurde nicht neu gestartet, und der Empfang war seit einem
Dienststart ohne Soundkarte fuer immer abgeschaltet.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from ft8_appliance.audio import alsa_io
from ft8_appliance.runtime import production

WURZEL = Path(__file__).parents[2]


def test_rigctld_wird_immer_neu_gestartet() -> None:
    for f in ("deploy/systemd/ft8-rigctld.service.in", "deploy/systemd/ft8-rigctld.service"):
        s = (WURZEL / f).read_text()
        assert "\nRestart=always\n" in s, f
        assert "\nStartLimitIntervalSec=0\n" in s, f


def test_geraetesuche_findet_die_karte_sobald_sie_da_ist(monkeypatch) -> None:
    da = {"name": None}
    monkeypatch.setattr(production, "_resolve_capture_device",
                        lambda hint, still=False: da["name"])
    suche = production._GeraeteSuche("")
    suche._HALTBAR_S = 0.0
    assert suche() is None
    da["name"] = "plughw:CARD=CODEC,DEV=0"
    assert suche() == "plughw:CARD=CODEC,DEV=0"


def test_dienststart_ohne_soundkarte_schaltet_den_empfang_nicht_ab(monkeypatch) -> None:
    """Vorher: 'decode source disabled (noop)' — und dabei blieb es."""
    monkeypatch.setattr(production, "_resolve_capture_device", lambda hint, still=False: None)
    monkeypatch.setattr(alsa_io, "_ALSA_AVAILABLE", True)
    gestartet = []
    monkeypatch.setattr(alsa_io.AlsaCapture, "start", lambda self: gestartet.append(self))
    config = SimpleNamespace(
        demo_mode=False, bands=[SimpleNamespace(name="20m")],
        rig=SimpleNamespace(audio_card_hint=""),
        operating=SimpleNamespace(mode="FT8", decoder_mode="standard", auto_notch_enabled=False),
    )
    quelle = production._build_decode_source(config)
    assert quelle is not production._noop_decode_source
    assert len(gestartet) == 1 and callable(gestartet[0].device)
    assert production._build_playback(config) is not None


def test_aufnahme_wartet_auf_die_karte_und_oeffnet_sie_dann(monkeypatch) -> None:
    geoeffnet = threading.Event()
    da = {"name": None}

    class _Pcm:
        def __init__(self, **kw):
            assert kw["device"] == "plughw:CARD=CODEC,DEV=0"
            geoeffnet.set()

        def read(self):
            time.sleep(0.01)
            return 0, b""

        def close(self):
            pass

    monkeypatch.setattr(alsa_io, "_ALSA_AVAILABLE", True)
    monkeypatch.setattr(alsa_io, "alsaaudio", SimpleNamespace(
        PCM=_Pcm, PCM_CAPTURE=0, PCM_NORMAL=0, PCM_FORMAT_S16_LE=0), raising=False)
    aufnahme = alsa_io.AlsaCapture(sink=lambda d, t: None, device=lambda: da["name"])
    monkeypatch.setattr(aufnahme._stop, "wait", lambda s: time.sleep(0.02))
    aufnahme.start()
    try:
        time.sleep(0.1)
        assert not geoeffnet.is_set()          # Rig aus: nichts zu oeffnen
        da["name"] = "plughw:CARD=CODEC,DEV=0"  # Rig wird eingeschaltet
        assert geoeffnet.wait(2.0)
    finally:
        aufnahme._stop.set()
        aufnahme._thread.join(timeout=2.0)


def test_senden_ohne_soundkarte_scheitert_laut() -> None:
    with pytest.raises(RuntimeError, match="keine Soundkarte"):
        alsa_io._geraet(lambda: None)
    assert alsa_io._geraet("default") == "default"


def _waechter(tmp_path, geraet: Path, attrappe: str):
    import subprocess
    rigctld = tmp_path / "rigctld"
    rigctld.write_text("#!/bin/sh\n" + attrappe + "\n")
    rigctld.chmod(0o755)
    return subprocess.Popen(
        ["/bin/sh", str(WURZEL / "deploy/scripts/rigctld-waechter.sh")],
        env={"RIGCTLD": str(rigctld), "RIG_MODEL": "1", "RIG_DEVICE": str(geraet),
             "RIG_BAUD": "4800", "RIG_PTT_ARGS": "", "PATH": "/usr/bin:/bin"},
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )


def _warte_auf(bedingung, sekunden: float = 10.0) -> bool:
    ende = time.monotonic() + sekunden
    while time.monotonic() < ende:
        if bedingung():
            return True
        time.sleep(0.1)
    return False


def test_waechter_faehrt_rigctld_mit_dem_rig_hoch_und_runter(tmp_path) -> None:
    """Abends aus, morgens an: Am 10.10.2026 lief rigctld nach dem Ausschalten
    mit toter Verbindung weiter, und am 02.10. hatte es sich beendet, ohne je
    neu gestartet zu werden."""
    geraet = tmp_path / "ttyUSB0"
    starts = tmp_path / "starts"
    p = _waechter(tmp_path, geraet, f"echo x >> {starts}; exec sleep 60")
    try:
        time.sleep(1.0)
        assert p.poll() is None and not starts.exists()        # Rig aus: wartet still
        geraet.write_text("")                                   # Rig an
        assert _warte_auf(starts.exists)
        geraet.unlink()                                         # Rig aus
        time.sleep(5.0)
        assert p.poll() is None                                 # Waechter bleibt, rigctld ist weg
        geraet.write_text("")                                   # Rig wieder an
        assert _warte_auf(lambda: starts.read_text().count("x") == 2)
    finally:
        p.terminate()
        assert p.wait(timeout=5) == 0
    aus = p.stdout.read()
    assert "warte" in aus and "verschwunden" in aus and "wieder am USB" in aus


def test_waechter_meldet_wenn_rigctld_von_selbst_stirbt(tmp_path) -> None:
    geraet = tmp_path / "ttyUSB0"
    geraet.write_text("")
    p = _waechter(tmp_path, geraet, "exit 3")
    assert p.wait(timeout=10) == 3        # systemd startet neu (Restart=always)
