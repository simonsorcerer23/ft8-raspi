"""Die Picker-Filterzaehler ueberleben einen Neustart — tageweise.

Sie lebten nur im Arbeitsspeicher, und der Dienst startet oft neu: am
2026-09-11 dreiundzwanzigmal, weil das Self-Update alle zehn Minuten
prueft. Die Zaehler liefen dadurch im Mittel keine Stunde, bevor sie auf
null gingen; im Status standen typisch drei Dutzend Verwerfungen.

Das entwertete genau das Instrument, das zeigen soll, was der Picker
wegwirft. Die Merkregel dazu lautet, unter fuenfzig Faellen keine
Schluesse zu ziehen — diese Schwelle wurde so gut wie nie erreicht.

Tageswerte, nicht Gesamtwerte: Ein Zaehler, der ueber Wochen hochlaeuft,
zeigt keine Veraenderung mehr.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from ft8_appliance.runtime import orchestrator as orch_mod


def _o(tmp_path, vorhandene=None):
    class _SM:
        filter_drops = dict(vorhandene or {})

    class _O:
        state_machine = _SM()
        _filter_drops_path = tmp_path / "filter_drops.json"
        _filter_drops_tag = ""
        _filter_drops_letzte_sicherung = 0.0
        _restore_filter_drops = orch_mod.Orchestrator._restore_filter_drops
        _persist_filter_drops = orch_mod.Orchestrator._persist_filter_drops

    return _O()


def _heute() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def test_zaehler_von_heute_kommen_zurueck(tmp_path):
    (tmp_path / "filter_drops.json").write_text(json.dumps(
        {"tag": _heute(), "stufen": {"cooldown": 120, "slot_paritaet": 30}}
    ))
    o = _o(tmp_path)

    o._restore_filter_drops()

    assert o.state_machine.filter_drops == {"cooldown": 120, "slot_paritaet": 30}


def test_zaehler_von_gestern_bleiben_liegen(tmp_path):
    """Sonst vermischen sich die Tage und man sieht keine Veraenderung."""
    (tmp_path / "filter_drops.json").write_text(json.dumps(
        {"tag": "2020-01-01", "stufen": {"cooldown": 999}}
    ))
    o = _o(tmp_path)

    o._restore_filter_drops()

    assert o.state_machine.filter_drops == {}


def test_ohne_datei_faengt_es_bei_null_an(tmp_path):
    o = _o(tmp_path)

    o._restore_filter_drops()

    assert o.state_machine.filter_drops == {}


def test_kaputte_datei_bricht_den_start_nicht(tmp_path):
    (tmp_path / "filter_drops.json").write_text("{kein json")
    o = _o(tmp_path)

    o._restore_filter_drops()

    assert o.state_machine.filter_drops == {}


def test_schreiben_und_wieder_lesen(tmp_path):
    o = _o(tmp_path, {"cooldown": 7, "fernziel_allein": 2})
    o._filter_drops_tag = _heute()

    o._persist_filter_drops()

    zweiter = _o(tmp_path)
    zweiter._restore_filter_drops()
    assert zweiter.state_machine.filter_drops == {"cooldown": 7, "fernziel_allein": 2}


def test_schreiben_wird_gedrosselt(tmp_path):
    """Einmal je Minute reicht — der Pfad laeuft in jedem Slot."""
    import time as _t

    o = _o(tmp_path, {"cooldown": 1})
    o._filter_drops_tag = _heute()
    o._persist_filter_drops()
    o.state_machine.filter_drops["cooldown"] = 99
    o._filter_drops_letzte_sicherung = _t.monotonic()

    o._persist_filter_drops()

    geschrieben = json.loads((tmp_path / "filter_drops.json").read_text())
    assert geschrieben["stufen"]["cooldown"] == 1, "der zweite Schreibvorgang entfaellt"
