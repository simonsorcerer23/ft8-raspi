#!/usr/bin/env python3
"""Spiegelt den Stand der Station auf den Webserver — filtert, was nicht raus soll.

Laeuft auf dem Webserver (Altec), nicht auf dem Pi. Der Altec liegt im
selben Tailscale-Netz und erreicht die Anlage in rund 150 Millisekunden;
der Pi bleibt dabei vollstaendig unerreichbar aus dem Internet. Er hat
keinen offenen Port, und daran aendert dieser Spiegel nichts.

Das Ergebnis sind statische Dateien, die nginx ausliefert. Kein Dienst,
kein Zustand, keine Datenbank — und damit auch nichts, was abstuerzen
kann, waehrend jemand die Seite aufruft.

**Was gefiltert wird, ist der eigentliche Zweck dieses Skripts:**

* Der Standort der Station kommt auf sieben Nachkommastellen aus der
  Anlage. Hier wird er auf das Zentrum des Locator-Feldes gerundet —
  gut 100 Kilometer Unschaerfe, und genau das, was in der amtlichen
  Rufzeichenliste ohnehin steht.
* Vom Geraetestatus geht nur nach draussen, was auf einer Stationsseite
  Sinn ergibt: Band, Frequenz, Leistung, SWR. Nicht die GPS-Position,
  nicht die Audiopegel, nicht die Update-Zustaende.
* Rufzeichen der Gegenstationen sind ueber ``--ohne-rufzeichen``
  abschaltbar. Dann bleiben Punkte auf der Karte und Zahlen.

**Wenn der Pi nicht antwortet, passiert nichts.** Die alten Dateien
bleiben liegen und tragen ihren Zeitstempel; die Seite zeigt damit den
letzten bekannten Stand statt einer Fehlermeldung. Erst wenn ein Abruf
gelingt, wird geschrieben — und zwar atomar, damit nie eine halbe Datei
ausgeliefert wird.

    ./spiegel.py --ziel /opt/dk9xr-live
    ./spiegel.py --ziel /opt/dk9xr-live --ohne-rufzeichen
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

# Adresse der Station. Der Tailscale-Name reicht; wer anders benennt,
# setzt FT8_PI (etwa "http://192.168.1.50:8000"). Eine feste IP gehoert
# nicht in ein oeffentliches Repository — sie ist fuer jeden Nachbauer
# falsch und verraet fuer nichts die eigene Netzstruktur.
PI = os.environ.get("FT8_PI", "http://ft8-pi5:8000").rstrip("/")
TOKEN_DATEI = Path(os.environ.get("FT8_SPIEGEL_TOKEN", "/etc/dk9xr-spiegel/token"))

# Was vom Geraetestatus nach draussen darf. Alles andere bleibt hier.
RIG_FELDER = ("freq_hz", "mode", "swr", "ptt")


def hole(pfad: str, token: str, timeout: float = 20.0):
    # Die Appliance prueft "Authorization: Bearer". Ein eigener Header
    # wird nur auf localhost durchgewunken — von aussen gibt es 401.
    req = urllib.request.Request(
        PI + pfad, headers={"Authorization": f"Bearer {token}",
                            "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def locator_mitte(lat: float, lon: float) -> tuple[float, float]:
    """Auf die Mitte des Maidenhead-Feldes runden (rund 100 km Raster).

    Ein Feld ist 2 Grad breit und 1 Grad hoch. Wer die Position der
    Station wissen will, findet sie ohnehin in der Rufzeichenliste der
    Bundesnetzagentur — aber auf sieben Nachkommastellen muss sie
    deshalb nicht im Netz stehen.
    """
    feld_lon = (lon + 180) // 2 * 2 - 180 + 1
    feld_lat = (lat + 90) // 1 * 1 - 90 + 0.5
    return round(feld_lat, 3), round(feld_lon, 3)


def schreibe(ziel: Path, name: str, daten) -> None:
    """Atomar schreiben — sonst liefert nginx irgendwann eine halbe Datei."""
    ziel.mkdir(parents=True, exist_ok=True)
    tmp = tempfile.NamedTemporaryFile(
        "w", dir=ziel, prefix=f".{name}.", delete=False, encoding="utf-8")
    try:
        json.dump(daten, tmp, ensure_ascii=False, separators=(",", ":"))
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp.close()
        os.chmod(tmp.name, 0o644)
        os.replace(tmp.name, ziel / name)
    except BaseException:
        tmp.close()
        Path(tmp.name).unlink(missing_ok=True)
        raise


def baue(token: str, *, mit_rufzeichen: bool) -> dict:
    status = hole("/api/status", token)
    stats = hole("/api/stats", token)
    karte = hole("/api/map", token)
    psk = hole("/api/psk/who-heard-me", token)

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
            "call": status.get("callsign"), "grid": "JN58BH",
            "lat": lat, "lon": lon,
            "zustand": status.get("state"),
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
        "stand": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


# Das Tagesprofil aendert sich im Stundentakt, und der Pi rechnet dafuer
# eine Woche Empfangsberichte durch. Jede Minute waere Verschwendung.
PROFIL_ALTER_S = 900.0


def ist_faellig(pfad: Path, max_alter_s: float, jetzt: float | None = None) -> bool:
    try:
        alter = (time.time() if jetzt is None else jetzt) - pfad.stat().st_mtime
    except OSError:
        return True
    return alter >= max_alter_s


def baue_profil(token: str) -> dict:
    """Wer uns wann hoert, je Band, Kontinent und Stunde (UTC).

    Der Pi liefert hier schon keine Rufzeichen. Uebernommen wird trotzdem
    nur, was ausdruecklich aufgezaehlt ist — wie beim Geraetestatus.
    """
    roh = hole("/api/psk/tagesprofil?tage=7", token, timeout=60.0)
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
    return {"tage": roh.get("tage"), "baender": baender,
            "stand": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}


def baue_qsl(token: str, ziel: Path, *, grenze: int = 120) -> dict:
    """Die neuesten Karten spiegeln — Metadaten und Bilder.

    Bilder werden nur geholt, wenn sie hier noch fehlen. Sie aendern sich
    nie: einmal gespiegelt, bleibt es dieselbe Karte.
    """
    liste = hole(f"/api/qsl?limit={grenze}&gruppiert=true&nur_mit_bild=true", token)
    bilder = ziel / "qsl"
    bilder.mkdir(parents=True, exist_ok=True)
    karten = []
    neu = 0
    for k in liste.get("karten") or []:
        name = f"{k['id']}.jpg"
        pfad = bilder / name
        if not pfad.exists():
            try:
                req = urllib.request.Request(
                    f"{PI}/api/qsl/{k['id']}/bild",
                    headers={"Authorization": f"Bearer {token}"})
                with urllib.request.urlopen(req, timeout=30) as r:
                    daten = r.read()
                tmp = pfad.with_suffix(".teil")
                tmp.write_bytes(daten)
                os.chmod(tmp, 0o644)
                tmp.replace(pfad)
                neu += 1
            except Exception:
                continue
        karten.append({
            "bild": f"qsl/{name}", "call": k["call"], "datum": k["qso_date"],
            "band": k["band"], "mode": k["mode"], "anzahl": k.get("anzahl", 1),
            "gruss": k.get("nachricht"),
        })
    return {"karten": karten, "gesamt": liste.get("gesamt"),
            "neu_gespiegelt": neu,
            "stand": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ziel", required=True, type=Path)
    ap.add_argument("--token-datei", type=Path, default=TOKEN_DATEI)
    ap.add_argument("--ohne-rufzeichen", action="store_true",
                    help="nur Punkte und Zahlen, keine fremden Rufzeichen")
    ap.add_argument("--ohne-qsl", action="store_true")
    ap.add_argument("--leise", action="store_true")
    args = ap.parse_args()

    try:
        token = args.token_datei.read_text().strip()
    except OSError as exc:
        print(f"Token nicht lesbar: {exc}", file=sys.stderr)
        return 2
    if not token:
        print("Token ist leer", file=sys.stderr)
        return 2

    try:
        live = baue(token, mit_rufzeichen=not args.ohne_rufzeichen)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        # Die Station ist nicht erreichbar. Nichts schreiben — die alten
        # Dateien tragen ihren Zeitstempel, und die Seite zeigt damit den
        # letzten bekannten Stand statt einer Luecke.
        if not args.leise:
            print(f"Station nicht erreichbar ({type(exc).__name__}), "
                  "alter Stand bleibt stehen", file=sys.stderr)
        return 1
    schreibe(args.ziel, "live.json", live)

    if not args.ohne_qsl:
        try:
            schreibe(args.ziel, "qsl.json", baue_qsl(token, args.ziel))
        except Exception as exc:
            if not args.leise:
                print(f"QSL-Spiegel uebersprungen: {exc}", file=sys.stderr)

    if ist_faellig(args.ziel / "profil.json", PROFIL_ALTER_S):
        try:
            schreibe(args.ziel, "profil.json", baue_profil(token))
        except Exception as exc:
            # Aeltere Pi-Version ohne Endpunkt, oder die Station ist gerade
            # beschaeftigt: live.json ist davon nicht betroffen.
            if not args.leise:
                print(f"Tagesprofil uebersprungen: {exc}", file=sys.stderr)

    if not args.leise:
        s = live["station"]
        print(f"{s['call']} {s['zustand']} {s['band']} · "
              f"{len(live['marker'])} Marker · {live['heute']['qso']} QSOs heute")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
