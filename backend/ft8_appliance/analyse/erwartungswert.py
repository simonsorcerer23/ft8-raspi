"""Erwartungswert je Kandidat: P(Erfolg) x Wert, gegen den CQ-Ertrag.

Seit 2026-09-14, als A/B-Arm gegen die Filterkette. Die Kette bestand aus
sieben Ja-Nein-Gates, die je eine Erfolgschance schaetzten und danach
verwarfen — binaer statt graduell, ohne Vergleich mit der Alternative,
und mit dem Wert eines Ziels erst nach der Chance. Hier steht statt
dessen eine Zahl je Kandidat:

    EW = P(Erfolg | Signalklasse, Kontinent, PSK-Beleg) x Wert(Ziel)

und ein Vergleichswert: was ein CQ-Ruf in derselben Zeit bringt. Ein
Kandidat wird angerufen, wenn sein EW je Sekunde den des CQ-Rufs
erreicht; von mehreren gewinnt der hoechste EW.

P kommt aus der eigenen Anruf-Telemetrie (pick_attempt, ausgehende
Anrufe), zum Eltern-Mittel geschrumpft wie die Zellenquoten des
Stunden-Tiers: Eine Zelle mit drei Anrufen entscheidet sonst so hart
wie eine mit dreihundert. Nur Standardbibliothek — die Bilanz rechnet
dieselbe Tabelle auf dem PC nach.
"""
from __future__ import annotations

from dataclasses import dataclass, field

SHRINK_K = 20.0
"""Pseudo-Anrufe, die jede Zelle vom Eltern-Mittel mitbringt."""

KLASSEN: tuple[tuple[str, int], ...] = (("a", -6), ("b", -12), ("c", -17))
"""Signalklassen: a >= -6, b -7..-12, c -13..-17, d <= -18 dB.

Grenzen aus der Telemetrie: Bei -12/-13 faellt die Quote von rund 20 auf
rund 10 %, bei -17/-18 noch einmal auf unter 5 % (60 Tage, 1806 Anrufe).
"""


def snr_klasse(snr_db: int | float | None) -> str:
    if snr_db is None:
        return "b"           # unbekannt: die haeufigste Klasse
    for name, untergrenze in KLASSEN:
        if snr_db >= untergrenze:
            return name
    return "d"


@dataclass(slots=True)
class PTabelle:
    """Geschrumpfte Erfolgswahrscheinlichkeit je (Klasse, Kontinent, PSK)."""

    zellen: dict[tuple[str, str, bool], tuple[int, int]] = field(default_factory=dict)
    klasse_psk: dict[tuple[str, bool], tuple[int, int]] = field(default_factory=dict)
    gesamt: tuple[int, int] = (0, 0)
    n_anrufe: int = 0

    @classmethod
    def aus_anrufen(cls, zeilen) -> PTabelle:
        """``zeilen``: Iterable von (snr_db, kontinent, psk_heard, completed)."""
        t = cls()
        for snr, kont, psk, ok in zeilen:
            k = snr_klasse(snr)
            p = bool(psk)
            o = 1 if ok else 0
            _add(t.zellen, (k, str(kont or "?"), p), o)
            _add(t.klasse_psk, (k, p), o)
            t.gesamt = (t.gesamt[0] + o, t.gesamt[1] + 1)
            t.n_anrufe += 1
        return t

    def schaetze(self, snr_db, kontinent: str | None, psk: bool) -> tuple[float, int]:
        """(P, n der eigenen Zelle). Ohne jede Telemetrie 0.15 — die
        Groessenordnung der Gesamtquote seit September; sobald Daten da
        sind, spielt der Startwert keine Rolle mehr."""
        if self.gesamt[1] == 0:
            return 0.15, 0
        k = snr_klasse(snr_db)
        p_gesamt = self.gesamt[0] / self.gesamt[1]
        kp_k, kp_n = self.klasse_psk.get((k, psk), (0, 0))
        p_kp = (kp_k + SHRINK_K * p_gesamt) / (kp_n + SHRINK_K)
        z_k, z_n = self.zellen.get((k, str(kontinent or "?"), psk), (0, 0))
        return (z_k + SHRINK_K * p_kp) / (z_n + SHRINK_K), z_n


def _add(d: dict, key, ok: int) -> None:
    k, n = d.get(key, (0, 0))
    d[key] = (k + ok, n + 1)


@dataclass(frozen=True, slots=True)
class Wertfaktoren:
    """Wie viel ein Ziel gegenueber Routine (1,0) wert ist — Praeferenz des
    Betreibers, keine Messung. Es zaehlt der hoechste zutreffende Faktor,
    nicht das Produkt: ein neues DXCC auf der Wunschliste ist nicht
    fuenfzehnmal so viel wert wie Routine."""
    new_dxcc: float = 3.0
    new_dxcc_band: float = 1.5
    new_grid: float = 1.3
    watchlist: float = 5.0
    rarity: float = 2.0
    rarity_ab: int = 40


def wert(f: Wertfaktoren, *, new_dxcc: bool, new_dxcc_band: bool, new_grid: bool,
         watchlist: bool, rarity: int) -> float:
    kandidaten = [1.0]
    if new_dxcc:
        kandidaten.append(f.new_dxcc)
    if new_dxcc_band:
        kandidaten.append(f.new_dxcc_band)
    if new_grid:
        kandidaten.append(f.new_grid)
    if watchlist:
        kandidaten.append(f.watchlist)
    if rarity >= f.rarity_ab:
        kandidaten.append(f.rarity)
    return max(kandidaten)


def schwelle(p_cq: float, anruf_s: float = 90.0, cq_s: float = 30.0) -> float:
    """Der EW, den ein Anruf mindestens haben muss, um einen CQ-Ruf zu schlagen.

    Beide auf Ertrag je Sekunde gebracht: Ein Anrufversuch kostet im
    Mittel rund 90 s (Erfolg 81 s, Geist 110 s), ein CQ-Ruf mit Hoerslot
    30 s. Anrufen lohnt, wenn P x Wert / anruf_s >= p_cq / cq_s.
    """
    if cq_s <= 0:
        return 0.0
    return max(0.0, p_cq) * anruf_s / cq_s


def p_cq_aus(eingehende_erfolge: int, cq_sekunden: float, cq_s: float = 30.0,
             mindest_rufe: int = 100) -> float | None:
    """ABGESCHALTET seit 2026-09-14, liefert immer None.

    Die Formel war falsch, und der A/B-Test hat es binnen eines halben
    Tages gezeigt. Sie teilte ALLE eingehenden Erfolge durch die Zahl der
    CQ-Rufe — aber die meisten eingehenden Anrufe sind keine Antwort auf
    einen CQ. Wer uns auf dem Band hoert, ruft auch, waehrend wir gerade
    jemand anderen arbeiten oder nur lauschen.

    Die Folge war eindeutig: p_cq sprang vom Startwert 0,015 auf 0,105,
    die Schwelle damit von 4,5 auf 31,5 Prozent. Von 156 bewerteten
    Kandidaten lagen nur 21 darueber, und der Erwartungswert-Arm holte
    1,6 QSOs je Stunde gegen 5,2 im Regelarm.

    Sauber messen liesse sich das nur mit eingehenden Erfolgen, die
    nachweislich in eine CQ-Phase fallen — dafuer muesste das
    Zeitprotokoll feiner aufloesen als tageweise. Bis dahin gilt der
    Konfigwert, und dieser Platzhalter bleibt als Warnung stehen: Eine
    Groesse zu messen, die man nicht sauber messen kann, ist schlechter
    als sie zu setzen.
    """
    return None
