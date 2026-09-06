"""Pre-flight guards executed before every TX transition.

A *Guard* is a callable that, given current observed state, decides
whether the next TX is allowed. Each returns a :class:`GuardResult`
which the state machine collects; the first non-OK result short-circuits
TX (state -> TX_LOCKED) and the reason is surfaced to the UI.

This module is intentionally pure and dependency-free, so unit-tests
can construct any combination of pass/fail scenarios cheaply.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GuardResult:
    ok: bool
    name: str
    # i18n message key + substitution params for the lock reason. The actual
    # localized string is produced at serialize time (browser request lang)
    # or with the config default lang for logs/ntfy — see ft8_appliance.i18n.
    # Kept dependency-free here so this module stays pure/unit-testable.
    code: str | None = None
    params: dict[str, object] | None = None


@dataclass(slots=True)
class HardwareState:
    """Snapshot of the live measurements the guards reason over."""

    gps_fix_mode: int = 3  # 0=no, 2=2D, 3=3D
    # chrony-Offset der Systemuhr in Sekunden. None = chronyc hat nicht
    # geantwortet, also UNBEKANNT — nicht 0,0. Ein erfundener Idealwert
    # war bis 2026-09-06 genau die Luecke, durch die eine frei laufende
    # Uhr als "perfekt synchron" durchging.
    time_offset_s: float | None = 0.0
    swr: float = 1.2
    alc_pct: int = 0
    battery_v: float | None = None  # None = on external power
    # None = Sensor nicht lesbar (kein /sys/class/thermal, Dev-Rechner).
    # Der temp_guard laesst das passieren: der Pi drosselt sich selbst,
    # und ein erfundener Wert waere schlechter als keiner.
    cpu_temp_c: float | None = 50.0
    audio_drift_samples: int = 0
    # Antenna lockout: True if the active antenna covers the current band.
    # Default True so legacy tests don't trip; production wiring sets this
    # every slot.
    antenna_covers_band: bool = True
    # Licence lockout: True if the operator's licence class (and, abroad,
    # CEPT) permits TX on the current band. Computed per slot by the
    # orchestrator via AppConfig.can_tx_on(). Default True for the same
    # reason as above.
    band_allowed_for_license: bool = True
    # chrony ist auf eine Quelle synchronisiert (Stratum < 16). Das ist
    # DIE Zeitquelle fuer den time_guard — nicht der GPS-Fix, siehe dort.
    # Default True, weil die HardwareState-Defaults bewusst "alles gruen"
    # sind; die Produktion setzt das Feld jeden Slot aus chronyc.
    chrony_synced: bool = True
    # True, wenn die Rig-Frequenz innerhalb der Toleranz an einem
    # konfigurierten FT8-/FT4-Dial liegt; False = Rig steht woanders;
    # None = Frequenz unbekannt (dann urteilt der rig_link_guard).
    dial_on_configured_freq: bool | None = None
    # Nur fuer den Sperrgrund des dial_guard (Anzeige in MHz).
    rig_freq_hz: int | None = None
    # Sekunden seit dem letzten BRAUCHBAREN Rig-Snapshot (einer mit
    # Frequenz). None = seit dem Start noch keiner angekommen, also kein
    # Verlust den wir feststellen koennten — siehe rig_link_guard.
    rig_link_age_s: float | None = None


@dataclass(slots=True)
class GuardLimits:
    """Thresholds — wired from :class:`AppConfig` at runtime."""

    swr_max: float = 2.0
    alc_max: int = 0
    # 0 = Guard aus (gleiche Semantik wie alc_max). Der fruehere Default
    # 12,0 V wurde nie gegen ein Geraet gehalten und passt nicht zum
    # 7,4-V-Akku des IC-705 — siehe battery_guard.
    battery_min_v: float = 0.0
    cpu_temp_max_c: float = 75.0
    audio_drift_warn_samples: int = 5
    audio_drift_fail_samples: int = 50
    time_offset_max_s: float = 0.5
    rig_link_max_age_s: float = 60.0
    dial_tolerance_hz: int = 500


Guard = Callable[[HardwareState, GuardLimits], GuardResult]


# ---------------------------------------------------------------------------
def time_guard(hw: HardwareState, lim: GuardLimits) -> GuardResult:
    """Blocke TX, wenn die Systemuhr nicht nachweislich synchron ist.

    Bis 2026-09-06 reichte ein GPS-Fix ("has_gps or has_chrony"). Die
    Annahme dahinter stand in architecture.md §3.5: GPS → gpsd → chrony
    → Systemuhr. Auf diesem System ist das nicht so — chrony laeuft
    NTP-only (decoder_evolution.md, v0.6.0 D: gpsd 3.25 schreibt nicht
    ins SHM). Ein GPS-Fix sagt damit NICHTS ueber die Systemuhr aus.
    Portabel ohne Internet, Pi ohne RTC, war das genau der Fall, in dem
    der Guard falsch gruen zeigte: chrony ohne Quelle, GPS-Fix da, Uhr
    frei laufend, TX im falschen Slot.

    Darum zaehlt nur noch chrony. Ist chrony auf GPS synchronisiert
    (Refid "GPS"), ist das automatisch abgedeckt — dann meldet chrony
    "synchron". Der GPS-Fix geht nur noch in den Sperrgrund ein, damit
    im Banner steht, ob GPS zwar da ist, aber die Uhr nicht stellt.
    """
    if not hw.chrony_synced:
        code = (
            "guard.time_no_sync_gps_idle" if hw.gps_fix_mode >= 2
            else "guard.time_no_sync"
        )
        return GuardResult(False, "time_guard", code)
    if hw.time_offset_s is None:
        # chrony synchron gemeldet, aber der Offset war nicht lesbar —
        # ohne Messwert kein Urteil, und ohne Urteil kein TX.
        return GuardResult(False, "time_guard", "guard.time_unknown")
    if abs(hw.time_offset_s) > lim.time_offset_max_s:
        return GuardResult(
            False, "time_guard", "guard.time_offset",
            {"offset": f"{hw.time_offset_s:+.3f}", "max": lim.time_offset_max_s},
        )
    return GuardResult(True, "time_guard")


def rig_link_guard(hw: HardwareState, lim: GuardLimits) -> GuardResult:
    """Blocke TX, wenn wir das Rig nicht mehr auslesen koennen.

    ``RigctldClient.snapshot()`` wirft nie: jedes Feld ist einzeln in ein
    ``try/except`` gewickelt, damit ein fehlendes Hamlib-Level nicht den
    ganzen Snapshot kippt. Ist rigctld tot oder das USB-Kabel raus, kommt
    darum kein Fehler zurueck, sondern ein Snapshot mit lauter ``None``.

    Das ist der gefaehrliche Teil: ``None`` bedeutet fuer die
    nachgelagerten Guards ueberall "unauffaellig". swr=None wird zu 1.0,
    battery_v=None heisst Netzbetrieb, freq_hz=None laesst Antennen- und
    Lizenz-Guard passieren, weil sie das als Startzustand lesen. Beim
    Wegfallen des Rigs ging also die komplette rig-seitige Guard-Kette auf
    gruen — genau umgekehrt zur Absicht.

    Darum die Alterspruefung an einer Stelle statt None-Checks in fuenf
    Guards: nur wer frische Messwerte hat, darf ueber sie urteilen.

    ``None`` sperrt bewusst nicht. Das heisst "seit dem Start nie ein
    Snapshot angekommen" — Bootphase, Demo-Betrieb, Testpfade ohne Rig.
    Ein Verlust laesst sich daraus nicht ableiten, und ein Lock waere
    sticky.
    """
    if hw.rig_link_age_s is None:
        return GuardResult(True, "rig_link_guard")
    if hw.rig_link_age_s > lim.rig_link_max_age_s:
        return GuardResult(
            False, "rig_link_guard", "guard.rig_link",
            {"age": f"{hw.rig_link_age_s:.0f}", "max": f"{lim.rig_link_max_age_s:.0f}"},
        )
    return GuardResult(True, "rig_link_guard")


def dial_guard(hw: HardwareState, lim: GuardLimits) -> GuardResult:
    """Blocke TX, wenn das Rig nicht auf einem konfigurierten Dial steht.

    architecture.md §5 verspricht eine IARU-Segment-Sperre; der Code
    dafuer (util/bandplan.is_in_ft8_segment) wurde nie aufgerufen. Was
    lief, war die grobe Banderkennung mit Region-2-Kanten: 80 m bis
    4 000 kHz, 40 m bis 7 300 — ein VFO auf 3,900 MHz galt als "80 m",
    Lizenz- und Antennen-Guard waren gruen, und die Box sendete
    ausserhalb der deutschen Zuteilung.

    Der Abgleich hier nutzt die Config als Wahrheit: die FT8- und
    FT4-Dials aller konfigurierten Baender, mit Toleranz
    (operating.dial_tolerance_hz). Bewusst nicht die IARU-Tabelle: die
    kennt fuer 60 m nur Region 2 und keine FT4-Fenster — naiv verdrahtet
    haette sie Dads 60 m in DL komplett gesperrt.

    None (Frequenz unbekannt) sperrt hier nicht — das ist der Fall des
    rig_link_guard, der davor steht. Der Tamper-Push mit Rollback-Button
    bleibt daneben bestehen; er ist Komfort, das hier ist der Guard.
    """
    if hw.dial_on_configured_freq is None:
        return GuardResult(True, "dial_guard")
    if not hw.dial_on_configured_freq:
        mhz = f"{hw.rig_freq_hz / 1e6:.4f}" if hw.rig_freq_hz is not None else "?"
        return GuardResult(
            False, "dial_guard", "guard.dial",
            {"mhz": mhz, "tol": lim.dial_tolerance_hz},
        )
    return GuardResult(True, "dial_guard")


def swr_guard(hw: HardwareState, lim: GuardLimits) -> GuardResult:
    if hw.swr > lim.swr_max:
        return GuardResult(
            False, "swr_guard", "guard.swr",
            {"swr": f"{hw.swr:.2f}", "max": f"{lim.swr_max:.2f}"},
        )
    return GuardResult(True, "swr_guard")


def alc_guard(hw: HardwareState, lim: GuardLimits) -> GuardResult:
    # alc_max <= 0 heisst "Guard aus", NICHT "Null-Toleranz". Der
    # ALC-Closed-Loop regelt bewusst auf alc_target_pct (Default 15) —
    # eine Null-Toleranz-Lesart wuerde bei bestimmungsgemaessem Betrieb
    # jeden TX sperren, und der Lock ist sticky.
    #
    # Es gibt hier kein Verhalten zu erhalten: bis 2026-07-30 setzte der
    # Orchestrator hw.alc_pct hartkodiert auf 0, dieser Guard hat also
    # noch nie gefeuert. Bestandsconfigs stehen auf alc_max=0 (dem
    # frueheren Default) und bleiben damit bewusst aus, bis der Operator
    # den Wert bewusst setzt.
    if lim.alc_max <= 0:
        return GuardResult(True, "alc_guard")
    if hw.alc_pct > lim.alc_max:
        return GuardResult(
            False, "alc_guard", "guard.alc",
            {"alc": hw.alc_pct, "max": lim.alc_max},
        )
    return GuardResult(True, "alc_guard")


def battery_guard(hw: HardwareState, lim: GuardLimits) -> GuardResult:
    # battery_min_v <= 0 heisst "Guard aus". Der alte feste Default von
    # 12,0 V (architecture.md §5) wurde nie gegen ein Geraet gehalten,
    # weil der Guard bis 2026-09-06 hartkodiert battery_v=None bekam. Der
    # interne Akku des IC-705 liegt nominal bei 7,4 V — ein Lock beim
    # ersten TX am Akku waere das wahrscheinlichste Ergebnis gewesen. Der
    # Wert gehoert nach einer Live-Messung in die Config, nicht hierher.
    if lim.battery_min_v <= 0:
        return GuardResult(True, "battery_guard")
    if hw.battery_v is None:
        return GuardResult(True, "battery_guard")  # external power, no check
    if hw.battery_v < lim.battery_min_v:
        return GuardResult(
            False, "battery_guard", "guard.battery",
            {"volts": f"{hw.battery_v:.1f}", "min": lim.battery_min_v},
        )
    return GuardResult(True, "battery_guard")


def temp_guard(hw: HardwareState, lim: GuardLimits) -> GuardResult:
    if hw.cpu_temp_c is None:
        # Sensor nicht lesbar. Kein Urteil ueber einen erfundenen Wert;
        # der Pi drosselt sich thermisch ohnehin selbst.
        return GuardResult(True, "temp_guard")
    if hw.cpu_temp_c > lim.cpu_temp_max_c:
        return GuardResult(
            False, "temp_guard", "guard.temp",
            {"temp": f"{hw.cpu_temp_c:.1f}", "max": lim.cpu_temp_max_c},
        )
    return GuardResult(True, "temp_guard")


def audio_drift_guard(hw: HardwareState, lim: GuardLimits) -> GuardResult:
    drift = abs(hw.audio_drift_samples)
    if drift > lim.audio_drift_fail_samples:
        return GuardResult(
            False, "audio_drift_guard", "guard.audio_drift", {"drift": drift},
        )
    return GuardResult(True, "audio_drift_guard")


def antenna_guard(hw: HardwareState, lim: GuardLimits) -> GuardResult:
    """Prevent TX on a band our active antenna can't handle.

    The orchestrator computes ``antenna_covers_band`` per slot from the
    current ``rig.freq_hz`` and the configured antenna profiles. If the
    user is parked on a band the antenna isn't rated for, TX is blocked.
    """
    if not hw.antenna_covers_band:
        return GuardResult(False, "antenna_guard", "guard.antenna")
    return GuardResult(True, "antenna_guard")


def license_guard(hw: HardwareState, lim: GuardLimits) -> GuardResult:
    """Prevent TX on a band the operator's licence doesn't cover.

    ``AppConfig.can_tx_on()`` existed and was tested from early on, but had
    no caller in the runtime path — only the autopilot's band picker was
    licence-aware. Setting a band or frequency by hand went straight past
    it, so a class-E operator could be parked on a class-A-only band and
    the guard pipeline would happily allow TX.

    Deliberately a separate guard from ``antenna_guard``: an unlicensed
    band and a mismatched antenna need different fixes, so they must not
    share a lock reason.
    """
    if not hw.band_allowed_for_license:
        return GuardResult(False, "license_guard", "guard.license")
    return GuardResult(True, "license_guard")


# Default ordered pipeline. Order matters: cheap pure-cpu checks first,
# then external-state checks. Stops at first failure.
DEFAULT_GUARDS: tuple[Guard, ...] = (
    time_guard,
    audio_drift_guard,
    # Vor allen rig-abgeleiteten Guards: sind die Messwerte veraltet,
    # ist deren Urteil wertlos. Der Grund fuer die Sperre soll dann
    # "Rig nicht erreichbar" heissen und nicht "SWR ok".
    rig_link_guard,
    # Direkt dahinter: steht das Rig auf keinem konfigurierten Dial, ist
    # das Band-Urteil von license/antenna wertlos (Region-2-Kanten).
    dial_guard,
    license_guard,
    antenna_guard,
    swr_guard,
    alc_guard,
    battery_guard,
    temp_guard,
)


def evaluate(
    hw: HardwareState,
    limits: GuardLimits,
    guards: tuple[Guard, ...] = DEFAULT_GUARDS,
) -> list[GuardResult]:
    """Run all *guards*; return their results in order."""
    return [g(hw, limits) for g in guards]


def first_failure(results: list[GuardResult]) -> GuardResult | None:
    """Return the first non-ok guard result, or ``None`` if all green."""
    return next((r for r in results if not r.ok), None)
