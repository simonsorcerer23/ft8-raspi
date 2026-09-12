"""Der QRZ-Logbuchabgleich haelt seinen 24-Stunden-Takt ein.

Er holt das komplette Logbuch des Operators — ueber 8000 Datensaetze —
und fuellt daraus die "schon gearbeitet"-Mengen des Pickers. Einmal am Tag
ist dafuer reichlich: Es kommen wenige Eintraege je Tag dazu.

Bis 2026-09-12 ueberlebte der Zeitpunkt des letzten Laufs den Neustart
nicht. Die Schleife begann stattdessen sechzig Sekunden nach jedem Start —
und weil die Station im Mittel alle gut siebzig Minuten neu startet,
lief der Abgleich **81-mal in 48 Stunden statt zweimal**. Gemessen am
12.9.: drei Laeufe in 45 Minuten, je 8387, 8390 und 8390 Datensaetze.
Geaendert hatten sich vier.

Das ist erhebliche Last fuer einen fremden Dienst, und sie war
unbeabsichtigt — dieselbe Fehlerklasse wie beim Strict-Modus und beim
Fehlschlag-Cooldown, nur mit umgekehrtem Vorzeichen: nicht "wirkt nie",
sondern "wirkt viel zu oft".
"""

from __future__ import annotations

import inspect

from ft8_appliance.runtime import orchestrator as orch_mod


def test_zeitpunkt_wird_gesichert():
    q = inspect.getsource(orch_mod.Orchestrator._qrz_logbook_sync_loop)
    assert "self._qrz_sync_at = time.time()" in q
    assert "_maybe_persist_runtime_state" in q


def test_zeitpunkt_landet_in_runtime_state():
    q = inspect.getsource(orch_mod.Orchestrator._maybe_persist_runtime_state)
    assert "qrz_sync_at" in q


def test_zeitpunkt_wird_beim_start_gelesen():
    q = inspect.getsource(orch_mod.Orchestrator._load_runtime_state)
    assert "qrz_sync_at" in q


def test_nach_neustart_wird_gewartet():
    """Der Kern: Liegt der letzte Lauf noch keine 24 Stunden zurueck, wird
    die Restzeit abgewartet statt sofort erneut zu holen."""
    q = inspect.getsource(orch_mod.Orchestrator._qrz_logbook_sync_loop)
    assert "_qrz_sync_abstand_s" in q
    assert "rest > 0" in q


def test_erster_lauf_wartet_nicht():
    """Ohne gesicherten Zeitpunkt (frische Installation) soll sofort
    geholt werden — sonst stuende der Picker einen Tag ohne Historie da."""
    q = inspect.getsource(orch_mod.Orchestrator._qrz_logbook_sync_loop)
    assert "self._qrz_sync_at > 0" in q


def test_abstand_ist_ein_tag():
    assert orch_mod.Orchestrator._qrz_sync_abstand_s == 86400.0
