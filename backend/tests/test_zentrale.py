"""Die Station meldet selbst an die Zentrale (seit 2026-11-25).

Zwei Dinge muessen stimmen:

* Was hinausgeht, ist Schluessel fuer Schluessel das, was der Spiegel bisher
  als ``live.json`` und ``profil.json`` schrieb — dk9xr.de liest es unveraendert.
* Faellt das Netz aus, geht nichts verloren: Die Station fragt die Zentrale
  nach ihrem Stand und schickt den Rest.
"""
from __future__ import annotations

import importlib.util
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from ft8_appliance.zentrale import aufbereitung
from ft8_appliance.zentrale.sender import Zentrale, ZentraleFehler, qso_zeile

WURZEL = Path(__file__).resolve().parents[2]

ANTWORTEN = {
    "/api/status": {"callsign": "DK9XR", "state": "CQ_CALLING", "mode": "FT8",
                    "auto_cq": False, "auto_answer": True,
                    "active_band": "20m", "tx_power_w": 70, "last_lock_code": None,
                    "worked_count": 6000, "rig": {"freq_hz": 14074000,
                                                  "swr": 1.0, "ptt": False},
                    "gps": {"lat": 48.7654321, "lon": 10.1234567}},
    "/api/stats": {"qso_today": 5, "dxccs_today": 3, "qso_7d": 40,
                   "qso_total": 400, "decodes_last_hour": 500,
                   "best_dx_today": {"call": "RC3DN", "grid": "KO95", "band": "20m",
                                     "distance_km_estimate": 2116}},
    "/api/map": {"operator_lat": 48.7654321, "operator_lon": 10.1234567,
                 "markers": [{"call": "UT0UE", "lat": 50.5, "lon": 31.0,
                              "kind": "worked", "band": "20m",
                              "last_worked": "2026-09-12T14:43:15"}]},
    "/api/psk/who-heard-me": {"reports": [
        {"rx_call": "M0VTS", "rx_grid": "IO82WS", "snr_db": -14, "band": "20m",
         "received_at": "2026-09-16T08:33:42+00:00", "flag": "\U0001F1EC\U0001F1E7"}]},
    "/api/psk/tagesprofil": {"tage": 7, "geheim": "x", "baender": [
        {"band": "20m", "empfaenger": 12, "intern": 1, "stunden": [
            {"h": 8, "tage": 3, "gesamt": 40, "kontinente": {"EU": 30},
             "laender": 5, "top": [["DL", 9], ["G", 5], ["F", 3], ["I", 1]],
             "calls": ["M0VTS"]}]}]},
}


