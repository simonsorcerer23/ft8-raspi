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


_KONTROLLE = ("Kontrollarm-Abschnitt der Bilanz: QSOs je Stunde Regel gegen "
              "Kontrolle; dann in pick_candidate, was der von dieser Stufe "
              "verworfene Zieltyp in der Kontrolle brachte.")

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
        "Quoten am 2026-09-08: EU 18 %, AS 7 %, NA 3 % (>= 20 Anrufe je Kontinent); "
        "Zellen 2026-09-12: NA mittel 1,3 %. Quote je Anruf, kein Ausbeute-Vergleich.",
        date(2026, 9, 12), _KONTROLLE,
    ),
    Regel(
        "schwach_ohne_psk", "chance",
        "Ziele unter -13 dB nur mit PSK-Beleg; -4 dB Bonus fuer neues DXCC/seltene Ziele.",
        "2026-09-07: unter -13 dB 3 % Abschluss, darueber 12 %. Zellen 2026-09-12: "
        "EU schwach ohne PSK 9,0 %, EU stark mit PSK 38,6 %; PSK-Beleg z=+3,18 "
        "(uebersteht Bonferroni ueber 11 Tests). A/B starr/adaptiv seit v0.143.0.",
        date(2026, 9, 12),
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
        "wie die Zellenquoten. Wertfaktoren sind Praeferenz. A/B gegen die Kette seit v0.150.0.",
        date(2026, 9, 14),
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
