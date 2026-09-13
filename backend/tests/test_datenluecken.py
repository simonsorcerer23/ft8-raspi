"""Die vier Luecken aus der Datenpruefung vom 2026-09-13.

Alle vier hatten dieselbe Eigenschaft: Sie haben nie einen Fehler erzeugt.
Eine Tabelle blieb leer, eine Spalte trug immer denselben Wert, ein
Rufzeichen stand in zwei Schreibweisen, und ein Ausgang blieb falsch
stehen. Aufgefallen sind sie erst, als jemand gezielt nachgesehen hat.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ft8_appliance.util.config_snapshot import MASKE, ist_geheim, maskiere


# --------------------------------------------------- 1. Konfigurationsstand

def test_zugangsdaten_werden_maskiert() -> None:
    """Die Datenbank wird gesichert und kopiert — Geheimnisse gehoeren da nicht hin."""
    # Werte bewusst so gewaehlt, dass keiner als Teilstueck in einem
    # Schluesselnamen vorkommt — sonst schlaegt die Suche unten falsch an.
    werte = {
        "qrz_password": "ZUGANG-QRZ-1",
        "qrz_logbook_api_key": "ZUGANG-QRZ-2",
        "clublog_app_password": "ZUGANG-CL-1",
        "clublog_api_key": "ZUGANG-CL-2",
        "api_token": "ZUGANG-UI-1",
        "ntfy_action_token": "ZUGANG-NTFY-1",
        "psk": "ZUGANG-WLAN-1",
    }
    roh = {
        "operators": [{
            "callsign": "DK9XR",
            "qrz_password": werte["qrz_password"],
            "qrz_logbook_api_key": werte["qrz_logbook_api_key"],
            "clublog_app_password": werte["clublog_app_password"],
            "clublog_api_key": werte["clublog_api_key"],
        }],
        "api_token": werte["api_token"],
        "ntfy_action_token": werte["ntfy_action_token"],
        "hotspot": {"psk": werte["psk"]},
    }
    aus = maskiere(roh)
    flach = repr(aus)
    for geheim in werte.values():
        assert geheim not in flach, f"{geheim!r} steht im Schnappschuss"
    assert aus["operators"][0]["callsign"] == "DK9XR", "Rufzeichen ist kein Geheimnis"
    assert aus["operators"][0]["qrz_password"] == MASKE


def test_schalter_bleiben_lesbar() -> None:
    """Ohne Typpruefung verschluckt das Namensmuster auch Schalter.

    ``hunt_weak_requires_psk`` ist ein bool und traf auf ``psk`` — genau
    solche Schalter will man in der Historie aber sehen.
    """
    aus = maskiere({
        "operating": {
            "hunt_weak_requires_psk": True,
            "hunt_strict_min_psk_snr_db": -10,
            "hunt_priority": ["psk_heard_us", "sole"],
        },
    })
    o = aus["operating"]
    assert o["hunt_weak_requires_psk"] is True
    assert o["hunt_strict_min_psk_snr_db"] == -10
    assert o["hunt_priority"] == ["psk_heard_us", "sole"]


def test_leere_werte_bleiben_leer() -> None:
    """Ein nicht gesetztes Passwort als *** zu zeigen waere eine Falschaussage."""
    assert maskiere({"qrz_password": None})["qrz_password"] is None
    assert maskiere({"qrz_password": ""})["qrz_password"] == ""


@pytest.mark.parametrize("name", [
    "qrz_password", "clublog_app_password", "api_token", "ntfy_action_token",
    "qrz_logbook_api_key", "clublog_api_key", "psk", "some_secret", "my_pwd",
])
def test_bekannte_geheimnisnamen(name: str) -> None:
    assert ist_geheim(name)


@pytest.mark.parametrize("name", ["callsign", "band", "hunt_priority", "grid", "mode"])
def test_harmlose_namen(name: str) -> None:
    assert not ist_geheim(name)


# ------------------------------------------------ 2. eingefrorene Messquelle

class _Stub:
    """Nur die Felder, die _s_meter_brauchbar anfasst."""
    _S_METER_TOT_AB = 20

    def __init__(self) -> None:
        self._s_meter_letzter = None
        self._s_meter_gleich = 0

    brauchbar = None  # wird unten gebunden


@pytest.fixture
def messquelle():
    from ft8_appliance.runtime.orchestrator import Orchestrator
    s = _Stub()
    s._S_METER_TOT_AB = Orchestrator._S_METER_TOT_AB
    s.brauchbar = Orchestrator._s_meter_brauchbar.__get__(s)
    return s


def test_bewegter_wert_wird_gespeichert(messquelle) -> None:
    for w in (-54, -52, -58, -51):
        assert messquelle.brauchbar(w) == w


def test_eingefrorener_wert_wird_verworfen(messquelle) -> None:
    """Bis 2026-09-13 stand in jeder der 1284 Zeilen exakt -54 dB."""
    grenze = messquelle._S_METER_TOT_AB
    for i in range(grenze - 1):
        assert messquelle.brauchbar(-54) == -54, f"zu frueh aufgegeben bei {i+1}"
    assert messquelle.brauchbar(-54) is None, "Quelle nicht als tot erkannt"
    for _ in range(5):
        assert messquelle.brauchbar(-54) is None


def test_quelle_erholt_sich(messquelle) -> None:
    """Ein Rig mit brauchbarem S-Meter darf nicht dauerhaft abgeschaltet werden."""
    for _ in range(messquelle._S_METER_TOT_AB + 3):
        messquelle.brauchbar(-54)
    assert messquelle.brauchbar(-49) == -49
    assert messquelle.brauchbar(-52) == -52


def test_schwelle_ist_nicht_eins(messquelle) -> None:
    """Zwei gleiche Messungen hintereinander sind voellig normal."""
    assert messquelle.brauchbar(-54) == -54
    assert messquelle.brauchbar(-54) == -54
    assert messquelle._S_METER_TOT_AB >= 10


# ------------------------------------------- 3. + 4. Rufzeichen und Ausgang

@pytest.fixture
async def db(tmp_path):
    """Frische Datenbank ueber die ORM-Modelle."""
    from ft8_appliance.db.session import create_all, init_engine
    init_engine(tmp_path / "t.sqlite")
    await create_all()
    yield


@pytest.mark.asyncio
async def test_volles_rufzeichen_wird_mitgeschrieben(db) -> None:
    """LA/DM2RM im Logbuch, DM2RM in der Telemetrie — ohne die zweite
    Spalte verliert jede Verknuepfung die portablen Stationen."""
    from ft8_appliance.db.models import PickAttempt
    from ft8_appliance.db.session import session_scope
    async with session_scope() as s:
        s.add(PickAttempt(
            ts=datetime.now(UTC), target_call="DM2RM", target_call_raw="LA/DM2RM",
            user_callsign="DK9XR", psk_heard_us=False, outcome="completed",
        ))
    async with session_scope() as s:
        from sqlalchemy import select
        z = (await s.execute(select(PickAttempt))).scalars().first()
    assert z.target_call == "DM2RM", "Basis-Call fuer Sperrfristen bleibt"
    assert z.target_call_raw == "LA/DM2RM", "volles Rufzeichen fehlt"


@pytest.mark.asyncio
async def test_spaet_doch_geklappt_wird_korrigiert(db) -> None:
    from sqlalchemy import select

    from ft8_appliance.db.models import PickAttempt
    from ft8_appliance.db.session import session_scope
    from ft8_appliance.runtime.orchestrator import Orchestrator

    jetzt = datetime.now(UTC)
    async with session_scope() as s:
        s.add(PickAttempt(
            ts=jetzt - timedelta(seconds=120), target_call="ON8BZ",
            user_callsign="DK9XR", psk_heard_us=False,
            outcome="bailed", bail_reason="went_silent",
        ))

    class _O:
        db_enabled = True
        _NACHSTEMPEL_FENSTER_S = Orchestrator._NACHSTEMPEL_FENSTER_S
    await Orchestrator._stemple_versuch_nach(_O(), "ON8BZ", jetzt)

    async with session_scope() as s:
        z = (await s.execute(select(PickAttempt))).scalars().first()
    assert z.outcome == "completed", "Versuch blieb auf gescheitert stehen"
    assert z.bail_reason is None
    assert z.nachgestempelt is True, "Korrektur ist nicht als solche erkennbar"


@pytest.mark.asyncio
async def test_dupe_anruf_nach_dem_qso_bleibt_gescheitert(db) -> None:
    """Wer nach einem laengst gefahrenen QSO erneut ruft, scheitert zu Recht.

    Genau diese Faelle hatten meine erste Zaehlung von 31 auf 4 gedrueckt:
    Ein Zeitfenster in beide Richtungen faengt auch Doppelanrufe.
    """
    from sqlalchemy import select

    from ft8_appliance.db.models import PickAttempt
    from ft8_appliance.db.session import session_scope
    from ft8_appliance.runtime.orchestrator import Orchestrator

    qso_zeit = datetime.now(UTC) - timedelta(minutes=10)
    async with session_scope() as s:
        s.add(PickAttempt(          # Anruf NACH dem QSO
            ts=qso_zeit + timedelta(minutes=5), target_call="EC5M",
            user_callsign="DK9XR", psk_heard_us=False,
            outcome="bailed", bail_reason="went_silent",
        ))

    class _O:
        db_enabled = True
        _NACHSTEMPEL_FENSTER_S = Orchestrator._NACHSTEMPEL_FENSTER_S
    await Orchestrator._stemple_versuch_nach(_O(), "EC5M", qso_zeit)

    async with session_scope() as s:
        z = (await s.execute(select(PickAttempt))).scalars().first()
    assert z.outcome == "bailed", "Dupe-Anruf faelschlich als Erfolg gebucht"
    assert z.nachgestempelt is None


@pytest.mark.asyncio
async def test_bereits_gebuchter_erfolg_wird_nicht_angefasst(db) -> None:
    from sqlalchemy import select

    from ft8_appliance.db.models import PickAttempt
    from ft8_appliance.db.session import session_scope
    from ft8_appliance.runtime.orchestrator import Orchestrator

    jetzt = datetime.now(UTC)
    async with session_scope() as s:
        s.add(PickAttempt(ts=jetzt - timedelta(seconds=300), target_call="G0ABC",
                          user_callsign="DK9XR", psk_heard_us=False,
                          outcome="bailed", bail_reason="went_silent"))
        s.add(PickAttempt(ts=jetzt - timedelta(seconds=60), target_call="G0ABC",
                          user_callsign="DK9XR", psk_heard_us=False,
                          outcome="completed"))

    class _O:
        db_enabled = True
        _NACHSTEMPEL_FENSTER_S = Orchestrator._NACHSTEMPEL_FENSTER_S
    await Orchestrator._stemple_versuch_nach(_O(), "G0ABC", jetzt)

    async with session_scope() as s:
        zeilen = list((await s.execute(select(PickAttempt).order_by(PickAttempt.ts))).scalars())
    assert [z.outcome for z in zeilen] == ["bailed", "completed"], \
        "der erste Abbruch war echt und darf nicht mitkorrigiert werden"
