"""Was von der Station nach draussen darf — dieselben Filter wie ``scripts/spiegel.py``.

Bis 2026-11 holte ein Spiegel auf dem Webserver die Daten jede Minute vom Pi
ab und filterte sie dort. Seit die Station selbst an die Zentrale meldet,
filtert sie vorher. Die Ergebnisse sind Schluessel fuer Schluessel dieselben
wie ``live.json`` und ``profil.json`` des Spiegels; die Annahme legt sie
unveraendert ab, und dk9xr.de liest sie wie bisher.

Die Funktionen bekommen die Antworten der eigenen API als Woerterbuecher
und rufen nichts selbst ab. So lassen sie sich ohne Pi pruefen.

* Der Standort wird auf die Mitte des Locator-Feldes gerundet.
* Vom Geraetestatus nur Band, Frequenz, Leistung, SWR und Sendezustand.
* Rufzeichen der Gegenstationen sind abschaltbar.
"""
from __future__ import annotations

from datetime import UTC, datetime


def stand_jetzt() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def locator_mitte(lat: float, lon: float) -> tuple[float, float]:
    """Auf die Mitte des Maidenhead-Feldes runden (rund 100 km Raster)."""
    feld_lon = (lon + 180) // 2 * 2 - 180 + 1
    feld_lat = (lat + 90) // 1 * 1 - 90 + 0.5
    return round(feld_lat, 3), round(feld_lon, 3)


def baue_live(status: dict, stats: dict, karte: dict, psk: dict, *,
              grid: str, mit_rufzeichen: bool) -> dict:
    lat, lon = locator_mitte(karte.get("operator_lat") or 48.3,
                             karte.get("operator_lon") or 10.2)
    rig = status.get("rig") or {}

    marker = []
    for m in karte.get("markers") or []:
        if m.get("lat") is None:
            continue
        eintrag = {
            "lat": round(m["lat"], 1), "lon": round(m["lon"], 1),
            "art": {"worked": "w", "heard": "h", "both": "b"}.get(m.get("kind"), "h"),
            "band": m.get("band") or "",
            "zeit": (m.get("last_worked") or m.get("last_seen") or "")[:16],
        }
        if mit_rufzeichen:
            eintrag["call"] = m.get("call")
        marker.append(eintrag)
    marker.sort(key=lambda m: m["zeit"], reverse=True)

    berichte = []
    laender: dict[str, int] = {}
    for r in (psk.get("reports") or [])[:400]:
        flagge = r.get("flag") or ""
        laender[flagge] = laender.get(flagge, 0) + 1
        if len(berichte) < 25:
            eintrag = {"snr": r.get("snr_db"), "flagge": flagge,
                       "zeit": (r.get("received_at") or "")[11:16],
                       "grid": (r.get("rx_grid") or "")[:4]}
            if mit_rufzeichen:
                eintrag["call"] = r.get("rx_call")
            berichte.append(eintrag)

    best = stats.get("best_dx_today") or None
    if best and not mit_rufzeichen:
        best = {k: v for k, v in best.items() if k != "call"}

    return {
        "station": {
            "call": status.get("callsign"), "grid": grid,
            "lat": lat, "lon": lon,
            "zustand": status.get("state"),
            "auto_cq": bool(status.get("auto_cq")),
            "auto_answer": bool(status.get("auto_answer")),
            "modus": status.get("mode"),
            "band": status.get("active_band"),
            "freq_mhz": round((rig.get("freq_hz") or 0) / 1e6, 3),
            "power_w": status.get("tx_power_w"),
            "swr": rig.get("swr"),
            "sendet": bool(rig.get("ptt")),
            "gearbeitet_gesamt": status.get("worked_count"),
        },
        "heute": {
            "qso": stats.get("qso_today"), "dxcc": stats.get("dxccs_today"),
            "qso_7d": stats.get("qso_7d"), "qso_gesamt": stats.get("qso_total"),
            "decodes_h": stats.get("decodes_last_hour"), "best": best,
        },
        "marker": marker,
        "hoerer": berichte,
        "hoerer_laender": sorted(laender.items(), key=lambda x: -x[1])[:10],
        "hoerer_gesamt": len(psk.get("reports") or []),
        "stand": stand_jetzt(),
    }


def baue_betrieb(status: dict) -> dict:
    """Nur fuer die Zentrale intern, wird nicht veroeffentlicht."""
    rig = status.get("rig") or {}
    return {
        "band": status.get("active_band"), "freq_hz": rig.get("freq_hz"),
        "mode": status.get("mode"), "swr": rig.get("swr"), "ptt": bool(rig.get("ptt")),
        "leistung_w": status.get("tx_power_w"), "zustand": status.get("state"),
        "gesperrt_grund": status.get("last_lock_code"),
    }


def baue_profil(roh: dict) -> dict:
    """Wer uns wann hoert, je Band, Kontinent und Stunde (UTC) — nur Aufgezaehltes."""
    baender = []
    for b in roh.get("baender") or []:
        baender.append({
            "band": b.get("band"),
            "empfaenger": b.get("empfaenger"),
            "stunden": [{
                "h": s.get("h"), "tage": s.get("tage"),
                "gesamt": s.get("gesamt"),
                "kontinente": s.get("kontinente") or {},
                "laender": s.get("laender"),
                "top": [[f, n] for f, n in (s.get("top") or [])][:3],
            } for s in b.get("stunden") or []],
        })
    return {"tage": roh.get("tage"), "baender": baender, "stand": stand_jetzt()}
