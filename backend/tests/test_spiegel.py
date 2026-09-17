"""Der Spiegel zur Homepage — was gefiltert wird, ist sein Zweck.

``scripts/spiegel.py`` laeuft auf dem Webserver und holt sich den Stand
der Station ueber Tailscale. Alles, was er schreibt, ist oeffentlich
abrufbar — deshalb liegt hier der Nachdruck auf dem, was NICHT
hinausgeht.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

WURZEL = Path(__file__).resolve().parents[2]


def _modul():
    pfad = WURZEL / "scripts" / "spiegel.py"
    spec = importlib.util.spec_from_file_location("spiegel", pfad)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_standort_wird_auf_das_locator_feld_gerundet() -> None:
    """Die Anlage kennt den Standort auf sieben Nachkommastellen. So
    genau muss er nicht im Netz stehen — das Feld JN58 reicht, und genau
    das steht ohnehin in der Rufzeichenliste der Bundesnetzagentur."""
    m = _modul()
    lat, lon = m.locator_mitte(48.7654321, 10.1234567)
    # Feldmitte JN58: 48,5 N / 11 O — rund 50 km daneben.
    assert lat == 48.5
    assert lon == 11.0
    assert abs(lat - 48.7654321) > 0.1, "Breite unveraendert durchgereicht"
    assert abs(lon - 10.1234567) > 0.5, "Laenge unveraendert durchgereicht"


def test_rundung_gilt_auch_suedlich_und_westlich() -> None:
    """Negative Koordinaten sind der Fall, in dem eine naive Rundung
    kippt — und Raymond faehrt zur See."""
    m = _modul()
    lat, lon = m.locator_mitte(-33.9, -18.4)
    assert -34 <= lat <= -33
    assert -20 <= lon <= -17


def test_atomar_schreiben_hinterlaesst_keine_halbe_datei(tmp_path) -> None:
    """nginx liefert aus, was da liegt. Ohne os.replace waere irgendwann
    eine halb geschriebene JSON-Datei im Netz."""
    m = _modul()
    m.schreibe(tmp_path, "probe.json", {"a": 1})
    assert (tmp_path / "probe.json").read_text() == '{"a":1}'
    # Kein Ueberbleibsel der Zwischendatei
    assert [p.name for p in tmp_path.iterdir()] == ["probe.json"]


def test_ohne_rufzeichen_bleibt_kein_fremdes_call_uebrig() -> None:
    """Der Schalter ist die Ruecknahme fuer den Fall, dass die
    Rechtsfrage doch anders beantwortet wird. Dann duerfen auch in
    Kartenmarkern, Empfangsberichten und der Bestleistung keine fremden
    Rufzeichen mehr stehen."""
    m = _modul()

    antworten = {
        "/api/status": {"callsign": "DK9XR", "state": "CQ_CALLING",
                        "active_band": "20m", "tx_power_w": 70,
                        "worked_count": 6000, "rig": {"freq_hz": 14074000,
                                                      "swr": 1.0, "ptt": False}},
        "/api/stats": {"qso_today": 5, "dxccs_today": 3, "qso_7d": 40,
                       "qso_total": 400, "decodes_last_hour": 500,
                       "best_dx_today": {"call": "RC3DN", "grid": "KO95",
                                         "band": "20m",
                                         "distance_km_estimate": 2116}},
        "/api/map": {"operator_lat": 48.7654321, "operator_lon": 10.1234567,
                     "markers": [{"call": "UT0UE", "lat": 50.5, "lon": 31.0,
                                  "kind": "worked", "band": "20m",
                                  "last_worked": "2026-09-12T14:43:15"}]},
        "/api/psk/who-heard-me": {"reports": [
            {"rx_call": "M0VTS", "rx_grid": "IO82WS", "snr_db": -14,
             "band": "20m", "received_at": "2026-09-16T08:33:42+00:00",
             "flag": "\U0001F1EC\U0001F1E7"}]},
    }
    m.hole = lambda pfad, token, timeout=20.0: antworten[pfad.split("?")[0]]

    aus = m.baue("egal", mit_rufzeichen=False)
    roh = repr(aus)
    for call in ("UT0UE", "M0VTS", "RC3DN"):
        assert call not in roh, f"{call} steht trotz --ohne-rufzeichen drin"
    # Die Zahlen bleiben: der Sinn der Sache.
    assert aus["heute"]["qso"] == 5
    assert aus["marker"][0]["lat"] == 50.5
    assert aus["heute"]["best"]["distance_km_estimate"] == 2116

    # Und mit Rufzeichen sind sie da — sonst prueft der Test nur sich selbst.
    mit = m.baue("egal", mit_rufzeichen=True)
    assert "UT0UE" in repr(mit)


def test_geraetestatus_gibt_keine_gps_position_heraus() -> None:
    """Der Status der Anlage enthaelt die GPS-Koordinaten des Empfaengers
    und die Audiopegel. Auf einer Stationsseite hat beides nichts zu
    suchen; hinaus geht nur, was ausdruecklich aufgezaehlt ist."""
    m = _modul()
    antworten = {
        "/api/status": {"callsign": "DK9XR", "state": "IDLE",
                        "active_band": "20m", "tx_power_w": 70,
                        "worked_count": 1,
                        "gps": {"lat": 48.4567890, "lon": 10.1637},
                        "rx_audio_dbfs": -31.5,
                        "rig": {"freq_hz": 14074000, "swr": 1.0, "ptt": False}},
        "/api/stats": {"qso_today": 0, "best_dx_today": None},
        "/api/map": {"operator_lat": 48.3, "operator_lon": 10.2, "markers": []},
        "/api/psk/who-heard-me": {"reports": []},
    }
    m.hole = lambda pfad, token, timeout=20.0: antworten[pfad.split("?")[0]]
    roh = repr(m.baue("egal", mit_rufzeichen=True))
    assert "48.4567890" not in roh, "GPS-Position durchgereicht"
    assert "rx_audio_dbfs" not in roh
    assert "gps" not in roh


def test_tagesprofil_reicht_nur_aufgezaehlte_felder_durch() -> None:
    """Der Pi liefert keine Rufzeichen im Profil. Kaeme durch eine spaetere
    Aenderung doch eines dazu, darf es nicht auf der Webseite landen."""
    m = _modul()
    m.hole = lambda pfad, token, timeout=20.0: {
        "tage": 7,
        "baender": [{"band": "20m", "empfaenger": 6014, "rx_calls": ["M0VTS"],
                     "stunden": [{"h": 8, "tage": 6, "gesamt": 210.5,
                                  "kontinente": {"EU": 180.2, "AS": 12.0},
                                  "laender": 41, "top": [["🇩🇪", "Germany"]],
                                  "beispiel": "UT0UE"}]}],
    }
    aus = m.baue_profil("egal")
    roh = repr(aus)
    assert "M0VTS" not in roh and "UT0UE" not in roh
    assert aus["baender"][0]["stunden"][0]["kontinente"]["EU"] == 180.2
    assert aus["baender"][0]["stunden"][0]["top"] == [["🇩🇪", "Germany"]]


def test_tagesprofil_wird_nur_alle_viertelstunde_neu_geholt(tmp_path) -> None:
    m = _modul()
    datei = tmp_path / "profil.json"
    assert m.ist_faellig(datei, 900), "fehlt die Datei, ist sie faellig"
    datei.write_text("{}")
    jetzt = datei.stat().st_mtime
    assert not m.ist_faellig(datei, 900, jetzt=jetzt + 60)
    assert m.ist_faellig(datei, 900, jetzt=jetzt + 901)
