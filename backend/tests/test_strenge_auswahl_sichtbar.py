"""Die strenge Auswahl arbeitete ohne jede Spur.

Sie schaltet nach einer Pechstraehne auf strengere Kriterien um — aber weder
Log noch Status sagten, ob das je passiert ist. Bei rund 20 % Abschlussquote
muesste die Bedingung (weniger als zwei Erfolge in zwanzig Anrufen)
rechnerisch in etwa jedem vierzehnten Fenster erfuellt sein; nachweisen liess
es sich nicht. Ein Mechanismus, dessen Wirkung niemand sehen kann, laesst
sich auch nicht bewerten.
"""

from __future__ import annotations

from datetime import UTC, datetime

from ft8_appliance.statemachine import machine as maschinen_modul
from ft8_appliance.statemachine.machine import StateMachine


def _maschine(monkeypatch):
    """Minimal-Aufbau: nur was _record_hunt_outcome anfasst."""
    from types import SimpleNamespace
    ctx = SimpleNamespace(
        hunt_attempt_meta={"K1ABC": {}},
        failed_attempt_counts={},
        hunt_recent_outcomes=[],
        hunt_poor_run_window=20,
        hunt_poor_run_min_successes=2,
        hunt_poor_run_strict_s=600,
        hunt_strict_until=0.0,
    )
    return SimpleNamespace(ctx=ctx)


class _Mitschnitt:
    """Faengt log.info ab. caplog haengt an der globalen Logger-Konfiguration
    und faellt aus, sobald ein anderer Test sie verstellt."""

    def __init__(self, monkeypatch):
        self.zeilen: list[str] = []
        echt = maschinen_modul.log.info

        def fake(msg, *args, **kw):
            try:
                self.zeilen.append(msg % args if args else msg)
            except Exception:
                self.zeilen.append(str(msg))
            return echt(msg, *args, **kw)

        monkeypatch.setattr(maschinen_modul.log, "info", fake)

    def enthaelt(self, text: str) -> bool:
        return any(text in z for z in self.zeilen)


def _melde(m, erfolge: int, gesamt: int) -> None:
    for i in range(gesamt):
        StateMachine._record_hunt_outcome(m, "K1ABC", completed=i < erfolge)


def test_pechstraehne_schaltet_um_und_sagt_es(monkeypatch):
    m = _maschine(monkeypatch)
    mit = _Mitschnitt(monkeypatch)
    _melde(m, erfolge=1, gesamt=20)
    assert m.ctx.hunt_strict_until > datetime.now(UTC).timestamp()
    assert mit.enthaelt("Strenge Auswahl"), \
        "das Umschalten muss im Log stehen, sonst ist es nicht bewertbar"


def test_gute_serie_schaltet_nicht_um(monkeypatch):
    m = _maschine(monkeypatch)
    mit = _Mitschnitt(monkeypatch)
    _melde(m, erfolge=5, gesamt=20)
    assert m.ctx.hunt_strict_until == 0.0
    assert not mit.enthaelt("Strenge Auswahl")


def test_meldet_nicht_bei_jedem_weiteren_anruf(monkeypatch):
    """Steht die strenge Auswahl schon, darf nicht jeder Anruf erneut melden —
    sonst ersaeuft das Log in einer langen Pechstraehne."""
    m = _maschine(monkeypatch)
    _melde(m, erfolge=0, gesamt=20)
    mit = _Mitschnitt(monkeypatch)
    StateMachine._record_hunt_outcome(m, "K1ABC", completed=False)
    assert not mit.enthaelt("Strenge Auswahl"), \
        "die Frist lief noch, also gab es nichts Neues zu melden"


def test_fenster_muss_voll_sein(monkeypatch):
    """Neunzehn Anrufe reichen nicht — sonst schlaegt sie nach einem
    Neustart sofort zu, bevor ueberhaupt Daten da sind."""
    m = _maschine(monkeypatch)
    _melde(m, erfolge=0, gesamt=19)
    assert m.ctx.hunt_strict_until == 0.0


def test_unbekannter_call_zaehlt_nicht(monkeypatch):
    """Nur selbst gewaehlte Ziele zaehlen; eingehende Anrufe haben eine
    andere Erfolgslage und wuerden das Fenster verfaelschen."""
    m = _maschine(monkeypatch)
    for _ in range(25):
        StateMachine._record_hunt_outcome(m, "DL9XYZ", completed=False)
    assert m.ctx.hunt_recent_outcomes == []
    assert m.ctx.hunt_strict_until == 0.0
