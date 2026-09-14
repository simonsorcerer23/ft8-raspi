"""Erwartungswert-Modul: Klassen, Schrumpfung, Wert, Schwelle."""
from __future__ import annotations

from ft8_appliance.analyse.erwartungswert import (
    PTabelle, Wertfaktoren, p_cq_aus, schwelle, snr_klasse, wert,
)


def test_signalklassen_an_den_gemessenen_kanten() -> None:
    assert snr_klasse(-6) == "a" and snr_klasse(-7) == "b"
    assert snr_klasse(-12) == "b" and snr_klasse(-13) == "c"
    assert snr_klasse(-17) == "c" and snr_klasse(-18) == "d"
    assert snr_klasse(None) == "b"


def test_leere_tabelle_liefert_startwert() -> None:
    p, n = PTabelle().schaetze(-10, "EU", False)
    assert p == 0.15 and n == 0


def test_kleine_zelle_bleibt_nah_am_elternmittel() -> None:
    """Drei Anrufe, null Erfolge: ohne Schrumpfung 0 %. Mit K=20 und einem
    Elternmittel von 20 % darf die Zelle nicht unter ~17 % fallen."""
    zeilen = [(-10, "EU", False, i % 5 == 0) for i in range(200)]   # 20 %
    zeilen += [(-10, "OC", False, False)] * 3
    t = PTabelle.aus_anrufen(zeilen)
    p_oc, n_oc = t.schaetze(-10, "OC", False)
    assert n_oc == 3 and 0.16 < p_oc < 0.20


def test_grosse_zelle_setzt_sich_durch() -> None:
    zeilen = [(-10, "EU", False, i % 5 == 0) for i in range(200)]   # 20 %
    zeilen += [(-10, "NA", False, i % 50 == 0) for i in range(300)]  # 2 %
    p_na, _ = PTabelle.aus_anrufen(zeilen).schaetze(-10, "NA", False)
    assert p_na < 0.05


def test_psk_beleg_ist_eigene_dimension() -> None:
    zeilen = [(-15, "EU", False, i % 10 == 0) for i in range(300)]   # 10 %
    zeilen += [(-15, "EU", True, i % 3 == 0) for i in range(300)]    # 33 %
    t = PTabelle.aus_anrufen(zeilen)
    assert t.schaetze(-15, "EU", True)[0] > 2 * t.schaetze(-15, "EU", False)[0]


def test_unbekannter_kontinent_faellt_auf_klasse_zurueck() -> None:
    zeilen = [(-10, "EU", False, i % 4 == 0) for i in range(400)]   # 25 %
    p, n = PTabelle.aus_anrufen(zeilen).schaetze(-10, None, False)
    assert n == 0 and 0.22 < p < 0.26


def test_wert_nimmt_den_hoechsten_faktor() -> None:
    f = Wertfaktoren()
    assert wert(f, new_dxcc=False, new_dxcc_band=False, new_grid=False, watchlist=False, rarity=0) == 1.0
    assert wert(f, new_dxcc=True, new_dxcc_band=True, new_grid=True, watchlist=True, rarity=99) == 5.0
    assert wert(f, new_dxcc=True, new_dxcc_band=False, new_grid=False, watchlist=False, rarity=0) == 3.0
    assert wert(f, new_dxcc=False, new_dxcc_band=False, new_grid=False, watchlist=False, rarity=40) == 2.0


def test_schwelle_rechnet_zeitkosten_ein() -> None:
    """P_cq 2 % je 30-s-Ruf entspricht 6 % je 90-s-Anruf."""
    assert abs(schwelle(0.02) - 0.06) < 1e-9
    assert schwelle(0.02, anruf_s=60.0) == 0.04
    assert schwelle(-1.0) == 0.0


def test_p_cq_messung_ist_abgeschaltet() -> None:
    """Die Formel zaehlte ALLE eingehenden Erfolge als CQ-Ertrag, auch
    die, die uns waehrend eines laufenden QSO erreichten. p_cq sprang
    dadurch von 0,015 auf 0,105 und die Schwelle auf 31,5 Prozent — der
    A/B-Test zeigte es binnen eines halben Tages (1,6 gegen 5,2 QSOs je
    Stunde). Bis eine saubere Messung existiert, gilt der Konfigwert."""
    assert p_cq_aus(3, 100 * 30.0) is None
    assert p_cq_aus(48, 13740.0) is None
    assert p_cq_aus(0, 0.0) is None
