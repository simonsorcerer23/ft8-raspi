"""Messplan: jede Messreihe mit ihrer Frage, Entscheidungsregel und Lesetermin.

Seit 2026-09-17. Das Regelregister haelt fest, womit jede FILTERREGEL belegt
ist. Fuer die Messreihen selbst gab es nichts Vergleichbares: Die Frage
hinter einer Spalte stand bestenfalls im Kommentar des Datenmodells, die
Entscheidungsregel und der Lesetermin nur in privaten Notizen. Die
Bestandsaufnahme am 17.09. ergab:

* 20 von 36 Spalten in ``pick_attempt`` wertete keine Bilanz aus — die
  Frage dahinter stand zwar meist im Modellkommentar, aber niemand las nach.
* Drei A/B-Tests liefen ohne festgelegtes Ende. Der zur Antwortfrequenz
  seit dem 7.09.; sein letztes Ergebnis (p = 0,059) rechnete niemand nach.

Hier steht je Messung, was sie klaeren soll. Ein Test haelt den Plan
deckungsgleich mit dem Code: Jede Spalte von ``pick_attempt``, jeder
A/B-Schalter und jede Telemetrie-Tabelle muss hier vorkommen. Wer eine
Messreihe anlegt, ohne ihre Frage einzutragen, bekommt einen roten Test.

Status:

* ``laufend``      — offene Frage mit Auswertung, Regel und Lesetermin
* ``dauerhaft``    — Dauerbeobachtung ohne Ende; die Regel sagt, welches
                     Zeichen etwas ausloesen wuerde
* ``entschieden``  — beantwortet; ``ergebnis`` sagt womit, ``danach``, ob
                     die Daten weiter geschrieben werden muessen
* ``ohne_auswertung`` — die Daten werden geschrieben und die Frage stand
                     einmal fest, aber niemand wertet sie aus. Entweder eine
                     Auswertung bauen oder das Schreiben einstellen.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime

STATUS = ("laufend", "dauerhaft", "entschieden", "ohne_auswertung")


@dataclass(frozen=True, slots=True)
class Messung:
    schluessel: str
    status: str
    frage: str
    # "tabelle.spalte" oder ganze "tabelle"; Schalter als "config:<name>"
    messgroessen: tuple[str, ...]
    auswertung: str | None           # Ueberschrift in scripts/qso_bilanz.py
    entscheidungsregel: str
    lesen_ab: date | None = None
    danach: str = ""
    seit: date | None = None
    ergebnis: str = ""               # bei "entschieden": womit beantwortet
    quelle: str = ""                 # wo die Frage urspruenglich festgehalten ist
    schalter: tuple[str, ...] = field(default=())

    def faellig(self, heute: date | None = None) -> bool:
        if self.status != "laufend" or self.lesen_ab is None:
            return False
        return (heute or datetime.now(UTC).date()) >= self.lesen_ab


# Spalten, die keine Messfrage tragen, sondern jede Zeile erst verwendbar machen.
KERN_PICK_ATTEMPT = frozenset({
    "id", "ts", "target_call", "target_call_raw", "user_callsign", "band",
    "mode", "outcome", "nachgestempelt", "dt_s",
})

# Tabellen, die nur fuer Auswertungen geschrieben werden. Betriebsdaten wie
# qso, heard, call_reputation oder die QSL-Karten stehen hier nicht.
TELEMETRIE_TABELLEN = frozenset({
    "pick_attempt", "pick_candidate", "state_time_daily", "filter_drop_daily",
    "band_noise", "solar_log", "swr_log", "path_prediction", "psk_reporter_in",
})

_GATE_STUFEN = "Jede Gate-Stufe einzeln"

MESSPLAN: tuple[Messung, ...] = (
    # ------------------------------------------------------------ laufend
    Messung(
        "kontrollarm", "laufend",
        "Bringen die Lohnt-sich-Gates zusammen mehr QSOs je Stunde, als sie kosten?",
        ("pick_attempt.kontroll_arm", "pick_candidate.kontroll_arm",
         "pick_candidate.haette_verworfen", "config:hunt_kontrollarm_anteil"),
        "Kontrollarm: was die Lohnt-sich-Gates insgesamt bringen",
        "Ab 30 Stunden je Arm: urteil_rate auf QSOs je Stunde. 'echt' oder "
        "'Rauschen' (statt 'wahrscheinlich') beendet die Kampagne.",
        lesen_ab=date(2026, 9, 18),
        danach="hunt_kontrollarm_anteil von 0,3 zurueck auf 0,1 (Dauerbetrieb). "
               "Liegt die Kontrolle vorn: im Abschnitt 'Jede Gate-Stufe einzeln' "
               "die schuldige Stufe suchen.",
        seit=date(2026, 9, 14),
        quelle="Kampagne 0,3 seit 15.09. (Sebastian: QSOs fuer schnellere Klarheit)",
        schalter=("hunt_kontrollarm_anteil",),
    ),
    Messung(
        "antwortfrequenz", "laufend",
        "Schliessen Antworten auf der Rufer-Frequenz oefter ab als auf einem ruhigen Bin?",
        ("pick_attempt.reply_kind", "config:hunt_reply_ab_test"),
        "Antwortfrequenz: Rufer-Frequenz oder ruhiger Bin? (A/B)",
        "Mantel-Haenszel ueber die SNR-Klassen: p < 0,05 → Gewinner fest "
        "einstellen. Bleibt p darueber, obwohl beide Arme mehr als 2000 Anrufe "
        "haben → als Nicht-Effekt eintragen.",
        lesen_ab=date(2026, 9, 17),
        danach="A/B abschalten (hunt_reply_ab_test: false), hunt_reply_quiet_freq "
               "auf den Gewinner. Stand 15.09.: Rufer-Frequenz vorn, OR 1,29, p = 0,059.",
        seit=date(2026, 9, 7),
        quelle="docs/flags.md 'Antwortstrategie' und 'Antwortfrequenz'",
        schalter=("hunt_reply_ab_test",),
    ),
    Messung(
        "fernziel_gate", "laufend",
        "Bringt es mehr, aussichtslose Fernziele im Alleingang dem CQ-Ruf zu ueberlassen?",
        ("pick_attempt.fern_gate", "config:hunt_sole_dx_ab"),
        "Fernziel-Gate: bringt CQ-Rufen mehr als ein 2-%-Anruf? (A/B)",
        "Beide Arme bekommen gleich viele 15-Minuten-Bloecke, also ist die QSO-Zahl "
        "je Arm die Ausbeute. z = (a−b)/√(a+b) ≥ 1,96 entscheidet, ab 40 QSOs je Arm.",
        lesen_ab=date(2026, 9, 21),
        danach="Gewinnt 'Gate aus': Gate abschalten. Gewinnt 'Gate an': A/B abschalten, "
               "Gate bleibt. Rauschen bei 150 QSOs je Arm: A/B abschalten, Gate bleibt "
               "(Beleg im Regelregister 'fernziel_allein').",
        seit=date(2026, 9, 11),
        quelle="docs/flags.md 'hunt_sole_dx_ab'",
        schalter=("hunt_sole_dx_ab",),
    ),
    Messung(
        "schwach_filter_ab", "laufend",
        "Verwirft der Filter fuer schwache Ziele ohne Empfangsbeleg mehr QSOs, als er Zeit spart?",
        ("pick_attempt.schwach_arm", "pick_candidate.schwach_arm",
         "config:hunt_weak_requires_psk_ab"),
        "Schwache Ziele ohne Empfangsbeleg: lohnt der Anruf? (A/B)",
        "QSO-Zahl je Arm, nicht die Quote. Dieselbe Frage beantwortet seit 16.09. "
        "das Schattenprotokoll des Kontrollarms (Stufe schwach_ohne_psk).",
        lesen_ab=date(2026, 9, 21),
        danach="Sobald 'Jede Gate-Stufe einzeln' fuer schwach_ohne_psk ein Urteil hat: "
               "A/B abschalten — zwei Messungen fuer eine Frage verduennen beide.",
        seit=date(2026, 9, 14),
        quelle="Modellkommentar pick_attempt.schwach_arm; Commit 4f933f3",
        schalter=("hunt_weak_requires_psk_ab",),
    ),
    Messung(
        "spaete_decodes", "laufend",
        "Bringt die jt9-Stufe unterm Strich QSOs, oder kostet sie mehr Aussendungen?",
        ("decode.stufe", "decode.eingang_s", "pick_attempt.ziel_stufe"),
        "Spaete Decodes: was bringt die jt9-Stufe, was kostet sie?",
        "Anrufe mit jt9-Ziel gegen schnelle Ziele (urteil), dazu QSOs mit einem "
        "Schritt, den nur jt9 empfing — gegen die ausgefallenen Aussendungen "
        "aus dem Journal (13.–17.09.: 549, alle nach einem jt9-Decode).",
        lesen_ab=date(2026, 9, 21),
        danach="Traegt jt9 wenig: als A/B 'spaete Funde erst im naechsten eigenen "
               "Slot'. Traegt es viel: beschleunigen statt einbremsen.",
        seit=date(2026, 9, 17),
        quelle="Befund 17.09. aus dem Journal",
    ),
    Messung(
        "sonnenindizes", "laufend",
        "Erklaeren K-Index oder Sonnenfluss, wann uns mehr Stationen hoeren?",
        ("solar_log",),
        "Umgebung: erklaert sie, wann es laeuft und wann nicht?",
        "Nach Herausrechnen des Tagesgangs: Rangkorrelation je Drei-Stunden-Block "
        "(K) bzw. je Tag (Sonnenfluss, ab 10 vollen Tagen). |t| ≥ 3 = deutlich.",
        lesen_ab=date(2026, 9, 22),
        danach="Deutlich: als Picker-Signal pruefen. Bleibt es bei 'keiner erkennbar' "
               "ueber 30 Tage: Frage verwerfen, solar_log einstellen.",
        seit=date(2026, 9, 12),
        quelle="Bilanz-Abschnitt 'Umgebung' seit 17.09.",
    ),
    Messung(
        "rauschflur", "laufend",
        "Erklaert der Rauschflur, wann Anrufe abschliessen?",
        ("band_noise",),
        "Umgebung: erklaert sie, wann es laeuft und wann nicht?",
        "Korrelation Rauschflur gegen Abschlussquote je Stunde; |r| > 0,4 = Zusammenhang.",
        lesen_ab=date(2026, 10, 12),
        danach="Bleibt 'keiner erkennbar' (17.09.: +0,04 ueber 106 Stunden): Frage "
               "verwerfen, band_noise-Aufzeichnung einstellen.",
        seit=date(2026, 9, 12),
        quelle="Bilanz-Abschnitt 'Umgebung' (Reihen ohne Leser, 12.09.)",
    ),
    Messung(
        "ft8_muf", "laufend",
        "Wie weit ueber die vorhergesagte MUF traegt FT8?",
        ("path_prediction",),
        "FT8-MUF: wie weit ueber die Vorhersage traegt FT8?",
        "Die FT8-MUF liegt dort, wo es Gelegenheiten gab und trotzdem keine Berichte "
        "kamen. Zuordnung der Berichte muss dafuer ueber 80 % liegen.",
        lesen_ab=date(2026, 9, 19),
        danach="Liegt die Grenze erkennbar: als Picker-Signal fuer Fernziele pruefen. "
               "Sonst Frage verwerfen und path_prediction nur noch fuer die Karte halten.",
        seit=date(2026, 9, 12),
        quelle="Tiefenpruefung 12.09. ('FT8-MUF-Frage in ~1 Woche neu')",
    ),
    Messung(
        "bandrand", "laufend",
        "Kosten Stationen knapp ueber der unteren Audiogrenze Abschluesse?",
        ("pick_attempt.freq_offset_hz",),
        "Bandrand: sitzt hunt_audio_freq_min_hz an der richtigen Stelle?",
        "Der Abschnitt nennt die fuer einen Nachweis noetige Fallzahl im Bereich "
        "400–499 Hz. Erst wenn sie erreicht ist, zaehlt das Urteil.",
        lesen_ab=date(2026, 10, 1),
        danach="Echt schlechter: Grenze anheben. Rauschen bei erreichter Fallzahl: "
               "Nicht-Effekt bestaetigt, Abschnitt kann weg.",
        quelle="Gemessene Nicht-Effekte (z = −1,08 bei n = 58)",
    ),
    # ---------------------------------------------------------- dauerhaft
    Messung(
        "zeit_je_zustand", "dauerhaft",
        "Wie viele QSOs bringt eine Stunde Betrieb — der Nenner fuer jeden Filtervergleich.",
        ("state_time_daily",),
        "Zeit je Zustand: QSOs je Stunde, nicht je Anruf",
        "Sinkt QSOs/Std bei steigender Quote je Anruf, wirft ein Filter zu viel weg.",
        seit=date(2026, 9, 14),
    ),
    Messung(
        "filterstufen_historie", "dauerhaft",
        "Greift jede Filterstufe ueberhaupt — oder haengt eine an einem Schalter?",
        ("filter_drop_daily",),
        "Filterstufen ueber die Tage: greift eine Stufe ueberhaupt?",
        "Eine Stufe ohne Treffer ueber Wochen: pruefen, ob sie an einem Schalter "
        "haengt oder hinter einer Bedingung, die nie eintritt.",
        seit=date(2026, 9, 12),
    ),
    Messung(
        "anrufwege_und_ausgang", "dauerhaft",
        "Kommen eingehende Anrufe zum Abschluss, und woran scheitern Versuche?",
        ("pick_attempt.pick_kind", "pick_attempt.bail_reason"),
        "Anrufversuche nach Weg: kommen eingehende Anrufe zum Abschluss?",
        "R-Reports an uns ohne QSO sind die Kennzahl der Sequenz-Loecher; "
        "steigen sie, ist die Zustandsmaschine zu pruefen.",
        seit=date(2026, 9, 10),
    ),
    Messung(
        "empfangsbeleg", "dauerhaft",
        "Traegt der PSK-Beleg (uns hat jemand dort gehoert) den Abschluss?",
        ("pick_attempt.psk_heard_us",),
        "PSK-Datenlage: an welchen Tagen stand die Liste?",
        "0 % Beleganteil an einem Tag heisst Liste leer, nicht niemand hoert uns — "
        "dann ist der PSK-Abruf zu pruefen, bevor irgendein Gate beurteilt wird.",
        seit=date(2026, 5, 30),
        quelle="Modelldocstring pick_attempt (v0.30.0)",
    ),
    Messung(
        "auswahl", "dauerhaft",
        "Hatte der Picker ueberhaupt eine Wahl — sonst wirken Prioritaeten nicht.",
        ("pick_attempt.n_candidates",),
        "Hatte der Picker eine Wahl? (entscheidet, ob Tiers ueberhaupt wirken)",
        "Solange die meisten Anrufe nur einen Kandidaten haben (17.09.: 761 von 901), "
        "ist der Engpass der Empfang, nicht die Reihenfolge.",
        seit=date(2026, 6, 1),
    ),
    Messung(
        "signalstaerke", "dauerhaft",
        "Wie stark haengt der Abschluss an der Signalstaerke — Beleg fuer das Schwach-Gate.",
        ("pick_attempt.snr_db",),
        "Abschluss nach Signalstaerke — traegt das Schwach-Gate?",
        "Schrumpft der Abstand zwischen stark und schwach, verliert das Gate seinen Beleg.",
        seit=date(2026, 9, 7),
    ),
    Messung(
        "wartezeit", "dauerhaft",
        "Wie lange schweigt ein Partner, der doch noch antwortet?",
        ("decode",),
        "Wartezeit: wie lange schweigt ein Partner, der doch noch antwortet?",
        "Verlust bei der aktuellen Grenze (6 Slots) ueber 5 %: Grenze pruefen. "
        "Kuerzen auf 3 kostete 30 %.",
        seit=date(2026, 9, 14),
    ),
    Messung(
        "umentscheiden", "dauerhaft",
        "Lohnt der Wechsel zu einem anderen Ziel, oder ist er Zufall?",
        ("pick_attempt.bail_reason",),
        "Umentscheiden: lohnt der Wechsel zu einem anderen Ziel?",
        "Schliesst das neue Ziel nicht besser ab als ein Wiederanruf des alten, "
        "bleibt der 60-s-Cooldown nach picked_another.",
        seit=date(2026, 9, 12),
        quelle="Tiefenpruefung 12.09. (Ziel-Wechsel ist Zufall)",
    ),
    Messung(
        "ankommen", "dauerhaft",
        "Kommen wir an — wer hoert uns wo, wie laut?",
        ("psk_reporter_in",),
        "Kommen wir an? Empfangsberichte ueber unser eigenes Signal",
        "Die einzige direkte Aussage ueber unser Signal. Bricht die Berichtszahl bei "
        "gleichem Betrieb ein: Antenne, Leistung, SWR pruefen.",
        seit=date(2026, 9, 11),
    ),
    Messung(
        "ausbreitung_oder_konkurrenz", "dauerhaft",
        "Hoeren uns viele Stationen eines Kontinents, ohne dass wir dort abschliessen?",
        ("pick_attempt.continent",),
        "Ausbreitung oder Konkurrenz?",
        "Hohe Berichtszahl bei niedriger Quote heisst: der Weg steht, aber wir setzen "
        "uns im Pile-up nicht durch — dagegen hilft Zielauswahl, nicht Ausbreitung.",
        seit=date(2026, 9, 11),
    ),
    Messung(
        "swr_verlauf", "dauerhaft",
        "Fruehwarnung: veraendert sich das SWR schleichend?",
        ("swr_log",),
        "Umgebung: erklaert sie, wann es laeuft und wann nicht?",
        "Spanne ueber 0,3 im Zeitraum: 'ANSTIEG PRUEFEN'.",
        seit=date(2026, 9, 12),
    ),
    Messung(
        "sendeversatz", "dauerhaft",
        "Wie spaet nach der Slotgrenze beginnt unsere Aussendung?",
        ("pick_attempt.tx_offset_s",),
        "Vorab-Decode: was der fruehere Durchgang bringt (A/B)",
        "Median weit ueber 1 s: Decode- oder Sendepfad pruefen (slot_phasen_s).",
        seit=date(2026, 9, 11),
    ),
    # --------------------------------------------------------- entschieden
    Messung(
        "erwartungswert_modell", "entschieden",
        "Waehlt ein Erwartungswert-Modell bessere Ziele als die Filterkette?",
        ("pick_attempt.ew_arm", "pick_candidate.ew_arm", "pick_candidate.p_erfolg",
         "pick_candidate.wert", "pick_candidate.ew", "config:hunt_erwartungswert_ab"),
        "Erwartungswert-Modell: stimmt die Wahrscheinlichkeitstabelle?",
        "",
        danach="Abgeschaltet. p_erfolg bleibt als Kalibrierung der Tabelle nuetzlich; "
               "wer neu anfaengt, senkt zuerst die Wertfaktoren.",
        seit=date(2026, 9, 14),
        ergebnis="15.09.: 1,82 gegen 2,66 QSOs/Std verloren, auch wertgewichtet "
                 "(2,28 gegen 3,16). Die Wertfaktoren hoben unwahrscheinliche Ziele.",
        schalter=("hunt_erwartungswert_ab",),
    ),
    Messung(
        "zellen_prior", "entschieden",
        "Ist die Abschlussquote je (Kontinent, Stunde) besser als die alte Stundenliste?",
        ("pick_attempt.zellen_arm", "config:hunt_zellen_prior_ab"),
        "Stunden-Tier: Zellen-Quote gegen die alte Stundenliste (A/B)",
        "",
        danach="Zellen-Quote ist fest eingestellt. Der Bilanz-Abschnitt zeigt nur noch "
               "eine Zeile und kann weg.",
        seit=date(2026, 9, 12),
        ergebnis="v0.136.0 (12.09.): Zellen-Quote uebernommen, staerkster Praediktor "
                 "(z = +5,25).",
        schalter=("hunt_zellen_prior_ab",),
    ),
    Messung(
        "vorab_decode", "entschieden",
        "Bringt ein Decode vor der Slotgrenze puenktlichere und damit erfolgreichere Antworten?",
        ("pick_attempt.pre_decode", "config:decoder_pre_decode_ab"),
        "Vorab-Decode: was der fruehere Durchgang bringt (A/B)",
        "",
        danach="Abgeschaltet. Die Spalte pre_decode kann weg, tx_offset_s bleibt "
               "(Sendeversatz).",
        seit=date(2026, 9, 11),
        ergebnis="12.09.: Versatz 0,05 statt 0,995 s, aber kein Erfolgsgewinn — "
                 "Puenktlichkeit ist in diesem Bereich keine wirksame Groesse; der "
                 "fruehe Durchgang kostet Auswahl.",
        schalter=("decoder_pre_decode_ab",),
    ),
    Messung(
        "nachbar_beleg", "entschieden",
        "Ersetzt ein Nachbar, der uns hoert, den fehlenden eigenen Empfangsbeleg?",
        ("pick_attempt.psk_heard_us",),
        "Ersatz fuer den fehlenden Empfangsbeleg",
        "",
        danach="Nicht verwenden. Der Abschnitt meldet univariat weiter 'sicher' — "
               "das ist der bekannte Scheinzusammenhang.",
        seit=date(2026, 9, 11),
        ergebnis="12.09.: univariat stark, multivariat z = +0,20 — Stellvertreter "
                 "fuer Kontinent und SNR.",
    ),
    # ----------------------------------------------------- ohne_auswertung
    Messung(
        "dupe_anrufe", "ohne_auswertung",
        "Bringen Anrufe an schon gearbeitete Stationen ueberhaupt Abschluesse?",
        ("pick_attempt.was_worked",),
        None, "", seit=date(2026, 5, 30),
        quelle="Modellkommentar v0.31.0 (skip_worked-Frage)",
    ),
    Messung(
        "neu_dxcc", "ohne_auswertung",
        "Schliessen Anrufe an neue DXCC-Gebiete anders ab?",
        ("pick_attempt.was_new_dxcc",),
        None, "", seit=date(2026, 5, 30), quelle="Modellkommentar v0.31.0",
    ),
    Messung(
        "bandbelegung", "ohne_auswertung",
        "Stoergroesse: haengt der Abschluss an der Bandbelegung (Decodes im Slot)?",
        ("pick_attempt.n_decodes",),
        None, "", seit=date(2026, 5, 30), quelle="Modellkommentar v0.31.0",
    ),
    Messung(
        "veraltete_picks", "ohne_auswertung",
        "Liegt went_silent an veralteten Picks oder an der Gegenstation?",
        ("pick_attempt.pick_age_s",),
        None, "", seit=date(2026, 6, 1), quelle="Modellkommentar v0.62.0",
    ),
    Messung(
        "wiederholungen", "ohne_auswertung",
        "Lohnt ein zweiter CQ- bzw. Report-Ruf — 'nie geantwortet' gegen 'engagiert, dann verloren'?",
        ("pick_attempt.n_resends", "pick_attempt.n_cq_resends", "pick_attempt.stale_slots"),
        None, "", seit=date(2026, 6, 1),
        quelle="Modellkommentare v0.62.0 und 15.09. (qso_max_cq_resends)",
    ),
    Messung(
        "laut_genug", "ohne_auswertung",
        "Sind wir laut genug — erklaert unser eigenes SNR bei der Gegenstation went_silent?",
        ("pick_attempt.psk_snr", "pick_attempt.our_snr_received", "pick_attempt.tx_power_w"),
        None, "", seit=date(2026, 6, 1), quelle="Modellkommentar v0.64.0",
    ),
    Messung(
        "entfernung", "ohne_auswertung",
        "Haengt der Abschluss an der Entfernung zum Ziel?",
        ("pick_attempt.distance_km", "pick_attempt.target_grid"),
        None, "", seit=date(2026, 6, 1),
        quelle="Modellkommentar v0.64.0; in der Tiefenpruefung 12.09. einmalig "
               "genutzt (wirkt nur ueber den Kontinent)",
    ),
    Messung(
        "prioritaeten", "ohne_auswertung",
        "Welche Prioritaetsregel entscheidet den Pick — wirken die Tiers?",
        ("pick_attempt.winning_tier", "pick_attempt.hunt_priority", "pick_attempt.was_tailend"),
        None, "", seit=date(2026, 6, 1), quelle="Modellkommentar v0.64.0",
    ),
    Messung(
        "qso_dauer", "ohne_auswertung",
        "Wie lange dauert ein Versuch bis zum Ausgang?",
        ("pick_attempt.qso_duration_s",),
        None, "", seit=date(2026, 6, 1), quelle="Modellkommentar v0.64.0",
    ),
    Messung(
        "kandidatenprotokoll", "dauerhaft",
        "Welche Kandidaten gab es je Slot, und welcher Filter nahm sie zuerst weg?",
        ("pick_candidate",),
        _GATE_STUFEN,
        "Grundlage des Schattenprotokolls; ohne diese Zeilen gibt es keinen "
        "Feldversuch je Gate-Stufe.",
        seit=date(2026, 9, 14),
    ),
)


def nach_status(status: str) -> list[Messung]:
    return [m for m in MESSPLAN if m.status == status]


def faellige(heute: date | None = None) -> list[Messung]:
    return [m for m in MESSPLAN if m.faellig(heute)]


def abgedeckte_groessen() -> set[str]:
    return {g for m in MESSPLAN for g in m.messgroessen}


def abgedeckte_schalter() -> set[str]:
    return {g.removeprefix("config:") for g in abgedeckte_groessen() if g.startswith("config:")}
