"""Tuner-Vergleich: werden wir mit eingebautem Tuner besser gehoert? (ab 2026-10-12)

Seit dem 11.10.2026 laeuft die Station ohne den eingebauten Tuner, damit der
SWR-Schutz die Antenne selbst sieht. Am Geraet aendert er nichts (39 W,
SWR 1,0, 12 A mit wie ohne) — aber die Hoererzahlen lagen danach unter denen
von vor dem Ausfall, bei zugleich niedrigerer Leistung und gestoertem
Funkwetter. Sebastian: "liegt die niedrige Hoererzahl am ausgeschalteten
Tuner?" Das laesst sich nur im Wechsel klaeren.

Wie beim AGC-Vergleich haengt der Arm allein an der Uhr: Jeder
15-Minuten-Block gehoert fest zu "an" oder "aus", mit eigenem Salz, damit die
beiden Vergleiche nicht im Gleichschritt laufen.
"""
from __future__ import annotations

import hashlib

BLOCK_S = 900
ARME = ("an", "aus")
_SALZ = "tuner"


def block_von(epoch_s: float) -> int:
    return int(epoch_s // BLOCK_S)


def arm_von_block(block: int) -> str:
    ziffer = hashlib.sha256((_SALZ + str(block)).encode("ascii")).digest()[0]
    return ARME[ziffer % len(ARME)]


def arm_zu(epoch_s: float) -> str:
    return arm_von_block(block_von(epoch_s))
