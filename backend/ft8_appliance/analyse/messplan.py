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

# Hoechstens so viele A/B-Tests duerfen gleichzeitig laufen. Grund ist die
# Rechnung, nicht die Ordnung: Die Zielgroesse ist QSOs je Stunde, und bei
# rund 2,1 QSOs je Stunde braucht ein Unterschied von 10 % etwa 800 QSOs je
# Arm — 32 Tage bei halber Zeit je Arm. Jeder weitere Arm teilt dieselbe
# Zeit noch einmal. Am 17.09. liefen drei A/B nebeneinander; zwei davon
# endeten nach zwei Wochen mit "Rauschen", weil keiner genug Zeit bekam.
MAX_GLEICHZEITIGE_AB = 2

MESSPLAN: tuple[Messung, ...] = (
    # ------------------------------------------------------------ laufend
    Messung(
        "kontrollarm", "dauerhaft",
        "Bringen die Lohnt-sich-Gates zusammen mehr QSOs je Stunde, als sie kosten?",
        ("pick_attempt.kontroll_arm", "pick_candidate.kontroll_arm",
         "pick_candidate.haette_verworfen", "config:hunt_kontrollarm_anteil"),
        "Kontrollarm: was die Lohnt-sich-Gates insgesamt bringen",
        "Ab 30 Stunden je Arm: urteil_rate auf QSOs je Stunde. 'echt' oder "
        "'Rauschen' (statt 'wahrscheinlich') beendet die Kampagne.",
        danach="Kampagne beendet am 21.09.: Regel 2,15 QSOs/Std (113 h) gegen "
               "Kontrolle 2,22 (42 h), z = -0,2 — Rauschen. hunt_kontrollarm_anteil "
               "zurueck auf 0,1. Der Arm laeuft dauerhaft weiter: Er ist die "
               "Vergleichsgruppe, mit der einzelne Gates ueberhaupt zu belegen sind "
               "(21.09. kontinent_gate belegt, schwach_ohne_psk widerlegt). Liegt die "
               "Kontrolle einmal deutlich vorn: 'Jede Gate-Stufe einzeln' lesen.",
        seit=date(2026, 9, 14),
        quelle="Kampagne 0,3 vom 15. bis 21.09. (Sebastian: QSOs fuer schnellere Klarheit)",
        schalter=("hunt_kontrollarm_anteil",),
    ),
    Messung(
        "antwortfrequenz", "entschieden",
        "Schliessen Antworten auf der Rufer-Frequenz oefter ab als auf einem ruhigen Bin?",
        ("pick_attempt.reply_kind", "config:hunt_reply_ab_test"),
        "Antwortfrequenz: Rufer-Frequenz oder ruhiger Bin? (A/B)",
        "Mantel-Haenszel ueber die SNR-Klassen: p < 0,05 → Gewinner fest "
        "einstellen. Bleibt p darueber, obwohl beide Arme mehr als 2000 Anrufe "
        "haben → als Nicht-Effekt eintragen.",
        lesen_ab=date(2026, 9, 17),
        danach="A/B aus (hunt_reply_ab_test: false), Antworten auf der Rufer-Frequenz "
               "(hunt_reply_quiet_freq: false). Randstationen weichen weiter auf einen "
               "ruhigen Bin aus — dafuer seit 17.09. der eigene Schalter "
               "hunt_reply_edge_dodge, das Ausweichen hing vorher an den beiden.",
        seit=date(2026, 9, 7),
        ergebnis="17.09., 14 Tage: Rufer-Frequenz 19,5 % (n = 1104), ruhiger Bin 15,4 % "
                 "(n = 1057); nach SNR geschichtet OR 1,31, z = +2,34, p = 0,019. Am "
                 "15.09. stand es bei p = 0,059 — mehrfaches Nachsehen erhoeht die "
                 "Irrtumsgefahr etwas; die Richtung war von Beginn an dieselbe.",
        quelle="docs/flags.md 'Antwortstrategie' und 'Antwortfrequenz'",
        schalter=("hunt_reply_ab_test",),
    ),
    Messung(
        "fernziel_gate", "entschieden",
        "Bringt es mehr, aussichtslose Fernziele im Alleingang dem CQ-Ruf zu ueberlassen?",
        ("pick_attempt.fern_gate", "config:hunt_sole_dx_ab"),
        "Fernziel-Gate: bringt CQ-Rufen mehr als ein 2-%-Anruf? (A/B)",
        "Beide Arme bekommen gleich viele 15-Minuten-Bloecke, also ist die QSO-Zahl "
        "je Arm die Ausbeute. z = (a−b)/√(a+b) ≥ 1,96 entscheidet, ab 40 QSOs je Arm.",
        lesen_ab=date(2026, 9, 21),
        danach="A/B aus (hunt_sole_dx_ab: false), Gate bleibt an. Beobachtet wird es "
               "weiter ueber den Kontrollarm (Schattenprotokoll, Stufe fernziel_allein).",
        seit=date(2026, 9, 11),
        ergebnis="17.09., 14 Tage: Gate an 176 QSOs, Gate aus 185, z = +0,47 — kein "
                 "messbarer Unterschied bei ueber 150 QSOs je Arm. Einen Unterschied von "
                 "10 % nachzuweisen braeuchte etwa die vierfache Menge (rund zwei Monate).",
        quelle="docs/flags.md 'hunt_sole_dx_ab'",
        schalter=("hunt_sole_dx_ab",),
    ),
    Messung(
        "schwach_filter_ab", "entschieden",
        "Verwirft der Filter fuer schwache Ziele ohne Empfangsbeleg mehr QSOs, als er Zeit spart?",
        ("pick_attempt.schwach_arm", "pick_candidate.schwach_arm",
         "config:hunt_weak_requires_psk_ab"),
        "Schwache Ziele ohne Empfangsbeleg: lohnt der Anruf? (A/B)",
        "QSO-Zahl je Arm, nicht die Quote. Dieselbe Frage beantwortet seit 16.09. "
        "das Schattenprotokoll des Kontrollarms (Stufe schwach_ohne_psk).",
        lesen_ab=date(2026, 9, 21),
        danach="Filter und A/B aus (hunt_weak_requires_psk: false, "
               "hunt_weak_requires_psk_ab: false). Das Schattenprotokoll des "
               "Kontrollarms sieht weiter zu; ein neuer Beleg muesste den Filter "
               "zurueckholen.",
        seit=date(2026, 9, 14),
        ergebnis="21.09., 14 Tage, zwei Wege: A/B 165 QSOs aus 1007 Anrufen mit "
                 "Filter gegen 163 aus 941 ohne (z = -0,12); Schattenprotokoll: die "
                 "83 verhinderten Ziele schlossen zu 16,9 % ab gegen 16,0 % ohne "
                 "Gate (z = +0,19). Der alte Beleg war eine Quote ohne "
                 "Vergleichsgruppe.",
        quelle="Modellkommentar pick_attempt.schwach_arm; Commit 4f933f3",
        schalter=("hunt_weak_requires_psk_ab",),
    ),
    Messung(
        "spaete_decodes", "entschieden",
        "Bringt die jt9-Stufe unterm Strich QSOs, oder kostet sie mehr Aussendungen?",
        ("decode.stufe", "decode.eingang_s", "pick_attempt.ziel_stufe",
         "config:hunt_skip_late_finds"),
        "Spaete Decodes: was bringt die jt9-Stufe, was kostet sie?",
        "Anrufe mit jt9-Ziel gegen schnelle Ziele (urteil), dazu QSOs mit einem "
        "Schritt, den nur jt9 empfing — gegen die ausgefallenen Aussendungen "
        "aus dem Journal (13.–17.09.: 549, alle nach einem jt9-Decode).",
        lesen_ab=date(2026, 9, 18),
        danach="Seit 18.09. hunt_skip_late_finds: jt9-Funde werden nicht neu angerufen "
               "(Wunschliste ausgenommen), in laufenden QSOs bleibt jt9 wirksam. "
               "Nachsehen, ob die ausgefallenen Aussendungen damit weitgehend "
               "verschwinden; bleiben viele, kommen sie aus laufenden QSOs — dann "
               "'spaeter Schritt erst im naechsten eigenen Slot' pruefen.",
        seit=date(2026, 9, 17),
        ergebnis="18.09., einen Tag: 50 Anrufe an jt9-Ziele, 0 QSOs; schnelle Ziele "
                 "27 von 170 (15,9 %), z = -3,01. Alle 50 waren CQ-Picks. Zugleich "
                 "hatten 6 der 27 QSOs einen Schritt der Gegenstation, den nur jt9 "
                 "empfing. Vor dem Lesetermin 21.09. entschieden, weil 0 von 50 "
                 "gegen 16 % auch mit mehr Daten nicht mehr kippt.",
        quelle="Befund 17.09. aus dem Journal; Entscheidung Sebastian 18.09.",
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
        # 21.09. gelesen und NICHT entscheidbar: nur 75 % der Berichte zugeordnet
        # (Regel verlangt 80), und die oberste Lage hatte null Gelegenheiten. Seit
        # v0.170.2 fragt die Vorhersage 16 statt 10 Empfangsfelder ab, das deckt
        # 91 % der Berichte. Neu lesen, wenn eine Woche damit gelaufen ist.
        lesen_ab=date(2026, 9, 28),
        danach="Liegt die Grenze erkennbar: als Picker-Signal fuer Fernziele pruefen. "
               "Sonst Frage verwerfen und path_prediction nur noch fuer die Karte halten. "
               "Bleibt die Lage 'mehr als 80 % darueber' auch dann ohne Gelegenheit, "
               "ist die Frage auf 20 m gar nicht zu beantworten — dann erst mit einem "
               "zweiten Band wieder aufnehmen.",
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
        "betriebsfenster", "dauerhaft",
        "Zu welcher Tageszeit traegt das Band — und was heisst das fuer die Betriebsart?",
        ("psk_reporter_in",),
        "Wann kommen wir an? Was die Tageszeit fuer die Betriebsart heisst",
        "Je Drei-Stunden-Block: Berichte ueber uns, Hoerer, Anrufe, QSOs. Wenige "
        "Berichte heissen 'Band traegt nicht' — daran aendert keine Zielauswahl "
        "etwas. Viele Berichte ohne Anrufe heissen das Gegenteil: dann ist CQ die "
        "bessere Betriebsart. Ein Block, der dauerhaft unter 5 % abschliesst, "
        "gehoert zur Frage, ob dort ueberhaupt gesendet werden soll.",
        seit=date(2026, 9, 21),
        ergebnis="21.09., 7 Tage: 00-02 UTC 59 Anrufe fuer 2 QSOs (Band zu), "
                 "03-11 UTC durchgehend 22-23 %, danach fallend bis 10 % um "
                 "21-23 UTC — bei 4031 Berichten ueber uns. Abends kommen wir "
                 "an, setzen uns aber nicht durch.",
        quelle="Befund 21.09.: 76 000 Empfangsberichte gingen bis dahin nur auf die Webseite",
    ),
    Messung(
        "swr_verlauf", "laufend",
        "Fruehwarnung: veraendert sich das SWR schleichend?",
        ("swr_log",),
        "Umgebung: erklaert sie, wann es laeuft und wann nicht?",
        "Spanne ueber 0,3 im Zeitraum: 'ANSTIEG PRUEFEN'. Spanne GENAU null "
        "ueber mehr als 20 Messungen: Der Sensor misst nichts — eine Reihe, "
        "die sich nie bewegt, ist keine Entwarnung.",
        # 21.09. gelesen: 1113 Messungen seit dem 12.09., jede exakt 1,00,
        # waehrend das Rig zur selben Zeit 61 W und schwankende ALC meldete.
        # Die Brueckenmessung sitzt hinter der Endstufe; ein Tuner davor
        # meldet immer 1:1. Sebastian sieht beim naechsten Mal am Display
        # des Rigs nach, ob dort ein echter Wert steht.
        lesen_ab=date(2026, 10, 5),
        danach="Steht am Rig auch 1:1, versteckt der Tuner die Antenne — dann "
               "Reihe einstellen, sie kann ihre Frage nicht beantworten. Zeigt "
               "das Display einen echten Wert, liefert ihn nur der CAT-Weg "
               "nicht; dann den Abruf reparieren.",
        seit=date(2026, 9, 12),
        quelle="Befund 21.09.: blinder Sensor, von der Bilanz als 'unauffaellig' gefuehrt",
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
        "dupe_anrufe", "entschieden",
        "Bringen Anrufe an schon gearbeitete Stationen ueberhaupt Abschluesse?",
        ("pick_attempt.was_worked",),
        "Wen anrufen? Was die Ziel-Eigenschaften zum Abschluss beitragen",
        "Quoten je Gruppe mit urteil. 'sicher' zugunsten neuer Stationen wuerde "
        "hunt_skip_worked rechtfertigen; umgekehrt bleibt der Schalter aus.",
        seit=date(2026, 5, 27),
        danach="hunt_skip_worked bleibt aus, jetzt mit Beleg statt mit Bauchgefuehl. "
               "Die Spalte bleibt: Sie trennt in jeder anderen Auswertung die "
               "bewaehrten Pfade von den neuen.",
        ergebnis="21.09., 14 Tage: schon gearbeitet 301 von 1273 (23,6 %), neu 390 von "
                 "2388 (16,3 %), z = +5,39. Wer schon einmal geantwortet hat, antwortet "
                 "wieder — der Pfad ist belegt.",
        quelle="Modellkommentar v0.31.0 (skip_worked-Frage)",
    ),
    Messung(
        "neu_dxcc", "laufend",
        "Schliessen Anrufe an neue DXCC-Gebiete anders ab?",
        ("pick_attempt.was_new_dxcc",),
        "Wen anrufen? Was die Ziel-Eigenschaften zum Abschluss beitragen",
        "Quote je Gruppe mit urteil, dazu die Tier-Tabelle (new_dxcc, new_dxcc_psk, "
        "new_dxcc_band). Faellt die Quote deutlich ab, ist zu entscheiden: Wert "
        "eines neuen Gebiets gegen seine Wahrscheinlichkeit.",
        # 21.09. vorgelegt. Sebastian: erst mehr Daten — eine Saison mit besserer
        # Ausbreitung kann anders aussehen, und der Wert eines neuen Gebiets ist
        # keine Frage der Quote. Bis dahin bleiben die Tiers, wie sie sind.
        lesen_ab=date(2026, 10, 19),
        danach="Entscheidung Sebastian: DXCC-Tiers behalten (ein neues Gebiet ist mehr "
               "wert als ein Routine-QSO), nur mit PSK-Beleg bevorzugen, oder abstufen. "
               "Stand 21.09.: 175 Anrufe, 9 QSOs (5,1 %) gegen 19,6 % sonst, z = -4,76; "
               "das Tier new_dxcc gab 32-mal den Ausschlag fuer 1 QSO.",
        seit=date(2026, 5, 27),
        quelle="Modellkommentar v0.31.0",
    ),
    Messung(
        "bandbelegung", "dauerhaft",
        "Stoergroesse: haengt der Abschluss an der Bandbelegung (Decodes im Slot)?",
        ("pick_attempt.n_decodes",),
        "Woran ein Versuch scheitert: Alter, Bandbelegung, Dauer",
        "Quote je Belegungsklasse und Rangkorrelation. Kein Schalter haengt daran; "
        "die Groesse sagt, ob ein Vergleich zwischen ruhigen und vollen Baendern "
        "zulaessig ist. Kehrt sich das Vorzeichen um, ist jede laufende Messung "
        "daraufhin nachzusehen.",
        seit=date(2026, 5, 27),
        ergebnis="21.09., 14 Tage: unter 5 Decodes 16,0 %, 5-14 20,5 %, 15-29 25,0 %; "
                 "r = +0,06 ueber 3661 Versuche. Ein volles Band ist eher ein gutes "
                 "Zeichen (offene Ausbreitung) als Konkurrenz.",
        quelle="Modellkommentar v0.31.0",
    ),
    Messung(
        "veraltete_picks", "entschieden",
        "Liegt went_silent an veralteten Picks oder an der Gegenstation?",
        ("pick_attempt.pick_age_s",),
        "Woran ein Versuch scheitert: Alter, Bandbelegung, Dauer",
        "Quote und Anteil 'went_silent' je Altersklasse des Decodes beim Pick. "
        "Bleibt eine Klasse mit hohem Alter uebrig, gehoert ein Hoechstalter in "
        "den Picker.",
        seit=date(2026, 8, 30),
        danach="Kein eigener Schalter noetig: hunt_skip_late_finds hat die Ursache "
               "beseitigt. Die Altersklassen bleiben in der Auswertung — taucht "
               "wieder eine aeltere auf, ist etwas zurueckgefallen.",
        ergebnis="21.09., 14 Tage: unter 3 s 21,2 % Abschluss und 30 % stumm, 3-8 s "
                 "3,7 % und 55 % stumm, 8-20 s 4,1 %. Ein Pick aelter als 3 s war "
                 "praktisch aussichtslos. Seit dem jt9-Filter vom 18.09. ist jeder "
                 "der 702 Picks juenger als 3 s — die alten waren jt9-Funde.",
        quelle="Modellkommentar v0.62.0",
    ),
    Messung(
        "wiederholungen", "entschieden",
        "Lohnt ein zweiter CQ- bzw. Report-Ruf — 'nie geantwortet' gegen 'engagiert, dann verloren'?",
        ("pick_attempt.n_resends", "pick_attempt.n_cq_resends",
         "pick_attempt.stale_slots"),
        "Dranbleiben oder aufgeben: Wiederholungen und unsere Lautstaerke",
        "Anteil der Abschluesse, die erst nach einer Wiederholung kamen, und "
        "Wiederholungen je Verlaufsart. Unter 5 % waeren die Wiederholungen "
        "verlorene Sendezeit.",
        seit=date(2026, 8, 30),
        danach="Wiederholungen bleiben. Die Quote je Wiederholungszahl taeuscht — wer "
               "zweimal wiederholt, hatte schon Kontakt; nur der Anteil an allen "
               "Abschluessen zaehlt.",
        ergebnis="21.09., 14 Tage: 173 von 691 Abschluessen (25,0 %) kamen erst nach "
                 "mindestens einer Wiederholung. Wer nie geantwortet hat, bekam im "
                 "Mittel 0,2 Wiederholungen und 7,0 leere Slots; 'engagiert, dann "
                 "verloren' 0,8 und 4,8.",
        quelle="Modellkommentare v0.62.0 und 15.09. (qso_max_cq_resends)",
    ),
    Messung(
        "laut_genug", "entschieden",
        "Sind wir laut genug — erklaert unser eigenes SNR bei der Gegenstation went_silent?",
        ("pick_attempt.psk_snr", "pick_attempt.our_snr_received",
         "pick_attempt.tx_power_w"),
        "Dranbleiben oder aufgeben: Wiederholungen und unsere Lautstaerke",
        "Quote und Anteil 'stumm' je Klasse, dazu Rangkorrelation. Traegt unser "
        "eigenes Signal, waere mehr Leistung oder eine bessere Antenne der Hebel.",
        seit=date(2026, 9, 1),
        danach="our_snr_received taugt nicht als Erklaerung fuer stumme Partner: Die "
               "Spalte gibt es nur, WENN einer geantwortet hat (Ueberlebensfehler). "
               "psk_snr bleibt als Signal, es steht auch ohne Antwort zur Verfuegung. "
               "tx_power_w ist unveraendert (70 W) und zeigt damit nur, dass die "
               "Leistung als Erklaerung ausscheidet.",
        ergebnis="21.09., 14 Tage: our_snr_received r = +0,06 und in allen Klassen 0 % "
                 "stumm — der Ueberlebensfehler. psk_snr dagegen: ab -5 dB 27,6 % "
                 "Abschluss, unter -18 dB 15,5 %, r = +0,11 ueber 1192 Versuche; der "
                 "Anteil stummer Partner bleibt dabei konstant bei 35 %.",
        quelle="Modellkommentar v0.64.0",
    ),
    Messung(
        "entfernung", "entschieden",
        "Haengt der Abschluss an der Entfernung zum Ziel?",
        ("pick_attempt.distance_km", "pick_attempt.target_grid"),
        "Wen anrufen? Was die Ziel-Eigenschaften zum Abschluss beitragen",
        "Quote je Entfernungsklasse und Rangkorrelation, dieselbe Korrelation noch "
        "einmal nur innerhalb Europas. Verschwindet sie dort, wirkt die Entfernung "
        "nur ueber den Kontinent und taugt nicht als eigenes Signal.",
        seit=date(2026, 9, 1),
        danach="Kein eigenes Picker-Signal; die Kontinent- und Zellen-Historie deckt es "
               "ab. Spalte bleibt fuer das Fernziel-Gate und die Karte.",
        ergebnis="21.09., 14 Tage: unter 1000 km 18,3 %, 1000-2500 km 22,9 %, "
                 "2500-4000 km 12,5 %, ueber 4000 km 7,2 %; r = -0,13. Nur innerhalb "
                 "Europas bleibt r = -0,04. Bestaetigt die Tiefenpruefung vom 12.09.",
        quelle="Modellkommentar v0.64.0; Tiefenpruefung 12.09.",
    ),
    Messung(
        "prioritaeten", "dauerhaft",
        "Welche Prioritaetsregel entscheidet den Pick — wirken die Tiers?",
        ("pick_attempt.winning_tier", "pick_attempt.hunt_priority",
         "pick_attempt.was_tailend"),
        "Wen anrufen? Was die Ziel-Eigenschaften zum Abschluss beitragen",
        "Haeufigkeit und Quote je Tier. Ein Tier, das selten den Ausschlag gibt UND "
        "dabei keine bessere Quote hat als der Durchschnitt, ist Ballast. 'sole' ist "
        "kein Tier, sondern die Ansage, dass es nichts zu waehlen gab.",
        seit=date(2026, 9, 1),
        ergebnis="21.09., 14 Tage: 2694 von 3381 Picks (80 %) waren 'sole' — die "
                 "Prioritaeten entscheiden also selten ueberhaupt etwas. Darueber "
                 "psk_snr 37,5 % (n=24), active_hour 30,8 % (39), psk_heard_us 25,7 % "
                 "(74), new_dxcc 3,1 % (32). Tail-End-Ziele gegen den Rest z = +3,34.",
        quelle="Modellkommentar v0.64.0",
    ),
    Messung(
        "qso_dauer", "dauerhaft",
        "Wie lange dauert ein Versuch bis zum Ausgang?",
        ("pick_attempt.qso_duration_s",),
        "Woran ein Versuch scheitert: Alter, Bandbelegung, Dauer",
        "Mittlere und laengste Dauer je Ausgang. Zeit ist die knappe Groesse: Ein "
        "haeufiger Abbruchgrund mit langer Dauer blockiert die Station und gehoert "
        "frueher beendet.",
        seit=date(2026, 9, 1),
        ergebnis="21.09., 14 Tage: went_silent 1319-mal, im Mittel 105 s — zusammen "
                 "rund 38 Stunden. report_never_closed 255-mal mit 158 s ist der "
                 "teuerste Einzelfall, picked_another 1150-mal mit 60 s der billigste. "
                 "Ein Abschluss braucht im Mittel 79 s.",
        quelle="Modellkommentar v0.64.0",
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


def laufende_ab() -> tuple[str, ...]:
    """Schalter der A/B-Tests, die gerade Zeit verbrauchen."""
    return tuple(s for m in MESSPLAN if m.status == "laufend" for s in m.schalter)


def faellige(heute: date | None = None) -> list[Messung]:
    return [m for m in MESSPLAN if m.faellig(heute)]


def abgedeckte_groessen() -> set[str]:
    return {g for m in MESSPLAN for g in m.messgroessen}


def abgedeckte_schalter() -> set[str]:
    return {g.removeprefix("config:") for g in abgedeckte_groessen() if g.startswith("config:")}
