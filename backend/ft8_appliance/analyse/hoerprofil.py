"""Wer uns wann hoert — Empfangsberichte als Tagesprofil.

Verdichtet die Berichte von pskreporter.info zu Band x Kontinent x Stunde
(UTC). Gezaehlt werden **verschiedene Empfaenger**, nicht Berichte: Eine
Station, die uns in einer Stunde zwanzigmal meldet, ist ein Hoerer.

Gemittelt wird je Stunde ueber die Tage, an denen auf diesem Band in dieser
Stunde ueberhaupt Berichte kamen. Eine Stunde, in der die Station nicht
gesendet hat, zieht den Schnitt sonst nach unten, ohne dass sich an der
Ausbreitung etwas geaendert haette.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable

KONTINENTE = ("EU", "AS", "NA", "SA", "AF", "OC")

# Unterhalb davon ist ein Band Rauschen, keine Nutzung: Vom 11. bis 17.09.
# standen 45.695 Berichten auf 20 m je ein bis elf Berichte auf sechs
# anderen Baendern — Fehlzuordnungen, keine Sendungen dort.
MINDEST_EMPFAENGER = 30

_BANDFOLGE = ("160m", "80m", "60m", "40m", "30m", "20m", "17m", "15m",
              "12m", "10m", "6m", "4m", "2m", "70cm")


def _bandrang(band: str) -> int:
    try:
        return _BANDFOLGE.index(band)
    except ValueError:
        return len(_BANDFOLGE)


def baue_profil(
    zeilen: Iterable[tuple[str, str, int, str]],
    kontinent_von: Callable[[str], str | None],
    land_von: Callable[[str], tuple[str, str] | None],
    *,
    mindest_empfaenger: int = MINDEST_EMPFAENGER,
) -> list[dict]:
    """``zeilen``: je (band, tag, stunde, rx_call), eindeutig.

    ``kontinent_von`` liefert EU/AS/NA/SA/AF/OC oder None, ``land_von``
    (Name, Flagge) oder None. Rueckgabe je Band, in Frequenzfolge.
    """
    # band -> stunde -> Menge (tag, call)
    hoerer: dict[str, dict[int, set[tuple[str, str]]]] = defaultdict(
        lambda: defaultdict(set))
    for band, tag, stunde, call in zeilen:
        if not band or not call:
            continue
        hoerer[band][int(stunde)].add((tag, call.upper()))

    kont_cache: dict[str, str | None] = {}
    land_cache: dict[str, tuple[str, str] | None] = {}

    def kont(call: str) -> str | None:
        if call not in kont_cache:
            kont_cache[call] = kontinent_von(call)
        return kont_cache[call]

    def land(call: str) -> tuple[str, str] | None:
        if call not in land_cache:
            land_cache[call] = land_von(call)
        return land_cache[call]

    ergebnis = []
    for band in sorted(hoerer, key=_bandrang):
        je_stunde = hoerer[band]
        empfaenger = {c for paare in je_stunde.values() for _, c in paare}
        if len(empfaenger) < mindest_empfaenger:
            continue

        stunden = []
        for h in range(24):
            paare = je_stunde.get(h, set())
            tage = len({t for t, _ in paare})
            if not tage:
                stunden.append({"h": h, "tage": 0, "gesamt": 0,
                                "kontinente": {}, "laender": 0, "top": []})
                continue
            je_kont: dict[str, int] = defaultdict(int)
            for _, c in paare:
                k = kont(c)
                if k in KONTINENTE:
                    je_kont[k] += 1
            # Laender ueber den ganzen Zeitraum, nicht gemittelt: "aus
            # welchen Laendern hoert man uns um diese Uhrzeit". Zusammengefasst
            # wird nach Flagge, nicht nach DXCC-Gebiet — cty.dat fuehrt das
            # europaeische und das asiatische Russland getrennt, und zwei
            # gleiche Flaggen nebeneinander sehen auf der Seite wie ein
            # Fehler aus.
            je_land: dict[str, set[str]] = defaultdict(set)
            name_von: dict[str, str] = {}
            for _, c in paare:
                l = land(c)
                if l is not None:
                    schluessel = l[1] or l[0]
                    je_land[schluessel].add(c)
                    name_von.setdefault(schluessel, l[0])
            top = sorted(je_land.items(), key=lambda x: (-len(x[1]), x[0]))[:3]
            stunden.append({
                "h": h, "tage": tage,
                "gesamt": round(len(paare) / tage, 1),
                "kontinente": {k: round(je_kont[k] / tage, 1)
                               for k in KONTINENTE if je_kont.get(k)},
                "laender": len(je_land),
                "top": [[s if s != name_von[s] else "", name_von[s]]
                        for s, _ in top],
            })
        ergebnis.append({"band": band, "empfaenger": len(empfaenger),
                         "stunden": stunden})
    return ergebnis
