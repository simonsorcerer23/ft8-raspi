"""Der Stehwellenverlauf wird aufgeschrieben.

Die Tabelle ``swr_log`` gab es seit jeher und war nie beschrieben worden —
auf der Station standen dort nach Monaten Betrieb null Zeilen.

Ohne Verlauf faellt eine Verschlechterung der Antenne nicht auf: Ein
Stecker, der ueber Wochen korrodiert, oder Wasser im Kabel aendern das
Stehwellenverhaeltnis langsam. Der Schutz greift erst beim Grenzwert; bis
dahin sieht jeder Einzelwert fuer sich unauffaellig aus.

Zwei Bedingungen entscheiden ueber die Brauchbarkeit der Daten:

* **Nur waehrend eines eigenen Bursts.** Zwischen den Aussendungen faellt
  der gemessene Wert auf den Empfangs-Vorgabewert 1,0 zurueck. Eine
  Historie aus solchen Werten waere gelogen.
* **Sparsam.** Der Rig-Poll laeuft jede Sekunde — ungefiltert waeren das
  Zehntausende Zeilen am Tag fuer eine Groesse, die sich kaum bewegt.
"""

from __future__ import annotations

import inspect

from ft8_appliance.runtime import orchestrator as orch_mod


class _Rig:
    freq_hz = 14_074_000


def _o(*, burst=True, letzter=None, letzte_zeit=0.0):
    class _O:
        _tx_burst_active = burst
        db_enabled = True
        _swr_log_wert = letzter
        _swr_log_at = letzte_zeit
        _last_rig = _Rig()
        state_machine = type("S", (), {"ctx": type("C", (), {"band": "20m"})()})()
        geschrieben: list = []

        def _current_band(self):
            return "20m"

        async def _persist_swr(self, band, freq_hz, swr):
            self.geschrieben.append((band, freq_hz, swr))

        _buche_swr = orch_mod.Orchestrator._buche_swr

    o = _O()
    o.geschrieben = []
    return o


def _lauf(o, swr):
    """_buche_swr aufrufen und die erzeugte Task synchron einsammeln."""
    import asyncio

    aufgaben = []

    class _FakeTask:
        def __init__(self, coro):
            aufgaben.append(coro)

    echt = orch_mod.asyncio.create_task
    orch_mod.asyncio.create_task = _FakeTask
    try:
        o._buche_swr(swr)
    finally:
        orch_mod.asyncio.create_task = echt
    for coro in aufgaben:
        asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)
    return len(aufgaben)


def test_erster_wert_wird_geschrieben():
    o = _o()
    assert _lauf(o, 1.3) == 1
    assert o.geschrieben == [("20m", 14_074_000, 1.3)]


def test_zwischen_den_bursts_wird_nichts_geschrieben():
    """Dort faellt der gemessene Wert auf 1,0 zurueck — das waere gelogen."""
    o = _o(burst=False)
    assert _lauf(o, 1.0) == 0


def test_unveraenderter_wert_wird_nicht_wiederholt():
    o = _o(letzter=1.30, letzte_zeit=orch_mod.time.monotonic())
    assert _lauf(o, 1.32) == 0


def test_merkliche_aenderung_wird_geschrieben():
    o = _o(letzter=1.30, letzte_zeit=orch_mod.time.monotonic())
    assert _lauf(o, 1.9) == 1


def test_grundlinie_auch_ohne_aenderung():
    """Sonst fehlte bei stabiler Antenne jeder Beleg dafuer, dass gemessen
    wurde — und eine Luecke saehe aus wie ein guter Wert."""
    o = _o(letzter=1.30, letzte_zeit=orch_mod.time.monotonic() - 601)
    assert _lauf(o, 1.30) == 1


def test_ohne_frequenz_kein_eintrag():
    o = _o()
    o._last_rig = type("R", (), {"freq_hz": None})()
    assert _lauf(o, 1.3) == 0


def test_schreibfehler_kostet_nicht_den_sendebetrieb():
    q = inspect.getsource(orch_mod.Orchestrator._persist_swr)
    assert "except Exception" in q


def test_die_messung_haengt_am_swr_check():
    """Nur dort ist die Settling-Periode nach dem Tasten schon abgewartet."""
    q = inspect.getsource(orch_mod.Orchestrator._check_swr_warn)
    assert "_buche_swr" in q
    vor_settling = q.split("SWR_SETTLING_S", 1)[0]
    assert "_buche_swr" not in vor_settling, "erst nach der Settling-Pruefung"
