"""Die PSK-Liste darf nach einem Neustart nicht leer sein.

``psk_heard_us`` sagt, wer uns laut PSK Reporter zuletzt gehoert hat. Der
Abruf laeuft alle 15 Minuten, der erste erst rund zwei Minuten nach dem
Start — bis dahin war die Liste leer.

Das ist kein harmloser Zustand: Das Schwach-Gate verlangt fuer Ziele unter
-13 dB einen Empfangsbeleg und verwirft ohne Liste *jedes* davon; die Tiers
psk_heard_us, psk_snr, marine_psk und new_dxcc_psk liefern null. Bei einem
Self-Update-Rhythmus von zehn Minuten faellt dieses Fenster regelmaessig an.

Die Liste beschreibt, wer uns in den letzten 24 Stunden gehoert hat — eine
kurz zurueckliegende Kopie ueberbrueckt die Luecke und kostet PSK Reporter
nichts. Genau deshalb wird sie zwischengespeichert
statt beim Start neu geholt.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from ft8_appliance.runtime import orchestrator as orch_mod


def _orch(tmp_path: Path, mode: str = "FT8"):
    from ft8_appliance.statemachine.machine import StateMachine
    from ft8_appliance.statemachine.states import MachineContext

    class _Op:
        pass

    class _Operating:
        def __init__(self, m): self.mode = m

    class _Cfg:
        def __init__(self, m): self.operating = _Operating(m)

    class _O:
        config = _Cfg(mode)
        state_machine = StateMachine(ctx=MachineContext(callsign="DK9XR", my_grid="JN58"))
        _psk_heard_us_cache: set = set()
        _psk_cache_path = tmp_path / "psk_heard_cache.json"
        _persist_psk_cache = orch_mod.Orchestrator._persist_psk_cache
        _restore_psk_cache = orch_mod.Orchestrator._restore_psk_cache
    o = _O()
    o._psk_heard_us_cache = set()
    return o


def test_geschriebene_liste_kommt_zurueck(tmp_path):
    o = _orch(tmp_path)
    o._persist_psk_cache({"W1AW", "JA1XYZ"}, "FT8")

    neu = _orch(tmp_path)
    neu._restore_psk_cache()

    assert neu._psk_heard_us_cache == {"W1AW", "JA1XYZ"}
    assert neu.state_machine.ctx.psk_heard_us == {"W1AW", "JA1XYZ"}


def test_alte_liste_wird_verworfen(tmp_path):
    o = _orch(tmp_path)
    o._psk_cache_path.write_text(json.dumps({
        "ts": time.time() - 3 * 3600, "mode": "FT8", "calls": ["W1AW"],
    }))

    o._restore_psk_cache()

    assert o._psk_heard_us_cache == set()


def test_andere_betriebsart_wird_nicht_uebernommen(tmp_path):
    """Wer uns auf FT4 hoert, sagt nichts ueber FT8 — und umgekehrt."""
    o = _orch(tmp_path, mode="FT8")
    o._psk_cache_path.write_text(json.dumps({
        "ts": time.time(), "mode": "FT4", "calls": ["W1AW"],
    }))

    o._restore_psk_cache()

    assert o._psk_heard_us_cache == set()


def test_fehlende_datei_ist_kein_fehler(tmp_path):
    o = _orch(tmp_path)

    o._restore_psk_cache()          # darf nicht werfen

    assert o._psk_heard_us_cache == set()


def test_beschaedigte_datei_ist_kein_fehler(tmp_path):
    o = _orch(tmp_path)
    o._psk_cache_path.write_text("{kein json")

    o._restore_psk_cache()

    assert o._psk_heard_us_cache == set()


def test_leere_liste_wird_nicht_geschrieben(tmp_path):
    """Sonst ueberschriebe ein fehlgeschlagener Abruf den guten Stand."""
    o = _orch(tmp_path)
    o._persist_psk_cache({"W1AW"}, "FT8")
    o._persist_psk_cache(set(), "FT8")

    neu = _orch(tmp_path)
    neu._restore_psk_cache()

    assert neu._psk_heard_us_cache == {"W1AW"}


def test_verfall_passt_zum_abruffenster():
    """Der Abruf fragt who_heard_me(..., hours=1). Eine Kopie darf nicht
    deutlich aelter werden als die frischeste Information darin — die
    Grenze stand zunaechst bei sechs Stunden, begruendet mit einem
    24-Stunden-Fenster, das es nie gab."""
    import inspect
    from ft8_appliance.runtime import orchestrator as orch_mod
    from ft8_appliance.integrations import psk_reporter

    abruf = inspect.getsource(orch_mod.Orchestrator._psk_reciprocity_refresh_loop)
    assert "hours=1" in abruf, "Abruffenster hat sich geaendert — Grenze pruefen"

    verfall = inspect.getsource(orch_mod.Orchestrator._restore_psk_cache)
    assert "2 * 3600" in verfall
    assert "6 * 3600" not in verfall
