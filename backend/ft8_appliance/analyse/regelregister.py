"""Regelregister: jede Filterstufe des Pickers mit Beleg und Verfallsdatum.

Seit 2026-09-14. Bis dahin stand die Begruendung einer Regel im Kommentar
neben ihrem Code — und ueberlebte dort jede spaetere Aenderung der
Datenlage, weil niemand sie wieder las. Hier steht je Stufe: was sie
behauptet, womit das belegt wurde (mit Datum und Fallzahl), wie man es
nachpruefen wuerde, und wann der Beleg verfaellt.

Verfallen koennen nur Regeln der Art "chance" — die, die eine Erfolgs-
wahrscheinlichkeit schaetzen und danach verwerfen. Technische Gates
("geht nicht") und Sperren ("darf nicht") brauchen keinen Ausbeute-Beleg.

Ein Test haelt Register und Code deckungsgleich: Jede Stufe, die
``_buche_filter`` im Picker bucht, muss hier stehen, und umgekehrt.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime

GUELTIG_TAGE = 90


@dataclass(frozen=True, slots=True)
class Regel:
    stufe: str            # Name wie in StateMachine._buche_filter
    art: str              # "chance" | "technisch" | "sperre"
    behauptung: str
    beleg: str            # was gemessen wurde, mit n — oder "keiner"
    beleg_datum: date | None
    pruefung: str         # wie man den Beleg erneuert
    gueltig_tage: int = GUELTIG_TAGE

    def ueberfaellig(self, heute: date | None = None) -> bool:
        if self.art != "chance":
            return False
        if self.beleg_datum is None:
            return True
        return (heute or datetime.now(UTC).date()) - self.beleg_datum > _tage(self.gueltig_tage)


def _tage(n: int):
    from datetime import timedelta
    return timedelta(days=n)


# Seit v0.162.0 rechnet der Kontrollarm jedes Lohnt-sich-Gate weiter und
# schreibt in pick_candidate.haette_verworfen, welche Stufe gegriffen
# haette — angewandt wird nichts. Damit steht je Regel ein Feldversuch
# in der Bilanz, statt dass die Auswertung die Filterlogik nachbaut.
_KONTROLLE = ("Bilanz-Abschnitt 'Jede Gate-Stufe einzeln': was aus genau "
              "den Zielen wurde, die diese Stufe verhindert haette "
              "(Kontrollarm, Spalte haette_verworfen) — gegen die Quote "
              "der Kontroll-Anrufe, die kein Gate getroffen haette.")

REGELN: tuple[Regel, ...] = (
    # ------------------------------------------------ Chancen-Regeln
    Regel(
        "snr_floor", "chance",
        "Ziele unter -22 dB lohnen den Anruf nicht (unser eigenes Decode-Limit).",
        "rst_rcvd-Median -10 dB, 90-%-Perzentil -18 dB an dieser Station "
        "(Sebastian). Kein Ausbeute-Vergleich; nie gegen einen Kontrollarm gemessen.",
        date(2026, 5, 22), _KONTROLLE,
    ),
    Regel(
        "kontinent_gate", "chance",
        "Aus Kontinenten mit Abschlussquote unter 5 % nur mit PSK-Beleg anrufen.",
        "Erster Feldbeleg 2026-09-21 aus dem Kontrollarm: Die 60 Ziele, die diese "
        "Stufe verhindert haette, schlossen zu 3,3 % ab — gegen 16,0 % bei den 412 "
        "Kontroll-Anrufen, die kein Gate getroffen haette (z = -2,61). Davor nur "
        "Quoten je Anruf ohne Vergleichsgruppe (08.09.: EU 18 %, AS 7 %, NA 3 %; "
        "Zellen 12.09.: NA mittel 1,3 %).",
        date(2026, 9, 21), _KONTROLLE,
    ),
    Regel(
        "schwach_ohne_psk", "chance",
        "Ziele unter -13 dB nur mit PSK-Beleg; -4 dB Bonus fuer neues DXCC/seltene Ziele.",
        "WIDERLEGT 2026-09-21, zwei Wege: Die 83 Ziele, die die Stufe verhindert "
        "haette, schlossen zu 16,9 % ab — gegen 16,0 % ohne Gate (z = +0,19, "
        "Kontrollarm). Und das A/B ueber 14 Tage: Filter an 165 QSOs aus 1007 "
        "Anrufen, Filter aus 163 aus 941 (z = -0,12). Die Stufe trifft also keine "
        "schlechteren Ziele als der Durchschnitt. Die alten Zahlen (07.09.: unter "
        "-13 dB 3 %, darueber 12 %; Zellen 12.09.: EU schwach ohne PSK 9,0 % gegen "
        "38,6 %) waren Quoten je Anruf ohne Vergleichsgruppe — sie massen mit, dass "
        "schwache Ziele meist alternativlos sind (813-mal gab der Filter nach).",
        date(2026, 9, 21),
        "A/B-Abschnitt 'Schwache Ziele ohne Empfangsbeleg' (QSO-Zahl je Arm, nicht Quote) "
        "und " + _KONTROLLE,
    ),
    Regel(
        "pile_up", "chance",
        "Stationen mit erkanntem Pile-Up nicht anrufen (zu viel Konkurrenz).",
        "keiner — Annahme seit v0.19.0, nie gemessen.",
        None, _KONTROLLE,
    ),
    Regel(
        "einzelner_schwacher_cq", "chance",
        "Ein einzelner Kandidat unter -16 dB ohne Award-/Kontextsignal wird nicht angerufen.",
        "Abschlussquote faellt an der Kante -16/-17 dB (Anruf-Telemetrie, 2026-09-13). "
        "Der Filter war bis 13.09. vom Schwach-Gate abgeschirmt und hat erst seitdem "
        "eine eigene Wirkung; Ausbeute-Vergleich steht aus.",
        date(2026, 9, 13), _KONTROLLE,
    ),
    Regel(
        "fernziel_allein", "chance",
        "Ein einzelner Kandidat ueber 4000 km ohne neues DXCC wird nicht angerufen; CQ ist besser.",
        "Alleingang 10.-12.09. (388 Anrufe): > 4000 km 1 von 77 (1,3 %), < 2000 km 27,3 %; "
        "bei ruhigem Band 0 von 32 gegen 15 von 64 nah. A/B gegen CQ seit v0.110.0.",
        date(2026, 9, 12),
        "A/B-Abschnitt 'Fernziel-Gate' (bringt CQ mehr als der 2-%-Anruf?) und " + _KONTROLLE,
    ),
    Regel(
        "strict_modus", "chance",
        "Nach einer schlechten Serie verlangen alle Routine-Ziele fuer einige Minuten hohe Evidenz.",
        "keiner — nie gemessen, ob eine schlechte Serie die naechsten Anrufe vorhersagt.",
        None, _KONTROLLE,
    ),
    Regel(
        "erwartungswert", "chance",
        "Anrufen nur, wenn P(Erfolg) x Wert je Sekunde den CQ-Ertrag erreicht; hoechster EW gewinnt.",
        "Signalklassen-Kanten aus 60 Tagen Telemetrie (-12/-13 und -17/-18 dB); P geschrumpft "
        "wie die Zellenquoten. Wertfaktoren sind Praeferenz. A/B gegen die Kette seit v0.150.0. "
        "ABGESCHALTET 15.09. nach zwei Tagen: 1,82 QSOs/Std gegen 2,66 im Regelarm, und auch "
        "wertgewichtet hinten (2,28 gegen 3,16 Wert/Std). Belegt im Kandidatenprotokoll: 20 Anrufe "
        "an Ziele mit Wert 3 und im Mittel 9,5 % Chance, davon 1 Erfolg. Der Wertfaktor hebt "
        "unwahrscheinliche Ziele ueber wahrscheinliche. Die erste Erklaerung (falsche p_cq-Messung, "
        "Schwelle 31,5 statt 4,5 %) war richtig, aber nicht die ganze. Tabelle bleibt brauchbar: "
        "kalibriert (10-20 % vorhergesagt, 15,1 % eingetreten).",
        date(2026, 9, 15),
        "Bilanz-Abschnitt 'Erwartungswert gegen Kette' (QSOs je Stunde je Arm) und Kalibrierung "
        "(vorhergesagtes P gegen eingetretene Quote je Klasse).",
    ),
    # ------------------------------------------------ technisch
    Regel(
        "dt_fenster", "technisch",
        "|dt| > 2,5 s: das RX-Fenster der Gegenstation ist vorbei, sie hoert uns nicht.",
        "WSJT-X-Verhalten (Audit-Luecke 1); kein Ausbeute-Beleg noetig.",
        None, "entfaellt (technisch)",
    ),
    Regel(
        "slot_paritaet", "technisch",
        "Wer in unserem Sende-Slot sendet, hoert uns nicht.",
        "FT8-Zeitraster; kein Ausbeute-Beleg noetig.",
        None, "entfaellt (technisch)",
    ),
    Regel(
        "bandrand", "technisch",
        "Audio-Frequenz unterhalb hunt_audio_freq_min_hz wird nicht angerufen.",
        "Gemessen 2026-09-12: 11,9 % gegen 17,0 % bei n=59, z=-1,05 — Rauschen. "
        "Bleibt als Bandgrenze, nicht als Chancen-Regel.",
        date(2026, 9, 12), "entfaellt (technisch); Bilanz-Abschnitt 'Bandrand'",
    ),
    Regel(
        "spaeter_fund", "technisch",
        "Funde der jt9-Stufe (rund 5 s nach Slotbeginn) werden nicht neu angerufen.",
        "2026-09-18: 50 Anrufe an jt9-Ziele, 0 QSOs; schnelle Ziele 27 von 170 "
        "(15,9 %), z = -3,01. Jeder spaete Pick liess einen laufenden Burst "
        "ausfallen. In laufenden QSOs bleibt jt9 wirksam (6 von 27 QSOs).",
        date(2026, 9, 18),
        "entfaellt (technisch); Bilanz-Abschnitt 'Spaete Decodes' zaehlt weiter "
        "die ausgefallenen Aussendungen und die QSO-Schritte, die nur jt9 empfing.",
    ),
    # ------------------------------------------------ Sperren
    Regel(
        "cooldown", "sperre",
        "Nach Abbruch dieselbe Station fuer eine Weile nicht wieder anrufen.",
        "Wiederanruf-Befund fiel von 61,5 % (n=13) auf 20,5 % (n=73) — Rauschen; "
        "Cooldown 60 s nach picked_another bleibt, weil Zielwechsel Zufall ist (2026-09-12).",
        date(2026, 9, 13), "entfaellt (Sperre); Bilanz-Abschnitt 'Umentscheiden'",
    ),
    Regel(
        "soft_blacklist", "sperre",
        "Stationen mit schlechter Reputation werden gemieden.",
        "Betreiberentscheidung; kein Ausbeute-Beleg noetig.",
        None, "entfaellt (Sperre)",
    ),
    Regel(
        "schon_gearbeitet", "sperre",
        "Bereits gearbeitete Stationen ueberspringen (hunt_skip_worked).",
        "Gearbeitet gegen neu: 18,8 % gegen 16,1 %, z=+1,27 — kein Unterschied. "
        "Schalter steht deshalb auf aus.",
        date(2026, 9, 12), "entfaellt (Sperre, abgeschaltet)",
    ),
)


def stufen() -> set[str]:
    return {r.stufe for r in REGELN}


def ueberfaellige(heute: date | None = None) -> list[Regel]:
    h = heute or datetime.now(UTC).date()
    return [r for r in REGELN if r.ueberfaellig(h)]
