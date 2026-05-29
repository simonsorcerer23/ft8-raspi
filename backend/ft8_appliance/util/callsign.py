"""Callsign-Normalisierung.

``base_call`` reduziert ein Rufzeichen auf seinen lizenzierten Stamm:
Suffixe wie /P, /M, /MM, /AM, /QRP fallen weg, Compound-Prefixe
(DL/W1AW) werden auf den eigentlichen Call (W1AW) reduziert, Klammern
von hash-aufgeloesten Decodes (<DL7PM>) entfernt.

Verwendung v.a. fuer die Soft-Blacklist/Reputation: das Funkverhalten
einer Station (bricht ab, hoert uns nie, schliesst nie) haengt am
Menschen hinter dem Call — nicht daran ob er gerade /P oder /MM
unterwegs ist. Deshalb wird Reputation auf dem Basis-Call gefuehrt.

NICHT fuer DXCC/Award-Logik verwenden: dort sind Suffixe regelbehaftet
(/MM und /AM zaehlen z.B. gar nicht, ein PREFIX aendert das Land).
"""
from __future__ import annotations


def base_call(call: str | None) -> str:
    """Reduziere ein Rufzeichen auf den Basis-Call (uppercase).

    Leerer/None-Input → "".
    """
    c = (call or "").upper().strip()
    if not c:
        return ""
    if c.startswith("<") and c.endswith(">"):
        c = c[1:-1]
    if "/" in c:
        # Laengstes Teilstueck ist der lizenzierte Call (DL/W1AW → W1AW,
        # W1AW/P → W1AW). Bei Gleichstand das erste.
        c = max(c.split("/"), key=len)
    return c