def _spiegel():
    spec = importlib.util.spec_from_file_location("spiegel", WURZEL / "scripts" / "spiegel.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m.hole = lambda pfad, token, timeout=20.0: ANTWORTEN[pfad.split("?")[0]]
    return m


def _ohne_stand(d: dict) -> dict:
    return {k: v for k, v in d.items() if k != "stand"}


@pytest.mark.parametrize("mit", [True, False])
def test_live_gleicht_dem_spiegel(mit, monkeypatch) -> None:
    """dk9xr.de und das Board lesen live.json unveraendert weiter."""
    m = _spiegel()
    monkeypatch.setattr(m, "GRID", "JN58CH")
    alt = m.baue("egal", mit_rufzeichen=mit)
    neu = aufbereitung.baue_live(ANTWORTEN["/api/status"], ANTWORTEN["/api/stats"],
                                 ANTWORTEN["/api/map"], ANTWORTEN["/api/psk/who-heard-me"],
                                 grid="JN58CH", mit_rufzeichen=mit)
    assert _ohne_stand(neu) == _ohne_stand(alt)
    assert "7654321" not in json.dumps(neu)


def test_profil_gleicht_dem_spiegel() -> None:
    m = _spiegel()
    neu = aufbereitung.baue_profil(ANTWORTEN["/api/psk/tagesprofil"])
    assert _ohne_stand(neu) == _ohne_stand(m.baue_profil("egal"))
    assert "M0VTS" not in json.dumps(neu) and "geheim" not in json.dumps(neu)


class FalscheZentrale:
    """Speichert, was ankommt, wie die Annahme es tun soll (Upsert)."""

    def __init__(self) -> None:
        self.qso: dict[tuple, dict] = {}
        self.karten: dict[str, dict] = {}
        self.bilder: dict[str, bytes] = {}
        self.meldungen: list[dict] = []
        self.profile: list[dict] = []
        self.weg = False
        self.status_code: int | None = None

    def __call__(self, req: httpx.Request) -> httpx.Response:
        if self.weg:
            raise httpx.ConnectError("offline", request=req)
        if self.status_code:
            return httpx.Response(self.status_code, headers={"Retry-After": "120"})
        assert req.headers["authorization"] == "Bearer geheim"
        p = req.url.path
        rumpf = json.loads(req.content) if req.method == "POST" else None
        if rumpf is not None:
            assert rumpf["v"] == 1
        if p == "/v1/meldung":
            self.meldungen.append(rumpf)
            return httpx.Response(204)
        if p == "/v1/profil":
            self.profile.append(rumpf["profil"])
            return httpx.Response(204)
        if p == "/v1/qso/stand":
            k = req.url.params["db_kennung"]
            ids = [i for (dk, i) in self.qso if dk == k]
            return httpx.Response(200, json={"letzte_id": max(ids, default=0), "anzahl": len(ids)})
        if p == "/v1/qso":
            for z in rumpf["zeilen"]:
                self.qso[(rumpf["db_kennung"], z["qso_id"])] = z
            return httpx.Response(200, json={"angenommen": len(rumpf["zeilen"]),
                                             "letzte_id": max(z["qso_id"] for z in rumpf["zeilen"])})
        if p == "/v1/qso/abgleich":
            dk = rumpf["db_kennung"]
            hier = {i: z["hash"] for (k, i), z in self.qso.items() if k == dk}
            dort = dict(rumpf["eintraege"])
            return httpx.Response(200, json={
                "fehlt": [i for i in dort if i not in hier],
                "anders": [i for i in dort if i in hier and hier[i] != dort[i]],
                "nur_zentral": [i for i in hier if i not in dort]})
        if p == "/v1/karten/stand":
            return httpx.Response(200, json={
                "schluessel": list(self.karten),
                "mit_bild": [s for s, k in self.karten.items() if k["sha256"] in self.bilder]})
        if p == "/v1/karten":
            for z in rumpf["zeilen"]:
                self.karten[z["schluessel"]] = z
            fehlen = sorted({z["sha256"] for z in rumpf["zeilen"]
                             if z["sha256"] and z["sha256"] not in self.bilder})
            return httpx.Response(200, json={"fehlen": fehlen})
        if p.startswith("/v1/bild/"):
            import hashlib
            sha = p.rsplit("/", 1)[1]
            assert hashlib.sha256(req.content).hexdigest() == sha
            self.bilder[sha] = req.content
            return httpx.Response(204)
        return httpx.Response(404)


def _lokal(req: httpx.Request) -> httpx.Response:
    assert "authorization" not in req.headers, "lokal braucht keinen Token"
    return httpx.Response(200, json=ANTWORTEN[req.url.path])


@pytest.fixture
async def db(tmp_path):
    from ft8_appliance.db.session import create_all, init_engine
    init_engine(tmp_path / "t.sqlite")
    await create_all()
    yield


async def _qso(call: str, minute: int = 0, user: str = "DO3XR") -> int:
    from ft8_appliance.db import repository
    from ft8_appliance.db.session import session_scope
    t = datetime(2026, 11, 25, 10, 0, tzinfo=UTC) + timedelta(minutes=minute)
    async with session_scope() as s:
        q = await repository.insert_qso(
            s, call=call, band="15m", freq_hz=21074000, mode="FT8", rst_sent=-10,
            rst_rcvd=-12, qso_start=t, qso_end=t + timedelta(minutes=1), my_grid="JN58CH",
            user_callsign=user, my_lat=48.7654321, my_lon=10.1234567)
        return q.id


def _zentrale(tmp_path, falsch: FalscheZentrale) -> Zentrale:
    return Zentrale("https://zentrale.test", "geheim", grid="JN58CH",
                    qsl_verzeichnis=lambda: tmp_path / "qsl",
                    zustand_pfad=tmp_path / "zentrale.json",
                    transport=httpx.MockTransport(falsch),
                    lokal_transport=httpx.MockTransport(_lokal))


@pytest.mark.asyncio
async def test_erste_runde_schickt_alles(db, tmp_path) -> None:
    for i, c in enumerate(("EA4ZM", "IU2QDY", "K1ABC")):
        await _qso(c, i)
    f = FalscheZentrale()
    z = _zentrale(tmp_path, f)
    await z.runde(jetzt=1000.0)
    assert len(f.meldungen) == 1 and f.meldungen[0]["live"]["station"]["grid"] == "JN58CH"
    assert f.meldungen[0]["betrieb"]["freq_hz"] == 14074000
    assert {z_["call"] for z_ in f.qso.values()} == {"EA4ZM", "IU2QDY", "K1ABC"}
    zeile = next(iter(f.qso.values()))
    assert "my_lat" not in zeile and "qrz_uploaded" not in zeile
    assert zeile["qso_start"].endswith("Z")
    assert len(f.profile) == 1
    assert z.rueckstand_qso == 0 and z.letzte_ok


@pytest.mark.asyncio
async def test_offline_geht_nichts_verloren(db, tmp_path) -> None:
    """Die Station sammelt nicht selbst: die DB ist die Warteschlange."""
    await _qso("EA4ZM")
    f = FalscheZentrale()
    z = _zentrale(tmp_path, f)
    await z.runde(jetzt=1000.0)
    f.weg = True
    await _qso("IU2QDY", 5)
    z.neues_qso()
    with pytest.raises(httpx.ConnectError):
        await z.runde(jetzt=1060.0)
    await _qso("K1ABC", 9)
    f.weg = False
    await z.runde(jetzt=1120.0)
    assert {z_["call"] for z_ in f.qso.values()} == {"EA4ZM", "IU2QDY", "K1ABC"}


@pytest.mark.asyncio
async def test_neue_db_kennung_bleibt_ueber_neustarts(db, tmp_path) -> None:
    f = FalscheZentrale()
    a = _zentrale(tmp_path, f).db_kennung
    assert a == _zentrale(tmp_path, f).db_kennung
    (tmp_path / "zentrale.json").unlink()
    assert _zentrale(tmp_path, f).db_kennung != a


@pytest.mark.asyncio
async def test_abgleich_schickt_geaendertes_nach(db, tmp_path) -> None:
    from sqlalchemy import update

    from ft8_appliance.db.models import Qso
    from ft8_appliance.db.session import session_scope
    qid = await _qso("EA4ZM")
    f = FalscheZentrale()
    z = _zentrale(tmp_path, f)
    await z.runde(jetzt=1000.0)
    async with session_scope() as s:
        await s.execute(update(Qso).where(Qso.id == qid).values(notes="nachgetragen"))
    await z.abgleich(z._client())
    assert next(iter(f.qso.values()))["notes"] == "nachgetragen"
    assert json.loads((tmp_path / "zentrale.json").read_text())["abgleich_at"] > 0


def test_hash_haengt_nur_an_den_gesendeten_feldern() -> None:
    class Q:
        id = 7
        call, band, freq_hz, mode = "EA4ZM", "15m", 21074000, "FT8"
        rst_sent = rst_rcvd = grid_rcvd = my_power_w = notes = None
        qso_start = qso_end = datetime(2026, 11, 25, 10, 0)
        my_grid, user_callsign = "JN58CH", "DO3XR"
        qrz_uploaded = False
    a = qso_zeile(Q())
    Q.qrz_uploaded = True
    assert qso_zeile(Q())["hash"] == a["hash"]
    Q.notes = "x"
    assert qso_zeile(Q())["hash"] != a["hash"]
    assert a["qso_start"] == "2026-11-25T10:00:00Z"


@pytest.mark.asyncio
async def test_karten_mit_operator_und_bild(db, tmp_path) -> None:
    from ft8_appliance.db.models import QslKarte
    from ft8_appliance.db.session import session_scope
    (tmp_path / "qsl").mkdir()
    (tmp_path / "qsl" / "a.jpg").write_bytes(b"\xff\xd8jpeg")
    async with session_scope() as s:
        s.add(QslKarte(schluessel="k1", call="EA4ZM", qso_date="20261125", time_on="1000",
                       band="15M", mode="FT8", user_callsign="DK9XR", datei="a.jpg",
                       medientyp="image/jpeg"))
        s.add(QslKarte(schluessel="k2", call="K1ABC", qso_date="20261125", time_on="1001",
                       band="15M", mode="FT8", user_callsign="DO3XR"))
        s.add(QslKarte(schluessel="k3", call="F1XYZ", qso_date="20261125", time_on="1002",
                       band="15M", mode="FT8", user_callsign="DK9XR", datei="../../etc/passwd"))
    f = FalscheZentrale()
    z = _zentrale(tmp_path, f)
    await z.runde(jetzt=1000.0)
    assert f.karten["k1"]["user_callsign"] == "DK9XR"
    assert f.karten["k2"]["user_callsign"] == "DO3XR"
    assert f.karten["k3"]["sha256"] is None, "Pfad ausserhalb des Kartenordners"
    assert list(f.bilder.values()) == [b"\xff\xd8jpeg"]
    # Spaeter kommt das Bild zu k2 — die Karte geht noch einmal hinaus.
    (tmp_path / "qsl" / "b.png").write_bytes(b"\x89PNGpng")
    from sqlalchemy import update
    async with session_scope() as s:
        await s.execute(update(QslKarte).where(QslKarte.schluessel == "k2")
                        .values(datei="b.png", medientyp="image/png"))
    await z.karten(z._client())
    assert len(f.bilder) == 2


@pytest.mark.asyncio
async def test_fehler_der_zentrale(db, tmp_path) -> None:
    f = FalscheZentrale()
    z = _zentrale(tmp_path, f)
    f.status_code = 429
    with pytest.raises(ZentraleFehler) as e:
        await z.runde(jetzt=1000.0)
    assert e.value.warte_s == 120.0
    f.status_code = 401
    with pytest.raises(ZentraleFehler) as e:
        await z.runde(jetzt=1000.0)
    assert e.value.dauerhaft and "geheim" not in str(e.value)


def test_ohne_url_kein_sender() -> None:
    from ft8_appliance.config.models import AppConfig
    cfg = AppConfig()
    assert cfg.zentrale.url is None and cfg.zentrale.token is None


@pytest.mark.asyncio
async def test_grosser_kartenbestand_geht_in_stuecken(db, tmp_path) -> None:
    """Ueber 8000 Karten am MK2: nicht alles in einer Runde, aber alles."""
    from ft8_appliance.db.models import QslKarte
    from ft8_appliance.db.session import session_scope
    from ft8_appliance.zentrale import sender
    (tmp_path / "qsl").mkdir()
    async with session_scope() as s:
        for i in range(250):
            (tmp_path / "qsl" / f"{i}.jpg").write_bytes(b"\xff\xd8" + str(i).encode())
            s.add(QslKarte(schluessel=f"k{i}", call="EA4ZM", qso_date="20261125",
                           time_on="1000", band="15M", mode="FT8", user_callsign="DK9XR",
                           datei=f"{i}.jpg", medientyp="image/jpeg"))
        s.add(QslKarte(schluessel="gif", call="F1XYZ", qso_date="20261125", time_on="1000",
                       band="15M", mode="FT8", user_callsign="DK9XR", datei="0.jpg",
                       medientyp="image/gif"))
    f = FalscheZentrale()
    z = _zentrale(tmp_path, f)
    runden = 0
    while await z.karten(z._client()):
        runden += 1
        assert runden < 30
    assert len(f.karten) == 251 and len(f.bilder) == 250
    assert runden >= 250 // sender.BILDER_JE_RUNDE - 1
    assert not await z.karten(z._client()), "die GIF-Karte darf nicht ewig offen bleiben"
