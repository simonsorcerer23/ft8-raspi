"""Pydantic models for the YAML configuration file.

Schema mirrors ``architecture.md`` §7.1. Stays deliberately small — every
field that's optional defaults to a value sane for portable IC-705
operation, so a brand-new config.yaml with just a call sign would boot.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

# Compiled once
_CALLSIGN_RE = re.compile(r"^[A-Z0-9]{1,3}[0-9][A-Z0-9]*[A-Z](/[A-Z0-9]+)?$")
_GRID_RE = re.compile(r"^[A-R]{2}[0-9]{2}([a-x]{2})?$")


# ---------------------------------------------------------------------------
class OperatorConfig(BaseModel):
    """Operator-Profil. Mehrere koennen parallel angelegt sein
    (siehe AppConfig.operators) — der aktive wird ueber active_callsign
    bestimmt. QRZ-Credentials gehoeren ZUM Operator, nicht zur globalen
    Integrationen-Config: ein Pi soll von DK9XR und z.B. DL2XYZ benutzbar
    sein ohne dass beide das gleiche QRZ-Konto teilen muessen.
    """
    model_config = ConfigDict(extra="forbid")

    callsign: str
    default_locator: str | None = None  # if None: take from GPS
    default_power_w: int = Field(default=10, ge=1, le=750)
    # Deutsche Amateurfunk-Lizenzklasse. Default "A" weil das die
    # ursprüngliche Annahme war (DK9XR / Ray hat Klasse A). Für
    # Klasse-E-Operator (Sebastian D03XR) wird das Feld in seiner
    # config.yaml explizit auf "E" gesetzt → Band-Lockout + Power-Cap
    # greifen automatisch. Siehe config/license.py für die Tabellen.
    license_class: Literal["A", "E", "N"] = "A"
    # Per-Operator QRZ-Credentials. Optional — wenn None, faellt der
    # Code auf die globale integrations.qrz-Config zurueck (Backward-
    # Compat). Sebastian 2026-05-23: pro Operator eigenes QRZ-Konto.
    qrz_user: str | None = None
    qrz_password: str | None = None
    qrz_logbook_api_key: str | None = None

    @field_validator("callsign")
    @classmethod
    def _upper_and_valid(cls, v: str) -> str:
        v = v.upper().strip()
        if not _CALLSIGN_RE.match(v):
            raise ValueError(f"invalid callsign: {v!r}")
        return v

    @field_validator("default_locator")
    @classmethod
    def _validate_grid(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if not _GRID_RE.match(v):
            raise ValueError(f"invalid Maidenhead locator: {v!r}")
        return v


# ---------------------------------------------------------------------------
class BandConfig(BaseModel):
    """Ein Funkband mit seiner FT8- + FT4-Dial-Frequenz.

    Bänder sind intrinsisch — die Frequenz hängt nicht von der Antenne
    ab, nur vom Bandplan. Welche Antenne aktuell für ein Band genutzt
    werden kann, wird umgekehrt in AntennaConfig.bands gepflegt.

    FT4 hat eigene Sub-Bänder pro Band (z.B. 14.080 MHz statt 14.074
    MHz auf 20m). Sebastian-Bug v0.4.2: ohne dedizierte FT4-Dial-Freq
    war der Mode-Switch im UI nur halb-wirksam — Pi blieb auf der
    FT8-Frequenz und der FT4-Decoder fand nichts.
    """
    model_config = ConfigDict(extra="forbid")

    name: str
    freq_khz: int = Field(ge=1_800, le=148_000)  # FT8-Dial; HF + 6m + 2m falls IC-9700
    # FT4-Sub-Band-Dial. None = nicht konfiguriert → fallback auf freq_khz
    # (= FT8-Dial). Wenn der Pi auf FT4 geschaltet ist und das Band einen
    # ft4-Wert hat, springt das Rig automatisch auf diese Frequenz.
    freq_khz_ft4: int | None = Field(default=None, ge=1_800, le=148_000)
    # Alt-Feld (Migrations-Kompat): wurde in alten Configs gepflegt,
    # ist heute deprecated. Wird ignoriert, BandConfig braucht keine
    # Antennen-Zuordnung mehr. Lass null/auto-stripped damit alte
    # YAMLs nicht durchfallen.
    antenna: str | None = None

    def freq_for_mode(self, mode: str) -> int:
        """Liefert die korrekte Dial-Freq je nach Mode.

        Sebastian-Audit v0.4.2: FT4 hat pro Band eigene Sub-Bänder.
        Resolutions-Reihenfolge fuer Mode='FT4':
        1. ``freq_khz_ft4`` aus Config (User-Override)
        2. ``FT4_DEFAULT_DIALS[self.name]`` (Standard-Bandplan)
        3. ``freq_khz`` (FT8-Fallback, wenn Band unbekannt)
        Fuer Mode='FT8' immer ``freq_khz``.
        """
        if mode == "FT4":
            if self.freq_khz_ft4 is not None:
                return self.freq_khz_ft4
            default_ft4 = FT4_DEFAULT_DIALS.get(self.name)
            if default_ft4 is not None:
                return default_ft4
        return self.freq_khz


# Standard-FT4-Sub-Band-Defaults pro Band (in kHz). Werden in der Config
# nicht zwingend gepflegt — wenn fehlt, faellt freq_for_mode() auf
# freq_khz (FT8-Dial) zurueck. Quelle: WSJT-X-Defaults / IARU-Bandplan.
FT4_DEFAULT_DIALS: dict[str, int] = {
    "160m": 1_840,
    "80m":  3_575,
    "60m":  5_357,    # FT4 auf 60m selten benutzt, aber dokumentiert
    "40m":  7_047,    # 7.0475 MHz (.5 kHz Offset zur Standard-Notation)
    "30m": 10_140,
    "20m": 14_080,
    "17m": 18_104,
    "15m": 21_140,
    "12m": 24_919,
    "10m": 28_180,
    "6m":  50_318,
    "2m": 144_170,
}


class AntennaConfig(BaseModel):
    """Antenne mit ihrer Liste abgedeckter Band-Namen.

    Die Bänder sind Strings, die auf BandConfig.name verweisen. Im UI
    wird eine Multi-Select-Dropdown daraus gerendert — keine Freitext-
    Eingabe (Tippfehler = Antenne wäre für ein nicht definiertes Band).
    """
    model_config = ConfigDict(extra="forbid")

    name: str
    bands: list[str] = Field(min_length=1)


# ---------------------------------------------------------------------------
class OperatingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Digital-mode the appliance runs. FT8 is the 15-s slot default;
    # FT4 cuts slots to 7.5 s with a different tone count + spacing
    # (4-FSK, 105 symbols, ~20.83 Hz spacing). Switching this changes
    # the slot clock, decoder and TX synth in lock-step.
    mode: Literal["FT8", "FT4"] = "FT8"
    # Directed CQ (Sebastian Audit F7, v0.3.4): wenn gesetzt, sendet der
    # CQ-Loop "CQ <target> <call> <grid>" statt nur "CQ <call> <grid>".
    # Standard-Targets: DX (nur DX-Stationen), EU/NA/SA/AS/AF/OC (Kontinent),
    # POTA/SOTA (Park-Activator), TEST (Contest). Leer = klassischer CQ.
    # WSJT-X-Spec erlaubt 1-4 alphanumerische Zeichen.
    cq_directed: str = Field(default="", max_length=4, pattern=r"^[A-Z0-9]*$")
    # v0.6.0 Anti-WSJT-X-Audit Phase B: Decoder-Mode-Wahl.
    #   "standard" = osr=2/2, LDPC=25 (Default, schnellste, Pi-4-tauglich)
    #   "deep"     = osr=4/4, LDPC=50 (JTDX-Deep-Aequivalent, mehr Schwach-
    #                 Signal-Decodes, 1.5-2x langsamer)
    #   "multi"    = Pass1 standard + Pass2 deep, dedupe (maximum yield,
    #                 2-2.5x langsamer als Standard, Pi 5 empfohlen)
    # CPU-adaptive: bei wiederholten Late-Slots faellt der Pipeline-
    # Watchdog automatisch auf "standard" zurueck (Phase A1 misst Timing).
    # v0.6.1: Default "multi" (Sebastian-Entscheidung — Pi 5 verkraftet
    # 2-2.5x CPU locker, maximaler Yield als Standard). Wer auf
    # schwaecherer Hardware (Pi 4) deployt kann manuell zurueck.
    # v0.7.0 erweitert: "extreme" = Subtract-and-Rerun + Hint-Pass.
    # Pi-5-Mode mit ~3x Standard-CPU, JTDX-Niveau. CPU-Adaptive faellt
    # bei Late-Slots auto auf "standard" zurueck.
    # v0.7.1: Default "extreme" — Sebastian-Wunsch + Pi 5 verkraftet's.
    # CPU-Adaptive Fallback bleibt aktiv (Late-Slots → auto-zurueck zu
    # standard) damit der Default auch auf Pi 4 nicht hangs ist.
    decoder_mode: Literal["standard", "deep", "multi", "extreme"] = "extreme"
    # v0.7.0 Build 3: Auto-Notch fuer lokale QRM-Linien. Default True
    # weil's bei sauberer Umgebung 0 Overhead hat (Detector findet keine
    # Peaks → apply_notches ist no-op).
    auto_notch_enabled: bool = True
    auto_cq_interval_s: int = Field(default=30, ge=15, le=300)
    max_ptt_s: int = Field(default=18, ge=15, le=60)
    cq_idle_timeout_min: int = Field(default=10, ge=1)
    swr_max: float = Field(default=2.0, ge=1.0, le=5.0)
    alc_max: int = Field(default=0, ge=0, le=100)
    # Vorwarn-Stufen: ntfy-Push wenn überschritten, aber TX läuft weiter.
    # Erst beim Erreichen von swr_max/alc_max (Hard-Cap) lockt der Guard.
    # Zweck: dem Operator Zeit geben gegenzulenken bevor TX gesperrt wird.
    # swr_warn realistisch für Multibander-EFHW-Antennen: die sitzen
    # typisch bei 1.3-1.6 ohne dass irgendwas kaputt ist. Default 1.7
    # triggert erst wenn's wirklich ungewöhnlich wird (Antennen-Drift,
    # Vereisung, Steckerverbinder lose). Wer eine resonante Mono-Band-
    # Antenne hat darf das gerne auf 1.3 runterstellen.
    swr_warn: float = Field(default=1.7, ge=1.0, le=5.0)
    # alc_warn=30 liegt zwischen "alles ok" (=0 bis 25, ALC-Target-Window)
    # und "TX-Lock droht" (=50, alc_max). Der ALC-Closed-Loop dreht in
    # diesem Bereich schon selbst nach unten, der Warn-Push ist nur
    # zusätzliche Sichtbarkeit falls der Loop nicht schnell genug ist.
    alc_warn: int = Field(default=30, ge=0, le=100)
    # Veraltet: wurde nie irgendwo gelesen. Erbe einer früheren
    # Design-Iteration. Bleibt im Schema als Optional damit alte
    # YAMLs nicht durchfallen, neuer Code ignoriert ihn.
    answer_only_my_call: bool | None = None
    # ALC closed-loop: starting audio amplitude (0.0..1.0) for TX synth.
    # The orchestrator continuously trims this based on observed ALC
    # readings during TX — kept in config so we boot near the same gain
    # we landed on at shutdown. Architecture §6.x ALC closed-loop.
    audio_gain: float = Field(default=0.9, ge=0.05, le=1.0)
    # ALC target window for the closed loop. We aim to keep the rig's
    # reported ALC between *alc_target_low* and *alc_target_high* (each
    # expressed as 0..100). Going above high → ease off the gain;
    # going below low → bring it up a notch.
    alc_target_low: int = Field(default=5, ge=0, le=100)
    alc_target_high: int = Field(default=25, ge=0, le=100)
    # PI-Regler-Parameter (ALC → audio_gain, mit pwr_meter-Fallback).
    # Sebastian und Claude haben am 2026-05-22 den Bang-Bang-Regler
    # auf ALC durch einen PI ersetzt. Zuerst war pwr_meter als PV
    # gewaehlt, weil ALC mehrdeutig schien (peak=0% sweet-spot vs
    # underdrive). Live-Messung zeigte aber: bei diesem Rig/Antennen-
    # Setup bewegt sich pwr_meter im Sweet-Spot-Bereich (gain 0.25 ..
    # 0.35) nur 0.43..0.45 → Streckenverstaerkung ~0.5. ALC im selben
    # Bereich wandert von 3 % auf 35 % → Verstaerkung ~3.2, sechsmal
    # empfindlicher und monoton. ALC ist regelungstechnisch der bessere
    # Sensor — wir reglen jetzt auf alc_target_pct mit Setpoint mittig
    # im Soll-Fenster [low, high]. Bei alc_peak == 0 schaltet der
    # Regler in den PWR-Fallback (Underdrive-Erkennung via pwr_meter).
    #
    # alc_target_pct: Sollwert fuer den ALC-Hauptregler. Default 15
    # liegt mittig in [5, 25] und entspricht "leicht moduliert, gut
    # gehoerter Pegel, weit weg von Splatter".
    alc_target_pct: int = Field(default=15, ge=5, le=30)
    # alc_deadband_pct: ±-Fenster um den Sollwert in dem KEIN Update
    # ausgeloest wird. Default 5 → System bleibt still wenn ALC in
    # [10, 20] liegt, was bei FT8 voellig akzeptabel ist.
    alc_deadband_pct: int = Field(default=5, ge=1, le=20)
    # Wenn ALC == 0 ist die Frage: Sweet-Spot oder Underdrive? Wir
    # benutzen pwr_meter zur Disambiguation. Wenn pwr_meter >=
    # pwr_target_ratio * pwr_norm, dann ist's Sweet-Spot (kein Update);
    # wenn drunter, dann Underdrive (PWR-Regime, gain hoch).
    # Default 0.80 = 80 % rated power als Underdrive-Schwelle: drunter
    # sind wir definitiv noch nicht am Arbeitspunkt.
    pwr_target_ratio: float = Field(default=0.80, ge=0.5, le=0.95)
    # PI-Verstaerkungen (ALC-Regime: error ≈ alc_target/100 − alc_peak/100,
    # also Werte zwischen -0.4 und +0.15). Kp=0.2 ergibt bei error +0.15
    # einen Up-Step von Δu=0.03 (3 % gain). Ki=0.02 baut den I-Anteil
    # langsam auf damit stationaer keine groesseren Sprunge entstehen.
    gain_loop_kp: float = Field(default=0.2, ge=0.0, le=2.0)
    gain_loop_ki: float = Field(default=0.02, ge=0.0, le=1.0)
    # Safety-Overlay (unabhaengig vom PI): ALC-Watchdog. Wenn der
    # Burst-Peak diese Schwelle ueberschreitet, Notabschaltung:
    # gain ×= alc_safety_factor, Integrator reset. Tritt bei
    # Stoerungen (SWR-Spike, Setting-Sprung am Rig) ein und bringt
    # uns sofort aus der Splatter-Zone raus.
    alc_safety_threshold: int = Field(default=40, ge=10, le=80)
    alc_safety_factor: float = Field(default=0.7, ge=0.3, le=0.95)
    # Cooldown nach einem abgeschlossenen QSO. Der Hunting-Picker
    # überspringt Stationen innerhalb dieses Fensters — Beispiel:
    # 30 min Default heißt "selbe Station nicht im selben CQ-Run drei
    # mal anrufen, gleicher Op darf aber morgen wieder dran". 0 = aus.
    qso_cooldown_min: int = Field(default=30, ge=0, le=1440)
    # Wie viele Slots warten wir auf eine Reaktion vom Partner bevor
    # wir abbrechen? FT8 hat 15-s-Slots, also 6 Slots ≈ 90 s. Wir
    # senden in QSO_RESPOND re-tx wenn der Partner nochmal CQ ruft,
    # also entspricht das in der Praxis bis zu 3 Wiederholungen
    # unserer Grid-Antwort. Höher = geduldiger, niedriger = aggressiver
    # zum nächsten Op.
    qso_max_stale_slots: int = Field(default=6, ge=2, le=20)
    # Re-Send-Limit fuer "repeated CQ": wenn die Partnerstation N-mal
    # ihre CQ wiederholt waehrend wir antworten, ist sie uns offensicht-
    # lich nicht zu hoeren. Statt unendlich nachzusenden bailen wir nach
    # max_cq_resends und setzen den Call in den Failed-Cooldown.
    # Sebastian sah 2026-05-22: SV9TLU ignorierte uns 12x in 2h, jeder
    # Versuch verschwendete 15s Sendezeit (= 3 min Total).
    qso_max_cq_resends: int = Field(default=2, ge=0, le=10)
    # Wie oft duerfen wir unsere R-Report wiederholen wenn der Partner
    # statt RR73 nochmal seinen Report schickt (= er hat unsere R-Report
    # nicht decodiert). WSJT-X-Verhalten: 1× resend ist Default.
    # Sebastian 2026-05-24 nach UN7JO-QSO-Verlust auf 15m.
    qso_max_report_resends: int = Field(default=1, ge=0, le=3)
    # Failed-Attempt-Cooldown: wenn ein QSO-Versuch erfolglos endet
    # (timeout / picked-other / re-send-limit erreicht), wird der
    # Partnercall fuer diese Zeit (in Minuten) vom Hunting-Picker
    # uebersprungen. So vermeidet der Loop dass dieselbe Station im
    # naechsten Slot direkt wieder angerufen wird, wenn sie weiter CQ
    # ruft aber uns nicht hoert. 0 = aus.
    qso_failed_cooldown_min: int = Field(default=15, ge=0, le=120)
    # SNR-Floor fuer den Hunting-Picker: Stationen die mit einem Decode-
    # SNR unter diesem Schwellwert (in dB) ankommen werden uebersprungen.
    # Hintergrund (Sebastian 2026-05-22): die historische QSO-DB zeigt
    # rst_rcvd-Median -10 dB und 90%-Perzentil ~-18 dB — Stationen die
    # uns mit -22 dB oder schlechter empfangen, koennen wir wahrschein-
    # lich nicht erreichen (Reichweite ist asymmetrisch wenn das andere
    # Ende mit weniger Power oder schlechterer Antenne arbeitet, aber
    # selbst bei symmetrischer Reichweite ist -22 dB unser eigenes
    # Decode-Limit). Default -22 = nur die schwaechsten paar Prozent
    # filtern; -100 = Filter praktisch aus.
    hunt_snr_floor_db: int = Field(default=-22, ge=-30, le=0)
    # Audio-Frequenz-Filter: Stationen die mit Decode-Frequenz ausserhalb
    # des sicheren Rig-Bandpass-Bereichs ankommen werden uebersprungen.
    # Beim IC-7300 (PKTUSB) ist der Audio-Bandpass typisch 300..2700 Hz —
    # unterhalb 300 Hz und oberhalb 2700 Hz wird das Sendesignal stark
    # gedaempft. Sebastian sah 2026-05-22: ein Reply auf 262 Hz produzierte
    # so wenig Output (Audio im Sperrbereich) dass der PI im PWR-Regime
    # gain hochkurbelte → bei 0.44 kam genug durch dass ALC auf 54 %
    # einschlug, Watchdog cuttete. Mit Filter wird die Station gar nicht
    # erst angerufen.
    hunt_audio_freq_min_hz: int = Field(default=400, ge=100, le=1500)
    hunt_audio_freq_max_hz: int = Field(default=2600, ge=1500, le=3000)
    # CQ-TX-Slot-Parity: in welcher Slot-Haelfte (even=00/30s, odd=15/45s)
    # senden wir CQ — der andere Slot bleibt frei zum Empfang von Antworten.
    # Sebastian sah 2026-05-23 dass ohne diese Begrenzung CQ in JEDEM
    # Slot gesendet wurde → kein RX-Pfad → 34 min Funkstille mit
    # 0 Decodes obwohl der Decoder lief.
    # WSJT-X-Konvention: CQ-Rufer in "even" (00, 30), Antworter in "odd".
    # Default "even" passt fuer Deutschland und 99 % der Setups.
    cq_tx_slot_parity: Literal["even", "odd"] = "even"
    # PWR-Regime Rate-Limiter: maximale Stellgroessen-Aenderung pro Burst
    # im Underdrive-Regime. Verhindert dass eine Mini-Sequenz von Bursts
    # mit niedrigem pwr_meter (z.B. wegen Audio-Bandpass-Effekten oder
    # SWR-Spike) zu einem schnellen gain-Hochlauf fuehrt der dann beim
    # naechsten Burst in ALC-Overshoot resultiert. Default 0.03 = 3 %
    # Maximum-Gain-Bewegung pro Burst → Watchdog hat 5+ Slots Zeit zu
    # greifen bevor gain einen problematischen Wert erreicht.
    pwr_regime_max_delta: float = Field(default=0.03, ge=0.01, le=0.2)
    # Welcher Modus nach Service-Restart aktiv ist. "off" = beide aus,
    # "hunt" = Antworten/Hunting, "cq" = aktiv CQ rufen. Wird vom
    # Orchestrator beim toggle automatisch aktualisiert damit der Pi
    # nach einem Strom-Wackler weitermacht wo er aufgehört hat.
    boot_mode: Literal["off", "cq", "hunt"] = "off"
    # Hunting-Filter (greifen wenn auto_answer aktiv ist):
    # * skip_worked: ignoriere alle Calls die schon je in der QSO-Tabelle stehen
    # * dxcc_only: ignoriere alle CQ-Rufer aus Ländern die wir schon haben
    # Beide sind exklusiv konfigurierbar; dxcc_only ist die strengere Variante
    # (Award-Hunter-Modus).
    hunt_skip_worked: bool = False
    hunt_dxcc_only: bool = False
    # v0.10.0 Hunt-Priority-Tiers (Sebastian-Wunsch):
    # Mehrstufige Priorisierung beim Picker statt nur "DXCC zuerst, dann SNR".
    # Reihenfolge der Liste = Reihenfolge der Tiers (top-priority zuerst).
    # Mögliche Tier-Namen siehe statemachine.machine.HUNT_TIERS — beliebige
    # Permutation erlaubt. Unbekannte Namen werden im Picker ignoriert
    # (defensiv — wenn jemand einen Tier-Namen tippt, kracht's nicht).
    # Default-Reihenfolge ist Sebastian's "was am meisten Sinn macht"-Vote.
    hunt_priority: list[str] = Field(
        default_factory=lambda: [
            "not_bad_reputation",  # v0.15.0 — Soft-Blacklist (filter)
            "not_his_tx_slot",     # v0.15.0 — Slot-Parity-Awareness (filter)
            "not_in_pileup",       # v0.19.0 — Pile-Up-Avoidance (filter)
            "marine_psk",        # Marinefunker + PSK sagt "hört uns"
            "marine",            # Marinefunker (auch ohne PSK)
            "tail_end_target",   # v0.11.0 — Station hat gerade QSO beendet
            "grayline",          # v0.14.0 — CQ-Rufer in eigenem Grayline-Fenster
            "band_open",         # v0.14.0 — hamqsl: Band aktuell "Good"
            "active_hour",       # v0.16.0 — DB-History sagt: Continent jetzt aktiv
            "buddy_seen",        # v0.17.0 — Worked auf anderem Band (RX-Pfad bekannt)
            "new_dxcc_psk",      # neues DXCC + PSK sagt "hört uns"
            "new_dxcc",          # neues DXCC (auch ohne PSK)
            "psk_heard_us",      # PSK sagt "hört uns" (für routine-EU)
            "new_dxcc_band",     # 5BWAS — neues Band für DXCC
            "new_grid",          # neues Maidenhead-Grid (VUCC-Award)
            "new_grid_band",     # neues Grid auf diesem Band (VUCC-Band)
            "not_worked",        # nie gearbeitet überhaupt
            "dxcc_rarity",       # rare DXCC-Bonus
            "snr",               # Tie-Breaker — bestes Signal
        ]
    )

    @field_validator("hunt_priority", mode="after")
    @classmethod
    def _migrate_missing_tiers(cls, v: list[str]) -> list[str]:
        """Auto-Migration: wenn neue Tier-Namen im Code dazukommen, ergänze
        sie zur User-Liste — sonst sieht Sebastian sie nach Update nicht.

        Sebastian-Feedback v0.10.2: "die Grids fehlen noch" — er hatte
        die alte 9er-Liste in seiner config.yaml, neue Tiers wurden bei
        Pydantic-Load nicht gemerged.

        Strategie: fehlende known-Tiers werden VOR 'snr' eingefügt damit
        der Tie-Breaker unten bleibt. Wenn 'snr' nicht in der Liste ist,
        werden sie ans Ende angehängt. Unbekannte Namen die der User hat
        bleiben drin (defensive forward-compat).
        """
        # Synchron mit statemachine.machine.HUNT_TIERS — der Test
        # test_hunt_tiers_registry_complete erzwingt das.
        known = [
            "not_bad_reputation", "not_his_tx_slot", "not_in_pileup",
            "marine_psk", "marine", "tail_end_target",
            "grayline", "band_open", "active_hour", "buddy_seen",
            "new_dxcc_psk", "new_dxcc", "psk_heard_us", "new_dxcc_band",
            "new_grid", "new_grid_band", "not_worked", "dxcc_rarity", "snr",
        ]
        if not v:
            return list(known)  # leere Liste → komplette Default rein
        existing = list(v)
        missing = [t for t in known if t not in existing]
        if not missing:
            return existing
        # Vor 'snr' einfügen falls vorhanden, sonst hinten anhängen
        try:
            snr_idx = existing.index("snr")
            return existing[:snr_idx] + missing + existing[snr_idx:]
        except ValueError:
            return existing + missing

    # v0.19.2 — DXpedition-Push-Verhalten (NG3K-Auto-Import).
    # Manuelle Watchlist-Eintraege (vom User explizit hinzugefuegt)
    # behalten ihren 1h-Throttle. NG3K-Auto-Eintraege haben:
    # - eigenen On/Off-Schalter
    # - Throttle 24h pro Call (statt 1h) — kein Spam-Risiko
    # - Rarity-Gate: nur DXpeditions mit rarity_score >= Schwellwert
    #   pushen. So bleibt Routine-DX wie Galapagos (~30) still in der
    #   Watchlist (Decoder-Hint-Boost trotzdem aktiv), nur echte
    #   rare-Sachen (P5, 3Y, BS7H, ZL9 etc.) loesen Push aus.
    dxped_ng3k_push_enabled: bool = True
    dxped_ng3k_push_min_rarity: int = Field(default=50, ge=0, le=100)

    # v0.11.0 Tail-End-Hunter (Sebastian-Wunsch):
    # Wenn aktiv, markiert die State-Machine bei jedem RR73/RRR/73-Decode
    # den Sender als Tail-End-Candidate (= sein QSO ist beendet, er ist
    # gleich frei wie nach CQ). Picker injiziert dann synthetische CQ-
    # Decodes fuer diese Candidates, wodurch sie vom Tail-End-Tier
    # priorisiert werden koennen. WSJT-X kann das nicht automatisch —
    # etablierte FT8-Praxis von Hand.
    # Default False weil Latenz-sensitiv (Pi 5 empfohlen) und manche
    # Operator das nicht moegen (= "Kapern" eines fremden QSO-Endes).
    tail_end_hunter_enabled: bool = False

    # PSK-Reciprocity-Toggle: wenn aktiv, fetcht der Orchestrator periodisch
    # pskreporter.info um zu wissen welche Stationen uns gerade hören.
    # Die "marine_psk", "new_dxcc_psk" und "psk_heard_us" Tiers brauchen das.
    # Default False — User schaltet aktiv ein wenn er den Pfad nutzen will.
    psk_reciprocity_enabled: bool = False
    # Refresh-Intervall für PSK-Lookup (Sekunden). 10 min ist Server-freundlich.
    psk_reciprocity_refresh_s: int = Field(default=600, ge=120, le=3600)
    # Watchdog: wenn boot_mode != "off" aber state seit X min in IDLE
    # ohne Hunting-Aktivität hängt, schickt der Pi eine ntfy-Push mit
    # Action-Buttons zum Wieder-Aktivieren. 0 = aus.
    mode_watchdog_min: int = Field(default=15, ge=0, le=240)
    # Public-Hostname unter dem der Pi vom Handy via Tailnet erreichbar
    # ist. ntfy-Action-Buttons brauchen eine URL — der Pi muss sie
    # selbst kennen damit er sie ins Action-Header einsetzen kann.
    # Default: "ft8" (Tailnet-MagicDNS-Name). Falls anders einstellen.
    public_hostname: str = "ft8"


# ---------------------------------------------------------------------------
class WifiProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ssid: str
    psk: str | None = None  # None = open WiFi (rare)


class ApFallbackConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ssid: str = "ft8-hochgericht"
    psk: str = "changeme-please"


class NetworkConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    wifi_priority: list[WifiProfile] = Field(default_factory=list)
    ap_fallback: ApFallbackConfig = Field(default_factory=ApFallbackConfig)
    fallback_delay_s: int = Field(default=60, ge=10)


# ---------------------------------------------------------------------------
class QrzConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    user: str | None = None
    password: str | None = None
    # Logbook API key — separate from user/password (those are for XML
    # lookup). Generated in QRZ logbook settings. Without it we still do
    # callsign lookups via XML, but can't auto-upload QSOs.
    logbook_api_key: str | None = None
    # When True, the orchestrator drains unuploaded QSOs in the
    # background once connectivity returns. False = local-only.
    logbook_auto_upload: bool = True


class HamQthConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    user: str | None = None
    password: str | None = None


class PskReporterConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    upload_decodes: bool = True


class HamQslConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True


class BlitzortungConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    alarm_radius_km: int = Field(default=30, ge=1, le=500)


class DxClusterConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    host: str = "dxc.k1ttt.net"
    port: int = 7373


class NtfyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    topic: str | None = None  # ntfy.sh topic name
    server: str = "https://ntfy.sh"


class IntegrationsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    qrz: QrzConfig = Field(default_factory=QrzConfig)
    hamqth: HamQthConfig = Field(default_factory=HamQthConfig)
    psk_reporter: PskReporterConfig = Field(default_factory=PskReporterConfig)
    hamqsl: HamQslConfig = Field(default_factory=HamQslConfig)
    blitzortung: BlitzortungConfig = Field(default_factory=BlitzortungConfig)
    ntfy: NtfyConfig = Field(default_factory=NtfyConfig)
    dx_cluster: DxClusterConfig = Field(default_factory=DxClusterConfig)


# ---------------------------------------------------------------------------
class UiConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    language: Literal["de", "en"] = "de"
    theme: Literal["auto", "light", "dark"] = "auto"


# ---------------------------------------------------------------------------
# Hamlib model IDs and stock max-power-out for each rig we explicitly support.
# Add more here if needed; the Literal in RigConfig is the source of truth for
# what's selectable in the UI.
_RIG_TABLE: dict[str, tuple[int, int]] = {
    # model_id : (hamlib_id, default_max_power_w)
    "ic705":     (3085,  10),
    "ic7300":    (3073, 100),
    "ic9700":    (3081, 100),
    "ic7610":    (3079, 100),
    # QRP Labs QMX/QMX+ — Multibanddigi-Transceiver, max 5W. Hamlib ID
    # 2053 ist seit Hamlib 4.5 verfügbar. Falls die installierte
    # Hamlib-Version zu alt ist, kann hier auf 2014 (Kenwood TS-480)
    # ausgewichen werden — die QMX-Firmware emuliert TS-480 CAT.
    "qmx_plus":  (2053,   5),
}


class RigConfig(BaseModel):
    """Per-rig glue config. Drives both rigctld launch and orchestrator clamps."""

    model_config = ConfigDict(extra="forbid")

    # Friendly identifier; the Hamlib ID and stock max-power are derived from it.
    model: Literal["ic705", "ic7300", "ic9700", "ic7610", "qmx_plus"] = "ic705"

    # Stable serial device path. The IC-705 default works for a single rig
    # connected via its USB-C data port. IC-7300 default also works via USB-B.
    serial_device: str = "/dev/serial/by-id/usb-Icom_Inc._IC-705-if00"

    # CAT baud. Both 705 and 7300 default to 19200 from the factory; can be
    # bumped to 115200 on either side.
    cat_baud: int = Field(default=19200, ge=4800, le=115200)

    # Max TX power in watts. Defaults are derived from the model when unset
    # (10 for IC-705, 100 for the rest); set explicitly to clamp lower than
    # rig capability.
    max_power_w: int | None = Field(default=None, ge=1, le=200)

    # ALSA card name fragment to match against `arecord -L` output.
    # Empty string = auto-pick the first USB audio device that looks like an
    # Icom CODEC. Set explicitly when you have multiple rigs/USB sound cards.
    audio_card_hint: str = ""

    @computed_field  # type: ignore[prop-decorator]
    @property
    def hamlib_id(self) -> int:
        return _RIG_TABLE[self.model][0]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def effective_max_power_w(self) -> int:
        if self.max_power_w is not None:
            return self.max_power_w
        return _RIG_TABLE[self.model][1]


# ---------------------------------------------------------------------------
class AppConfig(BaseModel):
    """Top-level configuration.

    Multi-Operator-Modell (Sebastian 2026-05-23): ``operators`` ist die
    Liste aller bekannten Profile, ``active_callsign`` waehlt welches
    aktuell aktiv ist. Property ``operator`` liefert den aktiven —
    damit bleiben die ~24 bestehenden ``cfg.operator.*``-Zugriffe
    unveraendert (Backward-Compat).

    Alte single-operator-YAMLs werden im Loader transparent in
    ``operators=[op], active_callsign=op.callsign`` umgewandelt.
    """

    model_config = ConfigDict(extra="forbid")

    # Multi-Operator-Felder. Beim Load werden alte Configs (mit nur
    # `operator:`) automatisch in operators=[...] umgewandelt.
    operators: list[OperatorConfig] = Field(default_factory=list)
    active_callsign: str | None = None  # which operator is currently active
    # Auto-Login-Timeout: nach Service-Start wird nach diesen Sekunden
    # der is_default-Operator (falls keiner aktiv) geladen. Frontend-
    # Selector kann den Operator vorher manuell setzen. 0 = sofort.
    operator_auto_login_seconds: int = Field(default=30, ge=0, le=300)
    bands: list[BandConfig] = Field(default_factory=list)
    antennas: list[AntennaConfig] = Field(default_factory=list)
    operating: OperatingConfig = Field(default_factory=OperatingConfig)
    rig: RigConfig = Field(default_factory=RigConfig)
    network: NetworkConfig = Field(default_factory=NetworkConfig)
    integrations: IntegrationsConfig = Field(default_factory=IntegrationsConfig)
    ui: UiConfig = Field(default_factory=UiConfig)

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_operator(cls, data: object) -> object:
        """Backward-Compat fuer alte single-operator-YAMLs UND Test-
        Kwargs der Form ``AppConfig(operator=OperatorConfig(...))``.

        Falls ``operator`` als Skalar/Dict/OperatorConfig uebergeben
        wird aber keine ``operators``-Liste existiert, wandeln wir
        transparent in das Multi-User-Schema um.
        """
        if not isinstance(data, dict):
            return data

        qrz_block = ((data.get("integrations") or {}).get("qrz") or {})

        def _mirror_global_qrz(op_dict: dict) -> None:
            """Globale integrations.qrz.{user,password,key} in den
            Operator spiegeln — aber nur wenn der Operator-Callsign
            zum qrz.user passt (intent-aware) UND der Operator selbst
            keine eigenen Credentials hat. Damit ueberschreiben wir
            nicht versehentlich bewusst null gesetzte Werte."""
            qrz_user = qrz_block.get("user")
            if not qrz_user:
                return
            op_call = (op_dict.get("callsign") or "").upper().strip()
            if op_call != qrz_user.upper().strip():
                return
            if qrz_block.get("user") and not op_dict.get("qrz_user"):
                op_dict["qrz_user"] = qrz_block["user"]
            if qrz_block.get("password") and not op_dict.get("qrz_password"):
                op_dict["qrz_password"] = qrz_block["password"]
            if (qrz_block.get("logbook_api_key")
                    and not op_dict.get("qrz_logbook_api_key")):
                op_dict["qrz_logbook_api_key"] = qrz_block["logbook_api_key"]

        if "operators" in data and data["operators"]:
            # Schon im neuen Schema — operator-Property-Setter koennte
            # in __init__ noch fehlschlagen, also pop falls vorhanden
            data.pop("operator", None)
            # Globale QRZ-Credentials trotzdem in den passenden Operator
            # spiegeln (Sebastian sah am 2026-05-23 dass DK9XR die qrz_user-
            # Credentials verlor, als die alte single-operator-YAML nach
            # einem Save-Cycle in operators[] migriert wurde aber die
            # integrations.qrz weiterhin global standen).
            for op in data["operators"]:
                if isinstance(op, dict):
                    _mirror_global_qrz(op)
            return data
        legacy_op = data.pop("operator", None) if "operator" in data else None
        if not legacy_op:
            return data
        # OperatorConfig-Objekt oder Dict — beides akzeptieren
        if hasattr(legacy_op, "model_dump"):
            legacy_op = legacy_op.model_dump()
        if not isinstance(legacy_op, dict):
            return data
        # Globale QRZ-Credentials in den (frueher single) Operator spiegeln
        _mirror_global_qrz(legacy_op)
        # … aber hier auch ohne Callsign-Match (legacy single-op-Migration)
        if qrz_block.get("user") and not legacy_op.get("qrz_user"):
            legacy_op["qrz_user"] = qrz_block["user"]
        if qrz_block.get("password") and not legacy_op.get("qrz_password"):
            legacy_op["qrz_password"] = qrz_block["password"]
        if qrz_block.get("logbook_api_key") and not legacy_op.get("qrz_logbook_api_key"):
            legacy_op["qrz_logbook_api_key"] = qrz_block["logbook_api_key"]
        data["operators"] = [legacy_op]
        if not data.get("active_callsign"):
            cs = legacy_op.get("callsign")
            if isinstance(cs, str):
                data["active_callsign"] = cs.upper().strip()
        return data

    @field_validator("active_callsign")
    @classmethod
    def _normalise_active_callsign(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return v.upper().strip()

    @computed_field
    @property
    def operator(self) -> OperatorConfig:
        """Aktiver Operator. Kompatibilitaets-Property fuer alten Code-
        Pfad (cfg.operator.callsign etc.) UND fuer JSON-Serialisierung
        (Frontend erwartet cfg.operator weiterhin).

        Falls active_callsign nicht gesetzt oder unbekannt: erster
        Operator wird zurueckgegeben. Wenn operators leer ist, ValueError —
        das ist ein Boot-Konfigurationsfehler den der Wizard/Loader
        verhindern soll.
        """
        if not self.operators:
            raise ValueError(
                "AppConfig hat keine Operators — Konfiguration fehlt."
            )
        if self.active_callsign:
            for op in self.operators:
                if op.callsign == self.active_callsign:
                    return op
        # Fallback: erster (vorzugsweise is_default, sonst index 0)
        return self.operators[0]

    @operator.setter
    def operator(self, value: OperatorConfig) -> None:
        """Setter fuer Backward-Compat — alter Test/Code-Pfad der
        cfg.operator = OperatorConfig(...) macht. Ersetzt den aktiven
        Operator in der Liste."""
        if not self.operators:
            self.operators = [value]
        else:
            # Replace active or first
            for i, op in enumerate(self.operators):
                if op.callsign == self.active_callsign or (
                    self.active_callsign is None and i == 0
                ):
                    self.operators[i] = value
                    self.active_callsign = value.callsign
                    return
            self.operators[0] = value
        self.active_callsign = value.callsign

    @model_validator(mode="after")
    def _ensure_operators_consistent(self) -> "AppConfig":
        """Validierung: wenn operators leer, kein Boot moeglich.
        Wenn active_callsign gesetzt, muss er in operators sein."""
        if not self.operators:
            # Allow empty for tests that construct AppConfig(operator=...)
            # — der operator-Setter pflegt die Liste nach.
            return self
        # Eindeutige Callsigns
        seen: set[str] = set()
        for op in self.operators:
            if op.callsign in seen:
                raise ValueError(
                    f"duplicate callsign in operators: {op.callsign}"
                )
            seen.add(op.callsign)
        # Active-Pointer validieren
        if self.active_callsign and self.active_callsign not in seen:
            raise ValueError(
                f"active_callsign {self.active_callsign!r} not in operators "
                f"({sorted(seen)})"
            )
        if not self.active_callsign:
            # Fallback: erster Operator
            self.active_callsign = self.operators[0].callsign
        return self

    # ------------------------------------------------------------------ helpers
    def antenna_for(self, band_name: str) -> AntennaConfig | None:
        """Erste Antenne deren bands-Liste dieses Band enthält.

        Neue Semantik: Antennen sagen welche Bänder sie können, nicht
        umgekehrt. Wenn mehrere Antennen das Band abdecken, gewinnt
        die erste in der Liste (Operator-Reihenfolge zählt als Prio).
        """
        return next(
            (a for a in self.antennas if band_name in a.bands),
            None,
        )

    def can_tx_on(self, band_name: str) -> bool:
        """TX erlaubt? AND-Verknüpfung aus Antennen-Lockout UND Lizenz-Allowlist.

        Drei Gates:

        1. *Lizenz*: ist das Band für ``operator.license_class`` freigegeben?
        2. *Antenne*: gibt es überhaupt eine Antenne die das Band kann?
        3. (Power-Cap wird separat in ``effective_max_power_w()``
           erzwungen — der Operator darf das Band anrufen, aber der
           Slider darf nicht über den Cap.)
        """
        from .license import is_band_allowed
        if not is_band_allowed(self.operator.license_class, band_name):
            return False
        return self.antenna_for(band_name) is not None

    def effective_max_power_w(self, band_name: str) -> int:
        """Wirkliche TX-Leistungs-Obergrenze für *band_name*.

        MIN aus:

        * Lizenz-Cap (z.B. Klasse E: 100W HF, oder Klasse A auf 60m: 15W)
        * Rig-Hardware-Cap (z.B. QMX+ 5W, IC-7300 100W)

        ``operator.default_power_w`` ist KEIN Cap mehr (Sebastian
        2026-05-24): das war die persistente Initial-Preference und
        wurde durch fruehe Slider-Writes herabgestaucht — Slider blieb
        dann unter dem Lizenz-Max kleben. Jetzt sind die Caps rein
        physisch/legal, der Slider darf bis dahin.

        Wenn das Band für die Klasse gar nicht freigegeben ist, geben
        wir 0 zurück — der Caller wird in ``can_tx_on()`` schon vorher
        abgewiesen, aber die 0 ist die saubere Antwort falls jemand
        das hier direkt aufruft.
        """
        from .license import max_power_for
        license_cap = max_power_for(self.operator.license_class, band_name)
        if license_cap is None:
            return 0
        return min(
            license_cap,
            self.rig.effective_max_power_w,
        )
