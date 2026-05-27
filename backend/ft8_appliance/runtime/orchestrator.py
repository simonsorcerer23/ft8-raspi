"""The runtime orchestrator — the glue between hardware, decoder and state machine.

One :class:`Orchestrator` instance is the long-lived heart of the
appliance. It owns:

* the :class:`StateMachine` instance
* a :class:`RigctldClient` connected to (real or mock) ``rigctld``
* a :class:`GpsdClient` consuming the gpsd stream
* a :class:`SlotClock` driving the 15-second cycle
* a *decode source* — abstract callable that yields the current slot's
  decodes (real implementation uses ALSA + ft8_lib; tests use a list
  of canned decodes)
* an outgoing event bus for SSE consumers (web layer subscribes)

The orchestrator never touches HTTP — that lives in the web layer,
which holds a reference to the orchestrator and reads/writes through
the public methods.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import typing

import sdnotify
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

from ..config import AppConfig, OperatorConfig
from ..db import repository, session_scope
from ..db.models import Blacklist as DbBlacklist
from ..db.models import CallReputation as DbCallReputation
from ..db.models import DxpeditionSchedule as DbDxpeditionSchedule
from ..db.models import FreqReputation as DbFreqReputation
from ..db.models import Qso
from ..db.models import Watchlist as DbWatchlist
from ..gps import GpsdClient, GpsSnapshot
from ..rig import RigctldClient, RigSnapshot
from ..statemachine import (
    Action,
    DecodedMsg,
    GuardLimits,
    HardwareState,
    MachineContext,
    StateMachine,
)
from ..integrations import (
    BlitzortungClient,
    CtyDat,
    DxClusterClient,
    HamQslClient,
    HamQthClient,
    NtfyClient,
    PskReporterClient,
    QrzClient,
)
from ..integrations.mf_lookup import get_mf_lookup


def _mf_snapshot_mfnr(call: str | None) -> int | None:
    """Marinefunker-Snapshot-Helper (v0.9.0): liefert die aktuelle MFNr
    eines aktiven Mitglieds oder None. Verwendet im LOG_QSO-Insert um
    den MF-Status zum QSO-Zeitpunkt einzufrieren.
    """
    if not call:
        return None
    m = get_mf_lookup().lookup(call)
    return m.mfnr if m else None
from ..util.bandplan import (
    band_from_freq_hz as _band_from_freq_hz,
    iaru_region_from_latlon,
    is_in_ft8_segment,
)
from ..util.maidenhead import latlon_to_locator
from ..util.system_health import ChronyStatus, read_chrony_tracking
from .slot_clock import SlotClock, SlotTick


@dataclass(slots=True)
class IntegrationContainer:
    """Holds the configured online clients. Constructed in Orchestrator.start()
    from AppConfig; routes read from here via orch.integrations.
    """
    qrz: QrzClient | None = None
    hamqth: HamQthClient | None = None
    hamqsl: HamQslClient | None = None
    psk_reporter: PskReporterClient | None = None
    blitzortung: BlitzortungClient | None = None
    ntfy: NtfyClient | None = None
    cty: CtyDat | None = None  # offline DXCC lookup, loaded at boot
    dx_cluster: DxClusterClient | None = None

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class LoggedAction:
    """Action plus the wall-clock time at which the orchestrator
    observed it. The state machine itself is timeless; this wrapper
    lets the conversation feed show TX entries with their real send
    time instead of the API-call time (sonst sahen alle TX-Eintraege
    im UI gleichzeitig aus weil der Endpoint ``now_iso`` benutzt hat).
    """
    ts: datetime
    action: Action

    @property
    def kind(self) -> str:
        return self.action.kind

    @property
    def payload(self) -> dict:
        return self.action.payload


# A decode source is "given a slot, produce its decodes". Real impl
# pulls audio frames, calls ft8_lib decode; tests inject a list.
DecodeSource = Callable[[SlotTick], Awaitable[list[DecodedMsg]]]


def _safe_get_pass_stats() -> dict | None:
    """v0.8.0 Build C: best-effort pass-stats fetch. None bei Dev-
    Maschine ohne ft8_lib oder bei build-mismatch."""
    try:
        from ..decode.ft8_native import get_pass_stats
        return get_pass_stats()
    except Exception:
        return None


@dataclass(slots=True)
class OrchestratorStatus:
    """Snapshot for /api/status."""

    callsign: str
    state: str
    last_lock_reason: str | None
    cq_count: int
    current_qso_call: str | None
    last_slot_index: int
    last_decodes: int
    auto_answer: bool
    tx_power_w: int
    active_antenna: str | None
    worked_count: int
    blacklist_count: int
    rig: RigSnapshot
    gps: GpsSnapshot
    # ALC closed-loop telemetry — exposed so the operator can see the
    # current audio gain factor and the last ALC reading the loop saw.
    audio_gain: float = 0.9
    last_alc_pct: int | None = None
    # WSJT-Z-style: when True we re-CQ after each QSO until the user
    # explicitly hits Stop. Mirrors state_machine.ctx.auto_cq.
    auto_cq: bool = False
    # Active digital mode (FT8 or FT4). Driven by config.operating.mode.
    # Surfaced so the UI can show the slot length + tone-spacing the
    # appliance is using right now.
    mode: str = "FT8"
    # RX-Audio-Pegel in dBFS (negativer Wert, 0 = Full-Scale int16).
    # Direkt aus dem ALSA-Capture-Stream berechnet als RMS der letzten
    # 250 ms — unabhängig vom Hamlib-STRENGTH (das beim IC-7300 in
    # PKTUSB als kaputt bekannt ist, liefert konstant 0/-54). Zappelt
    # mit echter Signal-Aktivität. None wenn ALSA-Capture noch nicht
    # läuft (z.B. Dev-Maschine ohne Audio-Hardware).
    rx_audio_dbfs: float | None = None
    # Lizenzklasse (A/E/N) — Frontend nutzt das um den Power-Slider
    # zu deckeln und die Band-Auswahl zu filtern.
    license_class: str = "A"
    # Effective max TX power für das aktuell gewählte Band (MIN aus
    # Lizenz-Cap, Rig-Hardware-Cap und operator.default_power_w).
    # None wenn das aktive Band nicht erkannt wurde (z.B. Pi gerade
    # auf einer Frequenz die zu keinem konfigurierten Band passt).
    effective_max_power_w: int | None = None
    # Name des aktuell erkannten Bandes (Hz → Band-Lookup über
    # ±50 kHz Toleranz). None wenn Frequenz zu nichts passt.
    active_band: str | None = None
    # v0.6.3: Decoder-Mode (standard|deep|multi) — was die Pipeline
    # tatsaechlich JETZT nutzt. Bei CPU-Adaptive-Fallback weicht das
    # ab vom config.operating.decoder_mode (User-gewollt) — beide
    # zeigen damit Monitor + Frontend sofort sehen wenn der Pi
    # automatisch zurueckgeschaltet hat.
    decoder_mode: str = "standard"  # was konfiguriert ist
    actual_decoder_mode: str = "standard"  # was die Pipeline gerade nutzt
    decoder_late_slot_count: int = 0  # zaehlt seit Service-Start
    # v0.8.0 Build C: Per-Pass-Decoder-Statistics (extreme mode only)
    decoder_pass_stats: dict | None = None


@dataclass
class Orchestrator:
    config: AppConfig
    rig: RigctldClient
    gps: GpsdClient
    decode_source: DecodeSource
    slot_clock: Any = field(default_factory=SlotClock)  # SlotClock | FakeSlotClock
    # Optional AlsaPlayback (real TX path on the Pi). Tests pass None;
    # _do_tx_message logs without transmitting in that case.
    playback: Any = None
    db_enabled: bool = True  # tests can disable to skip session_scope

    state_machine: StateMachine = field(init=False)
    _action_handlers: dict[str, Callable[[dict], Awaitable[None]]] = field(init=False)
    _decode_subscribers: list[asyncio.Queue[DecodedMsg]] = field(default_factory=list, init=False)
    _state_subscribers: list[asyncio.Queue[OrchestratorStatus]] = field(
        default_factory=list, init=False
    )
    _action_log: list[LoggedAction] = field(default_factory=list, init=False)
    _last_slot: SlotTick | None = field(default=None, init=False)
    _last_decodes: list[DecodedMsg] = field(default_factory=list, init=False)
    # Monotonic timestamp des letzten erfolgreichen Decode-Empfangs.
    # Sebastian sah 2026-05-23: der Funkstille-Watchdog las _last_decodes
    # (Slot-Snapshot) alle 60s. Bei CQ-Mode mit even-only-TX trifft das
    # 60s-Intervall immer auf TX-Slots (60/15=4, alle TX) → _last_decodes
    # ist da immer leer (Halbduplex) → Watchdog dachte 15 min Funkstille
    # obwohl in odd-Slots ständig dekodiert wurde. Persistenter Timestamp
    # statt Snapshot eliminiert den Phase-Lock-Bug.
    _last_decode_recv_at: float = field(default_factory=lambda: time.time(), init=False)
    # v0.6.0 Anti-WSJT-X-Audit Phase A2: DT-Drift-Self-Diagnose.
    # Sammelt die DT-Werte (dt_s) der letzten Decodes — wenn der
    # Median systematisch um >0.5s offset ist, ist UNSERE Clock schuld
    # (nicht alle Stationen weltweit gleichzeitig). WSJT-X kann das
    # mehrmals pro Woche auf Windows-Default-7d-NTP-Sync passieren.
    # Wir alerten dann statt still falsche Decodes zu produzieren.
    _recent_dts: list[float] = field(default_factory=list, init=False)
    _last_dt_drift_alert_at: float = field(default=0.0, init=False)
    # v0.6.0 Phase A1: Decoder-Late-Slot-Watchdog-Push-Cooldown.
    _last_late_slot_alert_at: float = field(default=0.0, init=False)
    # v0.8.0 Build B: DT-Offset Auto-Kalibrierung. Rolling Median ueber
    # die letzten 200 dt-Werte. Wenn |median| > 0.3s wird der Wert als
    # negativ-Offset auf slot_start_posix appliziert — der Decoder sieht
    # dann zentrierte DTs (~0) und findet mehr Decodes weil das Time-
    # Window besser trifft. Update alle ~5 min.
    _dt_calibration_offset_s: float = field(default=0.0, init=False)
    _last_dt_calibration_at: float = field(default=0.0, init=False)
    # Sebastian v0.5.2: Single-Shot-Flag fuer Funkstille-Watchdog.
    # True = wir haben schon einen "Keine Decodes seit X min"-Push
    # rausgeschickt fuer diese Funkstille-Episode. Bleibt True bis der
    # naechste echte Decode reinkommt — dann False, und ein neuer Push
    # ist beim naechsten Stille-Event wieder erlaubt. Verhindert alle-
    # 15min-Spam wenn das Band nachts stundenlang tot ist.
    _funkstille_push_active: bool = field(default=False, init=False)
    # Sebastian v0.5.2: Timestamp des letzten Eigenstandig-gesetzten
    # rig.set_freq (via _ensure_dial_matches_mode). Tamper-Detector
    # darf in den ersten paar Sekunden danach KEINEN "extern verstellt"-
    # Push feuern — der Set selbst rast oft als false-positive ein
    # weil rig-poll die Aenderung sieht bevor das echo registered ist.
    _dial_set_at: float = field(default=0.0, init=False)
    # CQ-Idle-Watchdog (Sebastian 2026-05-24, Audit-Finding 1):
    # Wann hat der State_Machine zuletzt cq_count=0 gemeldet (= QSO
    # gerade erfolgreich abgeschlossen ODER frischer CQ-Start)? Wenn
    # cq_count seitdem nur hochgezaehlt wurde ohne Reset auf 0
    # → niemand antwortet, ntfy-Push faellig.
    _cq_count_zero_at: float = field(default=0.0, init=False)
    _cq_idle_alert_sent: bool = field(default=False, init=False)
    _bg_tasks: list[asyncio.Task] = field(default_factory=list, init=False)
    # Set of callsigns we've already worked at least once. Loaded from
    # DB at start(); appended to on every LOG_QSO. Used by the worked-B4
    # annotation served via /api/decodes and the SSE stream.
    _worked_calls: set[str] = field(default_factory=set, init=False)
    _worked_dxccs: set[str] = field(default_factory=set, init=False)
    # Multi-color highlighting (WSJT-Z parity). Grids in canonical
    # 4-character form (e.g. "FN31"). Grid-band tuples track whether a
    # grid is new for *this* band specifically — DXers care because
    # working ZL on 40m is a different award point than ZL on 20m.
    _worked_grids: set[str] = field(default_factory=set, init=False)
    _worked_grid_band: set[tuple[str, str]] = field(default_factory=set, init=False)
    # v0.10.0: 5BWAS-Tracking (DXCC-Entity-Name, Band-Kuerzel) Tuples.
    # Aus DB beim Boot rekonstruiert, bei jedem LOG_QSO inkrementiert.
    _worked_dxcc_band: set[tuple[str, str]] = field(default_factory=set, init=False)
    # v0.10.0: Set normalisierter Calls die uns laut PSK Reporter recently
    # gehört haben. Vom Background-Refresh-Loop _psk_reciprocity_refresh
    # alle paar Minuten upgedated. Leer wenn psk_reciprocity_enabled=False.
    _psk_heard_us_cache: set[str] = field(default_factory=set, init=False)
    _psk_last_refresh_at: float = field(default=0.0, init=False)
    _psk_last_refresh_ok: bool = field(default=False, init=False)
    # v0.14.0 — Watchlist + Band-Conditions + Solar-Refresh-Throttle.
    # _watchlist_calls: set normalisierter Calls die der User beobachtet
    # (DXpeditions etc.). Beim Boot aus DB rekonstruiert, dann via
    # handle_watchlist_add/remove aktualisiert. Wird pro Slot in den
    # state-machine-ctx gespiegelt.
    _watchlist_calls: set[str] = field(default_factory=set, init=False)
    # Throttle pro Watchlist-Call (POSIX-Zeit des letzten ntfy-Pushes).
    # Default-Fenster: 1h damit DXpedition-Calls die im selben Slot
    # 30x decoden nicht in Push-Spam ausarten.
    _watchlist_last_alert: dict[str, float] = field(default_factory=dict, init=False)
    # Band-Conditions aus hamqsl-Solar — periodisch via _solar_refresh_loop
    # aktualisiert, pro Slot in den ctx kopiert.
    _band_conditions_day: dict[str, str] = field(default_factory=dict, init=False)
    _band_conditions_night: dict[str, str] = field(default_factory=dict, init=False)
    _solar_last_refresh_at: float = field(default=0.0, init=False)
    # v0.15.0 — Soft-Blacklist-Cache aus call_reputation-DB-Tabelle.
    # Set normalisierter Calls die ueber Threshold sind. Beim Boot
    # rekonstruiert, bei jedem QSO_BAIL/LOG_QSO inkrementell aktualisiert.
    _soft_blacklist: set[str] = field(default_factory=set, init=False)
    # v0.15.0 — Slot-Parity-Tracking pro Op (Call → "even"|"odd"|"").
    # Aus Decode-Beobachtungen: wenn wir mehrere Decodes von call X in
    # konsistenten Slot-Parities sehen, koennen wir seinen TX-Slot
    # raten und vermeiden im selben Slot zu antworten.
    _op_slot_parity_votes: dict[str, dict[str, int]] = field(
        default_factory=dict, init=False
    )
    _op_slot_parity: dict[str, str] = field(default_factory=dict, init=False)
    # v0.16.0 — Hour-of-Day-Aggregat aus DB. Set von (continent, hour)
    # die in den letzten 90 Tagen ueberdurchschnittlich aktiv waren.
    # Beim Boot rekonstruiert + alle 1h re-aggregiert.
    _active_continent_hours: set[tuple[str, int]] = field(
        default_factory=set, init=False
    )
    _hod_last_refresh_at: float = field(default=0.0, init=False)
    # v0.17.0 — Buddy-Seen-Tier: (call, band) Set fuer "schon auf dem
    # Band gearbeitet". Beim Boot aus DB rekonstruiert, bei jedem
    # LOG_QSO inkrementiert.
    _worked_call_band: set[tuple[str, str]] = field(
        default_factory=set, init=False
    )
    # v0.18.0 — Freq-Reputation: (band, bin) → (attempts, successes).
    # In-memory aus DB hydratisiert; Smart-CQ-Picker biast zu hoch-
    # erfolgreichen Bins. Updates fire-and-forget zu DB.
    _freq_reputation: dict[tuple[str, int], tuple[int, int]] = field(
        default_factory=dict, init=False
    )
    # Letzter CQ-Burst pro (band, bin) — wird beim LOG_QSO ausgewertet
    # um den Success dem richtigen Bin zuzuschreiben.
    _last_cq_band_bin: tuple[str, int] | None = field(default=None, init=False)
    # v0.13.0 — Blitzortung-Storm-Alert Throttle.
    # _last_storm_alert_at: monotonic-Zeit des letzten ntfy-Pushes
    # (egal welche Distanz). Verhindert Spam wenn ein Gewitter eine
    # Stunde lang in der Naehe hin- und herhuepft.
    # _last_storm_alert_km: Distanz die wir zuletzt gemeldet haben —
    # ermoeglicht "Storm kommt naeher"-Re-Push noch innerhalb des
    # Throttle-Fensters wenn er deutlich naeher gerueckt ist.
    _last_storm_alert_at: float = field(default=0.0, init=False)
    _last_storm_alert_km: float | None = field(default=None, init=False)
    # v0.10.0: Set normalisierter Marinefunker-Calls. Beim Boot aus
    # marinefunker.json geladen — statisch außer beim Refresh.
    _marine_calls_cache: set[str] = field(default_factory=set, init=False)
    # Currently active antenna profile name; drives the band-lockout guard.
    _active_antenna: str | None = field(default=None, init=False)
    # Currently configured TX power in watts (user-controllable via UI).
    _tx_power_w: int = field(default=10, init=False)
    # Letztes bekanntes Band — fuer Bandwechsel-Trigger des Safety-Floor
    # (Sebastian 2026-05-24, v0.2.3). Nur bei tatsaechlicher Aenderung
    # X→Y wird der Safety-Floor neu evaluiert, nicht bei jedem status-Poll.
    _last_active_band: str | None = field(default=None, init=False)
    # Letzte bekannte Rig-Identifikation (hamlib_id) — fuer Rig-Wechsel-
    # Detection in on_config_changed. None vor erstem Config-Load.
    _last_rig_hamlib_id: int | None = field(default=None, init=False)
    # Tamper-Detection: Tracking der zuletzt von uns (App) ausgeloesten
    # CAT-Befehle. Wenn der Rig-Poll nachher denselben Wert zurueckliest,
    # ist das unser Echo — keine Push-Benachrichtigung. Wenn der Wert
    # abweicht UND wir nichts gesendet haben, ist es eine externe
    # Aenderung (Frontpanel/andere Software) → ntfy-Push.
    # Key: "rfpower_norm"|"freq_hz"|"mode"|"bandwidth_hz", Value: (sollwert, monotonic_ts)
    _recent_app_commands: dict = field(default_factory=dict, init=False)
    # Throttle-Marker fuer Tamper-Pushes — wir wollen nicht spammen wenn
    # der OP gerade aktiv am Knopf dreht, sondern nur EINEN Push beim
    # ersten Erkennen eines neuen Werts. Wenn der Wert sich aendert,
    # neuer Push (z.B. erst auf 30W gedreht, dann auf 5W).
    _last_power_alert_w: int | None = field(default=None, init=False)
    _last_mode_alert: str | None = field(default=None, init=False)
    _last_bandwidth_alert_hz: int | None = field(default=None, init=False)
    # Boot-Gate: beim ersten Sync nach Service-Start wissen wir nicht
    # was am Rig steht (es koennte vom letzten Boot uebrig sein oder
    # gerade frisch verstellt worden sein). Erste Iteration syncen wir
    # nur silent — Tamper-Pushes ab dem zweiten Sync.
    _tamper_armed: bool = field(default=False, init=False)
    # ALC closed-loop runtime state. Seeded from
    # ``config.operating.audio_gain`` in ``start()`` and trimmed each
    # time the rig poll observes ALC during PTT.
    _audio_gain: float = field(default=0.9, init=False)
    # Last ALC reading observed *during PTT on*. Surfaced to the UI so
    # the operator sees what the closed-loop is reacting to. ``None``
    # when we haven't TX'd yet (or rig doesn't report ALC).
    _last_alc_pct: int | None = field(default=None, init=False)
    # Monotonic timestamp of the most recent _do_tx_message dispatch.
    # Sebastian sah am 2026-05-22: zwischen TX-Bursts gibt der IC-7300
    # ALC=0% zurueck, der Closed-Loop interpretierte das als "zu leise"
    # und kurbelte den Gain in 7 Sekunden von 0.48 → 1.00 hoch, dann
    # explodierte ALC beim naechsten echten Burst auf 94%, Loop drehte
    # in 12s zurueck auf 0.28, dann wieder hoch … Endlos-Ping-Pong.
    # Up-Adjustment darf nur greifen wenn wir kuerzlich (15s) wirklich
    # einen Burst losgelassen haben — sonst sind ALC=0%-Reads phantom.
    _last_tx_message_at: float = field(default=0.0, init=False)
    # Burst-Peak-basiertes ALC-Adjustment: Samples werden waehrend
    # eines TX-Bursts gesammelt und am Burst-Ende (PTT-Abfallflanke)
    # ausgewertet — verhindert Per-Tick-Oszillation zwischen Bursts.
    # PI-Regler auf rfpower_meter (Sebastian + Claude 2026-05-22):
    # - alc-Samples bleiben fuer den Safety-Watchdog (alc_peak>threshold
    #   ⇒ Notabschaltung) und fuer die UI-Anzeige
    # - pwr_meter-Samples sind die Messgroesse des Hauptreglers
    # - pwr_integrator akkumuliert die Regelabweichung fuer den I-Anteil
    _tx_alc_samples: list[int] = field(default_factory=list, init=False)
    _tx_pwr_samples: list[float] = field(default_factory=list, init=False)
    _pwr_integrator: float = field(default=0.0, init=False)
    _ptt_was_on: bool = field(default=False, init=False)
    # Path for persistent runtime-state (audio_gain across restarts).
    # Sebastians Beobachtung: nach Service-Restart startete _audio_gain
    # auf 0.21 weil der letzte ALC-trim-Wert nicht persistiert war →
    # ALC-Loop musste 10+ min hochregeln. Mit Persistenz beginnt der
    # Cold-Start dort wo der letzte Service stehen blieb.
    _runtime_state_path: Path = field(
        default_factory=lambda: Path("/var/lib/ft8-appliance/runtime_state.json"),
        init=False,
    )
    # Throttle für Persist-Writes (sonst schreiben wir potenziell jede
    # Sekunde — unnötiger Flash-Wear auf der SSD). Wir persistieren nur
    # wenn der Gain sich um ≥0.02 verschoben hat ODER ≥60s seit dem
    # letzten Write vergangen sind.
    _last_persisted_gain: float = field(default=-1.0, init=False)
    _last_persisted_gain_at: float = field(default=0.0, init=False)
    # Audio-Clipping-Watchdog State. Trackt seit wann der RX-Pegel im
    # roten Bereich (≥-3 dBFS) hängt — wenn das 30 s anhält, ntfy.
    # _last_audio_clip_ntfy_at: Throttle damit nicht alle 30 s erneut.
    _audio_clip_since: float | None = field(default=None, init=False)
    _last_audio_clip_ntfy_at: float = field(default=0.0, init=False)
    # SWR-Vorwarn-Watchdog (Soft-Stufe vor dem Hard-Lock).
    # Trackt seit wann SWR > swr_warn aber < swr_max. Bei nachhaltigem
    # Überschreiten ntfy-Push mit dem aktuellen Wert; Throttle 10 min.
    # _swr_warn_since=None heißt aktuell unter Schwelle.
    _swr_warn_since: float | None = field(default=None, init=False)
    _last_swr_warn_ntfy_at: float = field(default=0.0, init=False)
    # SWR-Runaway-Handling (Sebastian 2026-05-24): Live-PTT-Cut bei
    # SWR >= swr_max waehrend laufendem TX. Pre-TX-Guard greift nicht
    # weil hw.swr zwischen Bursts auf den RX-Default 1.0 zurueckfaellt
    # und der naechste _check_guards den Overshoot nicht sieht.
    # Flag verhindert Push-Spam wenn der gleiche TX-Burst mehrfach
    # gepollt wird vor PTT auf False geht.
    _swr_runaway_active: bool = field(default=False, init=False)
    # SWR-Settling-Period (Sebastian 2026-05-24): nach jedem PTT-On
    # braucht das IC-7300-SWR-Meter ~1-2 s bis es den AKTUELLEN Wert
    # misst — vorher liefert es noch Peak-Hold vom letzten Burst
    # (z.B. 2.88 vom 20m-TX im Cache obwohl wir jetzt 15m senden mit
    # echtem SWR 1.0). Innerhalb der Settling-Period werden SWR-
    # Readings vom Live-Cut-Check ignoriert.
    _ptt_on_at: float = field(default=0.0, init=False)
    # ALC-Vorwarn-Watchdog. Analog zu SWR: erste Stufe ist Push, zweite
    # ist Hard-Cap. Throttle ebenfalls 10 min damit's nicht spammt wenn
    # der ALC-Loop noch nachjustiert.
    _alc_warn_since: float | None = field(default=None, init=False)
    _last_alc_warn_ntfy_at: float = field(default=0.0, init=False)
    integrations: IntegrationContainer = field(default_factory=IntegrationContainer, init=False)
    # Failure-defaults: before the first _refresh_hardware_state() we want
    # every guard to FAIL, not pass. That way a control-button pressed
    # in the brief window between start() and the first slot can't TX on
    # made-up "no GPS yet, all good" defaults.
    _hardware_state: HardwareState = field(
        default_factory=lambda: HardwareState(
            gps_fix_mode=0,           # no fix until proven otherwise
            time_offset_s=99.0,       # out of bounds until chrony reports
            swr=9.9,                  # implausibly high
            alc_pct=100,              # implausibly high
            battery_v=0.0,            # critically low
            cpu_temp_c=999.0,         # critically hot
            audio_drift_samples=10_000,
        ),
        init=False,
    )

    # ------------------------------------------------------------------ init
    def __post_init__(self) -> None:
        self.state_machine = StateMachine(
            ctx=MachineContext(
                callsign=self.config.operator.callsign,
                my_grid=self.config.operator.default_locator or "AA00",
                cq_directed=(self.config.operating.cq_directed or "").upper(),
            ),
            limits=GuardLimits(
                swr_max=self.config.operating.swr_max,
                alc_max=self.config.operating.alc_max,
            ),
            qso_max_stale_slots=self.config.operating.qso_max_stale_slots,
            qso_max_cq_resends=self.config.operating.qso_max_cq_resends,
            qso_max_report_resends=self.config.operating.qso_max_report_resends,
            qso_failed_cooldown_s=float(
                self.config.operating.qso_failed_cooldown_min * 60
            ),
        )
        self._action_handlers = {
            "TX_MESSAGE": self._do_tx_message,
            "STOP_TX": self._do_stop_tx,
            "LOG_QSO": self._do_log_qso,
            "TX_LOCKED": self._do_tx_locked,
            "QSO_BAIL": self._do_qso_bail,  # v0.15.0
        }

    # ------------------------------------------------------------------ public API
    async def start(self) -> None:
        """Connect to hardware and launch the background tasks."""
        # Globale integrations.* aus dem aktiven Operator spiegeln,
        # BEVOR _init_integrations laeuft — sonst initialisiert sich
        # QRZ/ntfy mit den Default-/Alt-Werten der YAML statt mit den
        # Operator-Credentials.
        try:
            self._sync_global_integrations_from_operator(self.config.operator)
        except ValueError:
            # operators leer (Wizard-Mode) — Sync uebergangen
            pass
        self._init_integrations()
        await self._hydrate_from_db()
        if self._active_antenna is None and self.config.antennas:
            self._active_antenna = self.config.antennas[0].name
        self._tx_power_w = self.config.operator.default_power_w
        # Start mit Config-Default, dann ggf. persistierten Wert drüberlegen
        # damit Cold-Start dort weitermacht wo der letzte Service aufhörte.
        self._audio_gain = float(self.config.operating.audio_gain)
        # RX-Audio-Peak-Hold mit Decay — verhindert dass die UI-Anzeige
        # zwischen FT8-Burst (~-6 dBFS) und Pause (~-45 dBFS) wild flackert.
        # Peak springt instant nach oben, decayed dann 6 dB/sec runter
        # bis zum aktuellen RMS. Klassisches VU-Meter-Verhalten.
        self._rx_audio_dbfs_peak: float | None = None
        self._rx_audio_dbfs_peak_ts: float = 0.0
        self._load_runtime_state()
        # TX-Power Safety-Floor bei Boot (Sebastian 2026-05-24, v0.2.3):
        # Egal was die runtime_state.json oder operator.default_power_w
        # vorgeben — wenn der Wert oberhalb effective_max/2 liegt, runter
        # clampen. Variante B: QRP-Werte darunter bleiben unangetastet.
        # Active-Band ist hier noch unbekannt (rig poll noch nicht gelaufen),
        # daher faellt _compute_safe_default_power_w auf rig.effective_max
        # zurueck — sicher und konservativ.
        # _last_rig_hamlib_id setzen damit on_config_changed spaeter
        # Rig-Wechsel detecten kann.
        try:
            self._last_rig_hamlib_id = self.config.rig.hamlib_id
        except Exception:
            self._last_rig_hamlib_id = None
        await self._apply_tx_power_safety_floor("boot")
        # boot_mode wiederherstellen — auto_answer/auto_cq lebten bisher
        # nur in-memory und gingen bei jedem Service-Restart verloren.
        # Sebastians Beobachtung: "Hunting plötzlich aus".
        bm = self.config.operating.boot_mode
        if bm == "hunt":
            self.state_machine.set_auto_answer(True)
        elif bm == "cq":
            # Im CQ-Modus zünden wir den Run NICHT automatisch beim Boot
            # — der State-Machine braucht einen Hardware-Guard-Check, der
            # erst nach dem ersten Slot-Tick zuverlässig läuft. Wir
            # setzen nur das auto_cq-Flag; der nächste Slot kümmert
            # sich um den ersten TX.
            self.state_machine.ctx.auto_cq = True
            log.info("boot_mode=cq → auto_cq flag set, CQ resumes on next slot")
        # FT4 mode needs a 7.5-s slot clock. The default-constructed
        # SlotClock is 15-s (FT8); recreate it for FT4. Tests inject a
        # FakeSlotClock with its own slot_seconds, so only swap when
        # the caller left us the real-time SlotClock default.
        if isinstance(self.slot_clock, SlotClock) and self.config.operating.mode == "FT4":
            from .slot_clock import FT4_SLOT_SECONDS
            self.slot_clock = SlotClock(slot_seconds=FT4_SLOT_SECONDS)
            log.info(
                "FT4 mode active (Audit F6 v0.4.0): 7.5s slots, "
                "decode_slot_ft4 + synth_message_ft4 wired."
            )
        # rig.connect() can fail if rigctld isn't running yet (e.g. rig not
        # plugged in at boot). Don't crash the orchestrator over it — the
        # rig_poll_loop's _ensure_connected reconnects automatically when
        # rigctld comes online, and the rig snapshot stays at its disconnected
        # defaults in the meantime.
        try:
            await self.rig.connect()
        except Exception as exc:
            log.warning("rig.connect() failed at boot (rigctld down?): %s — will retry in poll loop", exc)
        # Sebastian v0.4.2: nach erfolgreichem Connect Rig-Dial auf
        # die mode-passende Sub-Band-Freq setzen. FT4 hat eigene Sub-
        # Bänder (z.B. 14.080 vs 14.074 MHz auf 20m). Ohne diesen
        # Hook bliebe der Pi auf der FT8-Dial-Freq auch bei mode=FT4.
        try:
            await self._ensure_dial_matches_mode("boot")
        except Exception as exc:
            log.warning("boot ensure_dial_matches_mode failed: %s", exc)
        # Sebastian-Bugfix v0.5.1: DecodePipeline-Band-Resolver wireup.
        # Vorher war band_hint statisch auf config.bands[0].name gesetzt
        # → bei Sebastian's Config (bands=[20m,15m,...]) wurden alle
        # Decodes (und damit alle QSO-Log-Eintraege + ntfy-Pushes) als
        # "20m" geloggt obwohl der Pi auf 15m FT4 stand. Resolver
        # liefert live den aktiven Band-Namen aus rig.freq_hz.
        if hasattr(self.decode_source, "band_resolver"):
            self.decode_source.band_resolver = self._resolve_current_band_name
        self._bg_tasks.append(asyncio.create_task(self.gps.run_forever(), name="gpsd"))
        self._bg_tasks.append(asyncio.create_task(self._slot_loop(), name="slot-loop"))
        self._bg_tasks.append(asyncio.create_task(self._rig_poll_loop(), name="rig-poll"))
        if self.config.operating.mode_watchdog_min > 0:
            self._bg_tasks.append(asyncio.create_task(
                self._mode_watchdog_loop(), name="mode-watchdog"
            ))
        self._bg_tasks.append(asyncio.create_task(
            self._daily_summary_loop(), name="daily-summary"
        ))
        self._bg_tasks.append(asyncio.create_task(
            self._dx_cluster_hint_loop(), name="dx-cluster-hint"
        ))
        if (
            self.db_enabled
            and self.config.integrations.qrz.logbook_auto_upload
            and self.config.integrations.qrz.logbook_api_key
        ):
            self._bg_tasks.append(asyncio.create_task(
                self._qrz_logbook_drain_loop(), name="qrz-logbook-drain"
            ))
        if (
            self.config.integrations.qrz.logbook_api_key
        ):
            self._bg_tasks.append(asyncio.create_task(
                self._qrz_logbook_sync_loop(), name="qrz-logbook-sync"
            ))
        # v0.10.0: PSK-Reciprocity-Refresh — alle paar Minuten fetchen
        # wer uns recently gehört hat. Nur wenn explizit aktiviert (Default
        # aus) UND PSK-Client überhaupt enabled ist (kein Punkt zu fetchen
        # ohne Upload-Counterpart).
        if (
            self.config.operating.psk_reciprocity_enabled
            and self.integrations.psk_reporter is not None
            and self.integrations.psk_reporter.enabled
        ):
            self._bg_tasks.append(asyncio.create_task(
                self._psk_reciprocity_refresh_loop(), name="psk-reciprocity-refresh"
            ))

        # v0.13.0 — Blitzortung: WS-Reader + Storm-Watchdog. Beide nur
        # wenn der User die Integration aktiviert hat. WS-Reader feedet
        # das in-memory Strike-Ringbuffer im BlitzortungClient, der
        # Watchdog pollt periodisch is_storm_nearby(gps) und schickt
        # ntfy bei Treffer (mit Throttle).
        if (
            self.config.integrations.blitzortung.enabled
            and self.integrations.blitzortung is not None
        ):
            self._bg_tasks.append(asyncio.create_task(
                self._blitzortung_ws_loop(), name="blitzortung-ws"
            ))
            self._bg_tasks.append(asyncio.create_task(
                self._blitzortung_watchdog_loop(), name="blitzortung-watchdog"
            ))

        # v0.14.0 — Solar-Refresh-Loop. hamqsl-Cache hat 30 min TTL, wir
        # holen alle 30 min frisch und spiegeln in _band_conditions_*.
        # Wenn hamqsl-Integration aus → leere Conditions → Tier `band_open`
        # liefert 0 (no boost), kein Crash.
        if self.integrations.hamqsl is not None and self.integrations.hamqsl.enabled:
            self._bg_tasks.append(asyncio.create_task(
                self._solar_refresh_loop(), name="solar-refresh"
            ))

        # v0.19.0 — DXpedition-Schedule-Sync (Watchlist-Auto-Add + ntfy
        # 24h-Reminder). Laeuft immer, harmlos wenn Schedule leer.
        self._bg_tasks.append(asyncio.create_task(
            self._dxpedition_schedule_loop(), name="dxpedition-schedule"
        ))
        # v0.19.1 — NG3K Auto-Import (alle 6h). Manuelle Eintraege
        # werden NICHT ueberschrieben (source-Feld unterscheidet).
        self._bg_tasks.append(asyncio.create_task(
            self._dxped_ng3k_import_loop(), name="dxped-ng3k-import"
        ))

        # systemd liveness-watchdog: nur schedulen wenn unter systemd
        # mit NotifyAccess+WatchdogSec gestartet (NOTIFY_SOCKET env).
        # In Tests / Workstation-runs ist die env nicht da → wir sparen
        # uns den Task komplett. sdnotify.notify() wäre eh no-op,
        # aber wir wollen auch keinen idlen Task.
        if os.environ.get("NOTIFY_SOCKET"):
            self._sd = sdnotify.SystemdNotifier()
            self._bg_tasks.append(asyncio.create_task(
                self._sd_heartbeat_loop(), name="sd-heartbeat"
            ))
            # READY=1 signalisiert systemd: orchestrator ist fully
            # wired, bg tasks laufen, FastAPI gleich am yield. Type=
            # notify-unit wartet darauf bevor "active" gemeldet wird.
            self._sd.notify("READY=1")
            log.info("sd_notify: READY=1 (systemd liveness aktiv, WATCHDOG alle 10s)")

    async def _sd_heartbeat_loop(self) -> None:
        """Pingt systemd alle 10 s damit WatchdogSec=30 nicht greift.

        1/3 des WatchdogSec-Werts ist die übliche Empfehlung — gibt 2
        verpasste heartbeats Spielraum.

        BEWUSST KEIN try/except außen rum. Wenn der event-loop hängt
        oder diese task selbst stirbt, MUSS systemd den Prozess killen
        — das ist genau der Sinn eines liveness-watchdogs. Defensive
        catch hier würde die Detektion blind machen.
        """
        while True:
            await asyncio.sleep(10)
            self._sd.notify("WATCHDOG=1")

    def _init_integrations(self) -> None:
        """Wire up online integration clients from AppConfig.

        Failures here are non-fatal: any client we can't construct stays
        ``None`` and the routes return empty payloads.
        """
        i = self.config.integrations
        c = IntegrationContainer()
        try:
            c.qrz = QrzClient(
                user=i.qrz.user, password=i.qrz.password, enabled=i.qrz.enabled
            )
        except Exception as exc:
            log.warning("qrz client init: %s", exc)
        try:
            c.hamqth = HamQthClient(
                user=i.hamqth.user, password=i.hamqth.password, enabled=i.hamqth.enabled
            )
        except Exception as exc:
            log.warning("hamqth client init: %s", exc)
        try:
            c.hamqsl = HamQslClient(enabled=i.hamqsl.enabled)
        except Exception as exc:
            log.warning("hamqsl client init: %s", exc)
        try:
            c.psk_reporter = PskReporterClient(
                enabled=i.psk_reporter.enabled,
                upload_decodes=i.psk_reporter.upload_decodes,
                my_call=self.config.operator.callsign,
                my_grid=self.config.operator.default_locator or "",
            )
        except Exception as exc:
            log.warning("psk_reporter client init: %s", exc)
        try:
            c.blitzortung = BlitzortungClient(
                enabled=i.blitzortung.enabled,
                alarm_radius_km=i.blitzortung.alarm_radius_km,
            )
        except Exception as exc:
            log.warning("blitzortung client init: %s", exc)
        try:
            c.ntfy = NtfyClient(
                topic=i.ntfy.topic, server=i.ntfy.server, enabled=i.ntfy.enabled
            )
        except Exception as exc:
            log.warning("ntfy client init: %s", exc)
        # DX cluster — opt-in via integrations.dx_cluster.enabled.
        # DxClusterClient.start() creates its own inner task, so we just
        # await it here (returns quickly after spawning the reader task).
        try:
            dxc = self.config.integrations.dx_cluster
            c.dx_cluster = DxClusterClient(
                callsign=self.config.operator.callsign,
                host=dxc.host,
                port=dxc.port,
                enabled=dxc.enabled,
            )
            if dxc.enabled:
                log.warning("starting DX cluster reader: %s:%d", dxc.host, dxc.port)
                asyncio.create_task(c.dx_cluster.start(), name="dx-cluster-init")
        except Exception as exc:
            log.warning("dx_cluster client init: %s", exc)
        # cty.dat — offline DXCC lookup. Looks for data/cty.dat next to
        # the repo root. Failure is silent (UI degrades to no country flag).
        try:
            cty_path = (
                __import__("pathlib").Path(__file__).resolve().parents[3]
                / "data" / "cty.dat"
            )
            if cty_path.is_file():
                c.cty = CtyDat.load(cty_path)
                log.info("cty.dat loaded (%d entries)", len(c.cty))
        except Exception as exc:
            log.warning("cty.dat load failed: %s", exc)
        self.integrations = c

    def _sync_global_integrations_from_operator(self, op: "OperatorConfig") -> None:
        """Globale integrations.qrz + integrations.ntfy aus dem aktiven
        Operator-Profil spiegeln. Das Frontend liest die globalen
        Felder fuer die "Online-Dienste"-Sektion in der Konfig, der
        QRZ-Logbook-Drain ebenfalls. Damit der Switch wirklich auf
        die Operator-spezifischen Credentials geht, syncen wir hier.

        ntfy-Topic wird per Convention auf ``ft8-{callsign.lower()}``
        gesetzt — pro Operator eigener ntfy-Stream, sonst mischen
        sich die Push-Notifications mehrerer User.
        """
        qrz = self.config.integrations.qrz
        if op.qrz_user is not None:
            qrz.user = op.qrz_user
        if op.qrz_password is not None:
            qrz.password = op.qrz_password
        if op.qrz_logbook_api_key is not None:
            qrz.logbook_api_key = op.qrz_logbook_api_key
        # ntfy-Topic pro Operator (lower-case, kein Slash etc.)
        ntfy = self.config.integrations.ntfy
        ntfy.topic = f"ft8-{op.callsign.lower()}"

    async def switch_operator(self, callsign: str) -> None:
        """Aktiven Operator wechseln (Hot-Swap, Sebastian 2026-05-23).

        Wirkung:
        - active_callsign in der Config wird gesetzt
        - laufender QSO wird abgebrochen (falls einer aktiv)
        - state_machine.ctx wird mit neuer Callsign + Locator + leerer
          Blacklist reinitialisiert
        - _worked_calls / _worked_grids werden neu aus DB geladen
          (gefiltert auf den neuen Operator)
        - QRZ-Client wird mit den neuen Credentials neu initialisiert
        - on_config_changed pflegt die State-Mirror-Felder nach

        Wenn der target callsign nicht in self.config.operators existiert
        → ValueError.
        """
        target = callsign.upper().strip()
        cfg = self.config
        if target not in {op.callsign for op in cfg.operators}:
            raise ValueError(f"unknown operator {target!r}")
        if target == cfg.active_callsign:
            return  # no-op
        log.info("switch_operator: %s → %s", cfg.active_callsign, target)
        # Laufenden QSO abbrechen (sonst inkonsistent in DB)
        if self.state_machine.qso is not None:
            self.state_machine.qso = None
            from ..statemachine.states import State as _S
            self.state_machine.state = _S.IDLE
            self.state_machine._pending.append(Action("STOP_TX", {}))
        # Active-Pointer setzen
        cfg.active_callsign = target
        new_op = cfg.operator  # die Property zeigt jetzt auf den neuen
        # State-Machine-Context aktualisieren
        self.state_machine.ctx.callsign = new_op.callsign
        if new_op.default_locator:
            self.state_machine.ctx.my_grid = new_op.default_locator
        # Globale Integrations-Config aus dem aktiven Operator spiegeln
        # (Sebastian sah 2026-05-23: nach Switch zu DO3XR zeigte die UI
        # immer noch DK9XR's QRZ-Credentials, weil die UI/integrations
        # auf cfg.integrations.qrz lesen, das war beim Switch nicht
        # aktualisiert worden).
        self._sync_global_integrations_from_operator(new_op)
        # Worked-Sets + Blacklist neu hydratisieren (auf neuen User filtern)
        await self._hydrate_from_db()
        # Integrations (QRZ, ntfy) neu mit den per-Operator-Credentials
        self._init_integrations()
        self._tx_power_w = new_op.default_power_w
        # Safety-Floor auch beim Operator-Wechsel: wenn default_power_w
        # des neuen Operators ueber effective_max/2 liegt, runter clampen.
        # Sebastian 2026-05-24 (v0.2.3).
        await self._apply_tx_power_safety_floor("operator_switch")
        log.info(
            "switch_operator done: active=%s worked=%d blacklist=%d",
            new_op.callsign, len(self._worked_calls),
            len(self.state_machine.ctx.blacklist),
        )

    async def _hydrate_from_db(self) -> None:
        """Load persistent state (worked calls + blacklist) from DB on boot.

        Multi-Operator (Sebastian 2026-05-23): alle Queries werden auf
        den aktiven Callsign gefiltert. So sieht DK9XR nur seine eigenen
        QSOs/Blacklist/worked-Grids, DL2XYZ nur seine. Bei Hot-Switch
        ruft :meth:`switch_operator` den Hydrate erneut auf.
        """
        if not self.db_enabled:
            return
        # Vor dem Hydrate alle Sets leeren — wichtig fuer Hot-Switch
        self._worked_calls = set()
        self._worked_grids = set()
        self._worked_grid_band = set()
        self._worked_call_band = set()  # v0.17.0
        # _worked_dxccs wurde frueher nur in-Session aufgebaut → nach Restart
        # zeigte der DXCC-Only-Filter alles als "neu" bis ein Land in der
        # aktuellen Session geloggt wurde. Sebastian 2026-05-23: jetzt aus
        # der DB rehydratisiert, gefiltert auf den aktiven Operator.
        self._worked_dxccs = set()
        # v0.10.0 Hunt-Priority-Tier "new_dxcc_band" (5BWAS) — wird beim
        # Hydrate aus der DB rekonstruiert (siehe unten).
        self._worked_dxcc_band = set()
        my_call = self.config.operator.callsign
        try:
            async with session_scope() as s:
                # worked calls — nur die unseres aktiven Operators
                rows = (await s.execute(
                    select(Qso.call).where(Qso.user_callsign == my_call).distinct()
                )).scalars()
                self._worked_calls = {c.upper() for c in rows if c}
                # v0.7.0 Build 2: Hint-Decoder profitiert von known-calls
                # im C-Shim-Hash-Table. Pushe worked-Calls rein — dort
                # werden sie als "known" benutzt fuer marginal-decode-
                # validation. n22=0 weil wir nur als Validation-Set
                # nutzen, nicht fuer Hash-Resolution.
                try:
                    from ..decode.ft8_native import lib
                    for c in self._worked_calls:
                        if c and len(c) <= 13:
                            lib.ft8_shim_hash_table_save(c.encode("ascii"), 0)
                    log.info(
                        "Hint-Decoder: %d worked-Calls in C-Shim-Hash-Table gepusht",
                        len(self._worked_calls),
                    )
                except Exception as exc:
                    log.warning("Hint-Decoder hash-push failed: %s", exc)
                # worked grids + grid-band combos
                grid_rows = (
                    await s.execute(
                        select(Qso.grid_rcvd, Qso.band).where(
                            Qso.user_callsign == my_call,
                            Qso.grid_rcvd.isnot(None),
                        )
                    )
                ).all()
                for grid_full, band in grid_rows:
                    g4 = (grid_full or "")[:4].upper()
                    if len(g4) == 4:
                        self._worked_grids.add(g4)
                        if band:
                            self._worked_grid_band.add((g4, band))
                # blacklist — nur des aktiven Operators
                bl_rows = (await s.execute(
                    select(DbBlacklist.call).where(DbBlacklist.user_callsign == my_call)
                )).scalars()
                self.state_machine.ctx.blacklist = {c.upper() for c in bl_rows if c}
                # v0.14.0 Watchlist — pro Operator isoliert. Sets im
                # Orchestrator und in ctx; In-Memory-Sync, DB ist die
                # Wahrheit.
                wl_rows = (await s.execute(
                    select(DbWatchlist.call).where(DbWatchlist.user_callsign == my_call)
                )).scalars()
                self._watchlist_calls = {c.upper() for c in wl_rows if c}
                # v0.17.0 — Watchlist-Calls in den Hint-Decoder-Hash-Table
                # pushen damit marginal-decodes (z.B. -22 dB SNR fuer
                # DXpeditions) eher mit-gefunden werden. Selbe Logik wie
                # fuer worked-Calls, nur fuer die wirklich-wichtigen.
                try:
                    from ..decode.ft8_native import lib as _ft8_lib
                    for c in self._watchlist_calls:
                        if c and len(c) <= 13:
                            _ft8_lib.ft8_shim_hash_table_save(
                                c.encode("ascii"), 0,
                            )
                    if self._watchlist_calls:
                        log.info(
                            "Hint-Decoder: %d Watchlist-Calls in Hash-Table gepusht",
                            len(self._watchlist_calls),
                        )
                except Exception as exc:
                    log.warning("watchlist hint-push failed: %s", exc)
                # v0.15.0 Soft-Blacklist aus call_reputation rekonstruieren
                self._soft_blacklist = set()
                rep_rows = (await s.execute(
                    select(
                        DbCallReputation.call,
                        DbCallReputation.score,
                        DbCallReputation.attempts,
                    ).where(DbCallReputation.user_callsign == my_call)
                )).all()
                for c_, score, attempts in rep_rows:
                    if (c_ and (score or 0) >= self._SOFT_BLACKLIST_THRESHOLD
                            and (attempts or 0) >= self._MIN_ATTEMPTS_FOR_BLACKLIST):
                        self._soft_blacklist.add(c_.upper())
                if self._soft_blacklist:
                    log.info(
                        "Soft-Blacklist hydratisiert: %d Calls",
                        len(self._soft_blacklist),
                    )
                # v0.16.0 Hour-of-Day-Aggregat aus QSO-Historie. Geht
                # ueber alle eigenen QSOs, mapped call → continent ueber
                # cty.dat (sync-Lookup im selben Loop), zählt pro
                # (continent, hour) und nimmt die Top-50% Stunden je
                # Continent als "aktiv".
                self._active_continent_hours = await self._aggregate_active_hours(s, my_call)
                # v0.18.0 — Freq-Reputation aus DB hydratisieren (kein
                # Operator-Filter, das Set ist Pi-global).
                self._freq_reputation = {}
                fr_rows = (await s.execute(
                    select(
                        DbFreqReputation.band,
                        DbFreqReputation.audio_bin_hz,
                        DbFreqReputation.attempts,
                        DbFreqReputation.successes,
                    )
                )).all()
                for band_, bin_, att, succ in fr_rows:
                    if band_ and bin_ is not None:
                        self._freq_reputation[(band_, int(bin_))] = (
                            int(att or 0), int(succ or 0),
                        )
                if self._freq_reputation:
                    log.info(
                        "Freq-Reputation hydratisiert: %d (band, bin) tracked",
                        len(self._freq_reputation),
                    )
            # DXCC-Set aus den geloggten Calls ableiten. Wir queryen die
            # DB *ausserhalb* des session_scope, weil der cty-Lookup
            # synchron + potenziell langsam (4500 Eintraege) ist und wir
            # die DB-Connection nicht so lang halten wollen.
            cty = getattr(self.integrations, "cty", None)
            if cty is not None:
                # v0.10.0: gleichzeitig 5BWAS-Set bauen (DXCC × Band).
                # Wir brauchen die call→band-Verknüpfung — dafür nochmal
                # die Qso-Rows mit (call, band) holen.
                try:
                    async with session_scope() as s2:
                        dxcc_band_rows = (await s2.execute(
                            select(Qso.call, Qso.band).where(
                                Qso.user_callsign == my_call,
                                Qso.call.isnot(None),
                                Qso.band.isnot(None),
                            )
                        )).all()
                except Exception:
                    dxcc_band_rows = []
                for call_, band_ in dxcc_band_rows:
                    if not (call_ and band_):
                        continue
                    # v0.17.0 Buddy-Seen-Tier: (call, band) Set unabhaengig
                    # vom cty-Lookup — direkt aus den QSO-Rows.
                    self._worked_call_band.add((call_.upper(), band_))
                    try:
                        rec = cty.lookup(call_)
                        if rec is not None and rec.entity is not None:
                            entity_name = rec.entity.name
                            self._worked_dxccs.add(entity_name)
                            self._worked_dxcc_band.add((entity_name, band_))
                    except Exception:
                        # cty.dat-Lookup-Fehler einzelner Calls darf den
                        # gesamten Hydrate nicht killen — skip silently
                        pass
                log.info(
                    "Worked-Sets: %d Calls, %d DXCC, %d DXCC-Band combos (5BWAS)",
                    len(self._worked_calls),
                    len(self._worked_dxccs),
                    len(self._worked_dxcc_band),
                )
            # v0.10.0: Marinefunker-Set laden für Tier "marine"/"marine_psk"
            try:
                from ..integrations.mf_lookup import all_members
                self._marine_calls_cache = {
                    m.call.upper() for m in all_members() if getattr(m, "active", True)
                }
                log.info("Marinefunker-Set: %d aktive Mitglieder geladen", len(self._marine_calls_cache))
            except Exception as exc:
                log.warning("mf_lookup load failed: %s — marine tier disabled", exc)
                self._marine_calls_cache = set()
            # In den State-Machine-Context spiegeln — der Picker liest's
            # pro Slot via _tier_marine.
            self.state_machine.ctx.marine_calls = self._marine_calls_cache
            self.state_machine.ctx.worked_dxcc_band = self._worked_dxcc_band
            # v0.10.2: VUCC-Tracking (worked grids) — Picker-Tiers new_grid +
            # new_grid_band lesen das. Direkte Set-Referenz; updates fließen
            # automatisch durch (worked_grids ist mutable shared).
            self.state_machine.ctx.worked_grids = self._worked_grids
            self.state_machine.ctx.worked_grid_band = self._worked_grid_band
        except Exception as exc:
            log.warning("hydrate_from_db failed: %s — starting with empty sets", exc)

    async def stop(self) -> None:
        for t in self._bg_tasks:
            t.cancel()
        for t in self._bg_tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        self._bg_tasks.clear()
        await self.rig.close()
        await self.gps.close()

    def status(self) -> OrchestratorStatus:
        # Aktives Band aus rig.freq_hz ableiten. Sebastian-Bugfix v0.4.3:
        # mode-aware — matcht gegen FT8-Dial UND FT4-Subband (vorher nur
        # band.freq_khz, der FT4-Subband 14.080 wurde nicht als 20m
        # erkannt und active_band blieb None → Antennen-Guard locked).
        active_band: str | None = None
        effective_max: int | None = None
        if self._last_rig.freq_hz is not None:
            freq_khz = self._last_rig.freq_hz / 1000.0
            for band in self.config.bands:
                for candidate_khz in (band.freq_for_mode("FT8"),
                                       band.freq_for_mode("FT4")):
                    if abs(candidate_khz - freq_khz) <= 50:
                        active_band = band.name
                        effective_max = self.config.effective_max_power_w(band.name)
                        break
                if active_band is not None:
                    break
        # Bandwechsel-Detection (Sebastian 2026-05-24, v0.2.3): wenn das
        # Band wechselt, Safety-Floor neu anwenden — Per-Band-Cap kann
        # niedriger als rig-max sein. Wir feuern nur bei active_band !=
        # _last_active_band UND active_band is not None (Skip wenn rig
        # auf nicht-bekannter Frequenz parkt). Variante B clamp-down-only
        # ehrt QRP-Settings darunter weiterhin. status() ist sync, daher
        # Fire-and-Forget via create_task. Wenn kein Loop laeuft (Test-
        # Context ohne asyncio) skippen wir leise.
        if active_band is not None and active_band != self._last_active_band:
            prev_band = self._last_active_band
            # _last_active_band JETZT setzen, sonst spawnt jeder status-
            # Poll bis der Task abgearbeitet ist erneut einen Task.
            self._last_active_band = active_band
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(
                    self._apply_tx_power_safety_floor(
                        "band_change", band=active_band
                    ),
                    name=f"safety-floor-band-{active_band}",
                )
                log.info(
                    "band change detected %s -> %s, scheduled tx-power safety-floor",
                    prev_band or "?", active_band,
                )
            except RuntimeError:
                # Kein laufender Loop (z.B. sync Test-Aufruf von status()).
                # In dem Fall hat status() ohnehin nichts mit echtem Rig
                # zu tun, also kein Safety-Issue.
                pass
        # RX-Audio-Pegel aus dem ALSA-Capture-Stream — Workaround für
        # den IC-7300/Hamlib-STRENGTH-Bug. decode_source ist auf dem
        # Pi die DecodePipeline mit .slot_buffer; auf Dev/Tests-Maschine
        # ein Closure ohne Slot-Buffer (dann liefert getattr None).
        rx_audio_dbfs: float | None = None
        slot_buf = getattr(self.decode_source, "slot_buffer", None)
        if slot_buf is not None:
            try:
                rx_audio_dbfs = slot_buf.rms_dbfs_recent()
            except Exception:
                rx_audio_dbfs = None
        # Peak-Hold mit 6 dB/sec Decay. Wenn der Burst kommt, springt die
        # Anzeige sofort hoch; danach fällt sie sanft auf den Rauschpegel.
        # So sieht der Operator den echten Spitzenpegel statt eine zappelnde
        # RMS-Anzeige.
        rx_audio_dbfs_peak: float | None = self._rx_audio_dbfs_peak
        if rx_audio_dbfs is not None:
            now_ts = time.time()
            if rx_audio_dbfs_peak is None:
                rx_audio_dbfs_peak = rx_audio_dbfs
            else:
                elapsed = max(0.0, now_ts - self._rx_audio_dbfs_peak_ts)
                # Decay um 6 dB pro Sekunde, aber nicht unter aktuelles RMS.
                decayed = rx_audio_dbfs_peak - 6.0 * elapsed
                rx_audio_dbfs_peak = max(decayed, rx_audio_dbfs)
            self._rx_audio_dbfs_peak = rx_audio_dbfs_peak
            self._rx_audio_dbfs_peak_ts = now_ts
        return OrchestratorStatus(
            callsign=self.state_machine.ctx.callsign,
            state=self.state_machine.state.name,
            last_lock_reason=self.state_machine.ctx.last_lock_reason,
            cq_count=self.state_machine.ctx.cq_count,
            current_qso_call=(
                self.state_machine.qso.their_call if self.state_machine.qso else None
            ),
            last_slot_index=self._last_slot.index if self._last_slot else -1,
            last_decodes=len(self._last_decodes),
            auto_answer=self.state_machine.ctx.auto_answer,
            tx_power_w=self._tx_power_w,
            active_antenna=self._active_antenna,
            worked_count=len(self._worked_calls),
            blacklist_count=len(self.state_machine.ctx.blacklist),
            rig=self._last_rig,
            gps=self.gps.snapshot,
            audio_gain=self._audio_gain,
            last_alc_pct=self._last_alc_pct,
            auto_cq=self.state_machine.ctx.auto_cq,
            mode=self.config.operating.mode,
            # Peak-Hold-Wert anzeigen statt zappelnder RMS — der Operator
            # will den Spitzenpegel der Bursts sehen, nicht das Rauschtal
            # zwischen den Slots.
            rx_audio_dbfs=rx_audio_dbfs_peak if rx_audio_dbfs_peak is not None else rx_audio_dbfs,
            license_class=self.config.operator.license_class,
            effective_max_power_w=effective_max,
            active_band=active_band,
            # v0.6.3: Decoder-Mode sichtbar fuer Monitor + UI
            decoder_mode=getattr(self.config.operating, "decoder_mode", "standard"),
            actual_decoder_mode=getattr(self.decode_source, "decoder_mode", "standard"),
            decoder_late_slot_count=getattr(
                getattr(self.decode_source, "metrics", None),
                "late_slot_count", 0,
            ) if hasattr(self.decode_source, "metrics") else 0,
            decoder_pass_stats=_safe_get_pass_stats(),
        )

    def is_worked_before(self, call: str | None) -> bool:
        """Quick check used to annotate decodes with the worked-B4 badge."""
        return bool(call) and call.upper() in self._worked_calls

    def is_blacklisted(self, call: str | None) -> bool:
        return bool(call) and call.upper() in self.state_machine.ctx.blacklist

    def is_psk_heard_us(self, call: str | None) -> bool:
        """Quick check used to annotate decodes with the PSK-Reciprocity-Badge.

        Returns True if the given call appears in the PSK-Reporter cache —
        i.e. pskreporter.info has the station listed as having heard our
        callsign in the last refresh window. Used by the decode-list UI to
        show the 📡-badge (Sebastian v0.10.4).
        """
        return bool(call) and call.upper() in self._psk_heard_us_cache

    # ------------------------------------------------------------------ multi-color helpers
    def is_new_dxcc_for(self, call: str | None) -> bool:
        """Would working *call* count as a new DXCC entity?

        Returns False when we don't have cty.dat or can't classify the
        call — failing closed keeps the UI honest (no false "🆕" badge
        on calls we just can't look up).
        """
        if not call:
            return False
        if self.integrations.cty is None:
            return False
        rec = self.integrations.cty.lookup(call)
        if rec is None:
            return False
        return rec.entity.name not in self._worked_dxccs

    def is_new_grid(self, grid: str | None) -> bool:
        """Has this 4-char grid ever shown up in a logged QSO?"""
        if not grid:
            return False
        g4 = grid[:4].upper()
        if len(g4) != 4:
            return False
        return g4 not in self._worked_grids

    def is_new_grid_on_band(self, grid: str | None, band: str | None) -> bool:
        """Same grid as :meth:`is_new_grid` but qualified by the band.

        A "new" grid that *was* worked on a different band still counts
        as new on this band — used to flag band-fills for award chasers.
        """
        if not grid or not band:
            return False
        g4 = grid[:4].upper()
        if len(g4) != 4:
            return False
        return (g4, band) not in self._worked_grid_band

    def hardware_state(self) -> HardwareState:
        return self._hardware_state

    # ------------------------------------------------------------------ control API (called by web)
    async def _refresh_hw_for_control(self) -> None:
        """Refresh ``_hardware_state`` before any user-driven TX transition.

        Control-API calls can arrive *before* the first slot has fired
        (e.g. user hammers the CQ button two seconds after boot). The
        :attr:`_hardware_state` field starts in *failure-mode* defaults
        on purpose; this call replaces them with the current live
        snapshot so the guards judge reality, not the boot-stub values.
        """
        # Use a synthetic "now" tick — _refresh_hardware_state doesn't
        # actually use the tick parameter, only the live gps/rig state.
        synthetic_tick = SlotTick(
            index=-1,
            posix=__import__("time").time(),
            utc_start=__import__("datetime").datetime.now(
                __import__("datetime").timezone.utc
            ),
        )
        await self._refresh_hardware_state(synthetic_tick)

    async def handle_start_cq(self) -> None:
        await self._refresh_hw_for_control()
        self.state_machine.on_user_start_cq(self._hardware_state)
        await self._drain_actions()
        self._persist_boot_mode("cq")

    async def handle_stop(self) -> None:
        self.state_machine.on_user_stop()
        await self._drain_actions()
        # Stop schaltet auch hunting aus → boot_mode = off
        self._persist_boot_mode("off")

    async def handle_panic(self) -> None:
        # Panic = same as stop + force PTT off + lock to prevent retries
        self.state_machine.on_user_stop()
        await self._drain_actions()
        try:
            await self.rig.set_ptt(False)
        except Exception as exc:
            log.warning("panic: PTT off failed: %s", exc)

    async def handle_reset_lock(self) -> None:
        self.state_machine.on_user_reset_lock()

    async def handle_reply_to(self, decoded: DecodedMsg) -> None:
        await self._refresh_hw_for_control()
        self.state_machine.on_user_reply_to(self._hardware_state, decoded)
        await self._drain_actions()

    async def handle_tail_end(self, decoded: DecodedMsg) -> None:
        """v0.12.0 — manueller 🎯-Klick in der UI auf einen RR73/73-Decode."""
        await self._refresh_hw_for_control()
        self.state_machine.on_user_tail_end(self._hardware_state, decoded)
        await self._drain_actions()

    async def handle_shutdown(self) -> None:
        """Sauberes System-Shutdown via systemd. Vorher: STOP_TX, PTT
        off, ntfy-Push, dann poweroff. sebastian-NOPASSWD-sudoers
        erlaubt /sbin/poweroff."""
        import subprocess
        self.state_machine.on_user_stop()
        await self._drain_actions()
        try:
            await self.rig.set_ptt(False)
        except Exception:
            pass
        if self.integrations.ntfy and self.integrations.ntfy.enabled:
            try:
                await self.integrations.ntfy.notify(
                    "Pi wird heruntergefahren — sicher zum Stecker-Ziehen in ~30 s",
                    title="🌙 FT8 Pi: shutdown",
                    priority="default",
                    tags=["sleeping"],
                )
            except Exception:
                pass
        # +5 s damit der HTTP-Response noch rauskommt bevor systemd uns killt
        subprocess.Popen(
            ["sudo", "/sbin/shutdown", "-h", "+0", "FT8-Appliance shutdown via UI"],
            close_fds=True,
        )

    async def handle_reboot(self) -> None:
        """Sauberer System-Reboot via systemd. Identisches Pre-Cleanup
        wie handle_shutdown (STOP_TX, PTT off, ntfy-Push), dann `shutdown -r`.

        Sebastian 2026-05-26 v0.8.2: UI-Button neben Shutdown, gleicher
        Sicherheitspfad, aber Pi kommt nach ~30 s automatisch wieder
        hoch (kein Vor-Ort-Eingriff noetig).
        """
        import subprocess
        self.state_machine.on_user_stop()
        await self._drain_actions()
        try:
            await self.rig.set_ptt(False)
        except Exception:
            pass
        if self.integrations.ntfy and self.integrations.ntfy.enabled:
            try:
                await self.integrations.ntfy.notify(
                    "Pi wird neu gestartet — kommt in ca. 30 s zurück",
                    title="🔁 FT8 Pi: reboot",
                    priority="default",
                    tags=["arrows_counterclockwise"],
                )
            except Exception:
                pass
        subprocess.Popen(
            ["sudo", "/sbin/shutdown", "-r", "+0", "FT8-Appliance reboot via UI"],
            close_fds=True,
        )

    async def handle_set_auto_answer(self, enabled: bool) -> None:
        """Toggle hunting / auto-answer mode."""
        self.state_machine.set_auto_answer(enabled)
        # boot_mode aktualisieren damit der Modus einen Restart überlebt.
        # "hunt" wenn enabled, sonst "off" — auto_cq wird hier explizit
        # NICHT mit reingerechnet, das ist eigene Aktion.
        self._persist_boot_mode("hunt" if enabled else "off")

    async def persist_config(self) -> None:
        """Schreibe self.config zurück nach YAML (atomic).

        Single-Choke-Point für alle UI-Toggles die Restart-fest sein
        sollen — TX-Power, Mode, Hunting-Filter etc. Filtert computed
        Felder raus (hamlib_id, effective_max_power_w, operator) die
        Pydantic sonst beim Re-Load mit extra_forbidden ablehnt ODER
        die das Multi-Operator-Schema kaputtschreiben.

        Sebastian 2026-05-24: ``operator`` ist ein ``@computed_field``
        das den aktiven Operator als Single-Op-Block zurueckliefert
        (Kompat fuer Frontend). Beim Persist landete es VERSEHENTLICH
        zusaetzlich neben ``operators: [...]`` im File. Wenn jemand
        die Datei dann ueber PUT /api/config liest+schreibt UND dabei
        den oben stehenden ``operator:``-Block als "Single-Op-Legacy"
        interpretiert, wird die ``operators``-Liste ueberschrieben →
        Multi-Op-Setup verloren. Daher hier exkludieren.
        """
        try:
            from ..config import set_config_for_tests
            from ..config.loader import get_current_path
            set_config_for_tests(self.config)
            path = get_current_path()
            if path is None:
                return
            import yaml
            d = self.config.model_dump(
                exclude_none=True,
                exclude={
                    "rig": {"hamlib_id", "effective_max_power_w"},
                    "operator": True,  # computed_field, siehe Docstring
                },
            )
            path.write_text(
                yaml.safe_dump(d, default_flow_style=False, sort_keys=False),
                encoding="utf-8",
            )
        except Exception as exc:
            log.warning("persist_config failed: %s", exc)

    def _persist_boot_mode(self, mode: str) -> None:
        """Wrapper über persist_config: setzt nur boot_mode + speichert."""
        if self.config.operating.boot_mode == mode:
            return
        self.config.operating.boot_mode = mode  # type: ignore[assignment]
        # Sync-Variante damit der Caller (handle_start_cq etc.) nicht
        # async werden muss; schedulet die persist_config-Async-Task.
        asyncio.create_task(self.persist_config(), name="persist-boot-mode")

    async def handle_skip_qso(self) -> None:
        """Drop the current QSO without logging it. Returns to IDLE."""
        self.state_machine.on_user_skip_qso()
        await self._drain_actions()
        try:
            await self.rig.set_ptt(False)
        except Exception as exc:
            log.warning("skip_qso PTT-off failed: %s", exc)

    async def handle_blacklist_add(self, call: str, reason: str | None = None) -> None:
        """Add a callsign to the blacklist (in-memory + DB persistence)."""
        call = call.upper().strip()
        if not call:
            return
        self.state_machine.ctx.blacklist.add(call)
        try:
            async with session_scope() as s:
                exists = await s.get(DbBlacklist, call)
                if exists is None:
                    s.add(DbBlacklist(
                        call=call, added=datetime.now(UTC), reason=reason
                    ))
        except Exception as exc:
            log.warning("blacklist DB persist failed: %s", exc)

    async def handle_blacklist_remove(self, call: str) -> None:
        call = call.upper().strip()
        self.state_machine.ctx.blacklist.discard(call)
        try:
            async with session_scope() as s:
                row = await s.get(DbBlacklist, call)
                if row is not None:
                    await s.delete(row)
        except Exception as exc:
            log.warning("blacklist DB delete failed: %s", exc)

    # ------------------------------------------------------------------ watchlist (v0.14.0)
    async def handle_watchlist_add(self, call: str, note: str | None = None) -> None:
        """Add a callsign to the watchlist (in-memory + DB persist).

        Operator-Isolation: row.user_callsign = aktiver Op. Bei Hot-Switch
        sieht ein anderer Op die nicht.
        """
        call = call.upper().strip()
        if not call:
            return
        my_call = self.config.operator.callsign
        self._watchlist_calls.add(call)
        # v0.17.0 — sofort in den Hint-Decoder-Hash-Table pushen damit
        # ab dem naechsten Slot marginal-Decodes des Calls funktionieren.
        try:
            from ..decode.ft8_native import lib as _ft8_lib
            if len(call) <= 13:
                _ft8_lib.ft8_shim_hash_table_save(call.encode("ascii"), 0)
        except Exception:
            pass
        try:
            async with session_scope() as s:
                exists = await s.get(DbWatchlist, call)
                if exists is None:
                    s.add(DbWatchlist(
                        call=call,
                        user_callsign=my_call,
                        added=datetime.now(UTC),
                        note=note,
                    ))
                else:
                    # Note aktualisieren falls neuer Wert mitkam.
                    if note is not None:
                        exists.note = note
                    # Operator-Pflege falls Eintrag global verwaist war.
                    if exists.user_callsign is None:
                        exists.user_callsign = my_call
        except Exception as exc:
            log.warning("watchlist DB persist failed: %s", exc)

    async def handle_watchlist_remove(self, call: str) -> None:
        call = call.upper().strip()
        self._watchlist_calls.discard(call)
        self._watchlist_last_alert.pop(call, None)
        try:
            async with session_scope() as s:
                row = await s.get(DbWatchlist, call)
                if row is not None:
                    await s.delete(row)
        except Exception as exc:
            log.warning("watchlist DB delete failed: %s", exc)

    async def _fire_watchlist_alert(self, call: str, decoded) -> None:
        """ntfy-Push wenn ein Watchlist-Call decoded wurde, mit 1h-Throttle.

        Wird aus dem Decode-Loop aufgerufen. Decoded ist eine DecodedMsg.
        """
        ntfy = self.integrations.ntfy
        if ntfy is None or not ntfy.enabled:
            return
        norm = call.upper()
        now = time.time()
        last = self._watchlist_last_alert.get(norm, 0.0)
        if now - last < 3600:  # 1h throttle pro Call
            return
        self._watchlist_last_alert[norm] = now
        try:
            band_tag = decoded.band or self.state_machine.ctx.band
            snr_str = f"{decoded.snr_db:+d} dB" if decoded.snr_db is not None else "?"
            msg_kind = "CQ" if decoded.call_to is None else f"→ {decoded.call_to}"
            await ntfy.push(
                title=f"👀 Watchlist: {norm}",
                message=f"{msg_kind} auf {band_tag} ({snr_str})",
                priority="default",
                tags="eyes",
            )
            log.info("Watchlist-Alert: %s decoded on %s (%s)", norm, band_tag, snr_str)
        except Exception as exc:
            log.warning("watchlist ntfy push failed for %s: %s", norm, exc)

    # ------------------------------------------------------------------ tamper detection helpers
    _ECHO_WINDOW_S = 3.0

    def _register_app_command(self, key: str, value) -> None:
        """Markiere dass wir gerade *value* via CAT gesendet haben.

        Wird vom rig-Poll-Sync benutzt um Echo (= eigener Befehl der jetzt
        am Rig sichtbar wird) von Tamper (= externe Aenderung am Frontpanel
        oder durch andere Software) zu unterscheiden. Sebastian 2026-05-24.
        """
        self._recent_app_commands[key] = (value, time.monotonic())

    def _is_app_echo(self, key: str, rig_value, tolerance: float = 0) -> bool:
        """True wenn der jetzt vom Rig gemeldete Wert unserem letzten
        App-Befehl entspricht (innerhalb ``_ECHO_WINDOW_S`` Sekunden).
        """
        entry = self._recent_app_commands.get(key)
        if entry is None:
            return False
        expected, ts = entry
        if time.monotonic() - ts > self._ECHO_WINDOW_S:
            return False
        if tolerance and isinstance(expected, (int, float)) \
                and isinstance(rig_value, (int, float)):
            return abs(rig_value - expected) <= tolerance
        return rig_value == expected

    async def _ensure_dial_matches_mode(self, reason: str) -> None:
        """Stell Rig-Dial auf die mode-passende Sub-Band-Freq.

        Sebastian-Audit v0.4.2: FT4 hat eigene Sub-Bänder (z.B. 14.080
        statt 14.074 MHz auf 20m). Beim Mode-Switch im UI muessen wir
        das Rig auf die richtige Dial-Frequenz fuer den aktiven Mode
        bringen, sonst hoert der FT4-Decoder weiterhin auf der FT8-
        Frequenz und sieht nichts.

        Sebastian-Bugfix v0.4.3: Defensiv — ist die aktuelle Rig-Freq
        unbekannt (Boot-Race) oder zu keinem konfigurierten Band
        matchbar, machen wir NICHTS. Vorher fiel das auf bands[0]
        zurueck und schickte das Rig auf ein zufaelliges Band — bei
        Sebastians Pi (Antenne nur 15m, bands=[20m,15m]) hat das den
        Rig auf 14.080 MHz gesetzt → Antennen-Guard locked TX +
        Tamper-Push.
        """
        mode = self.config.operating.mode
        if not self.config.bands:
            return
        current_hz = self._last_rig.freq_hz if self._last_rig else None
        if current_hz is None:
            log.info(
                "ensure_dial_matches_mode (%s): rig-freq noch unbekannt "
                "(rigctld not ready?) — skip, wird beim naechsten Mode-Event retried",
                reason,
            )
            return
        # Welches Band sind wir gerade auf? Match per ±100 kHz Toleranz
        # gegen ALLE Sub-Band-Frequenzen (FT8 + FT4) jedes konfig-Bands.
        active_band = None
        current_khz = current_hz / 1000.0
        for band in self.config.bands:
            for candidate_khz in (band.freq_for_mode("FT8"),
                                   band.freq_for_mode("FT4")):
                if abs(candidate_khz - current_khz) <= 100:
                    active_band = band
                    break
            if active_band is not None:
                break
        if active_band is None:
            log.info(
                "ensure_dial_matches_mode (%s): rig auf %.4f MHz matched "
                "kein konfiguriertes Band — skip (User koennte auf eine "
                "andere Frequenz manuell gegangen sein)",
                reason, current_hz / 1e6,
            )
            return
        target_khz = active_band.freq_for_mode(mode)
        target_hz = target_khz * 1000
        if current_hz == target_hz:
            log.debug(
                "ensure_dial_matches_mode (%s): already on %d Hz",
                reason, target_hz,
            )
            return
        log.info(
            "ensure_dial_matches_mode (%s): %s mode → set dial %d -> %d Hz "
            "(band=%s)",
            reason, mode, current_hz, target_hz, active_band.name,
        )
        try:
            await self.handle_set_freq(target_hz)
        except Exception as exc:
            log.warning(
                "ensure_dial_matches_mode: set_freq(%d) failed: %s "
                "(rig disconnected?). Will retry on next mode-event.",
                target_hz, exc,
            )

    async def handle_set_freq(self, hz: int) -> None:
        """Wrap rig.set_freq + Echo-Registration fuer Tamper-Detection."""
        await self.rig.set_freq(hz)
        self._register_app_command("freq_hz", int(hz))
        # Sebastian v0.5.2: Race-Window markieren. Der naechste rig-poll
        # sieht ggf. das Rig noch auf der ALTEN Freq (oder gerade
        # mittendrin im Sprung) und wuerde sonst einen False-Positive-
        # "Frequenz wurde verstellt"-Push feuern. Tamper-Check ignoriert
        # die ersten paar Sekunden nach diesem Set.
        self._dial_set_at = time.time()
        # SWR-Cache invalidieren: nach Band-Switch ist der gecachte
        # SWR-Wert vom alten Band stale. Sebastian 2026-05-24: nach
        # 20m-Test-Burst (SWR 2.88) hat der erste 15m-CQ den Wert
        # im _last_rig.swr noch gesehen und Runaway-Cut ausgeloest.
        if self._last_rig is not None:
            self._last_rig.swr = None
        self._swr_runaway_active = False
        self._swr_warn_since = None

    async def handle_set_mode(self, mode: str, bandwidth_hz: int = 2700) -> None:
        """Wrap rig.set_mode + Echo-Registration fuer Tamper-Detection."""
        await self.rig.set_mode(mode, bandwidth_hz)
        self._register_app_command("mode", mode)
        self._register_app_command("bandwidth_hz", int(bandwidth_hz))

    async def _notify_power_tamper(self, rig_w: int, expected_w: int) -> None:
        """Push: TX-Power wurde am Rig (extern) verstellt."""
        ntfy = self.integrations.ntfy
        if ntfy is None or not ntfy.enabled:
            return
        host = self.config.operating.public_hostname or "ft8"
        actions = [
            (
                f"http, ⏮ Auf {expected_w}W zurueck, "
                f"http://{host}:8000/api/control/tx-power, "
                "method=POST, headers.content-type=application/json, "
                f'body={{"watts":{expected_w}}}'
            ),
        ]
        await ntfy.notify(
            f"TX-Leistung am Rig auf {rig_w}W verstellt (App-Stand war "
            f"{expected_w}W). Jemand pfuscht am Rig.",
            title="🛠 Rig-Settings extern geaendert",
            priority="default",
            tags=["warning"],
            actions=actions,
        )

    async def _notify_mode_tamper(self, rig_mode: str, expected_mode: str) -> None:
        """Push: Mode wurde am Rig verstellt (z.B. PKTUSB → USB)."""
        ntfy = self.integrations.ntfy
        if ntfy is None or not ntfy.enabled:
            return
        host = self.config.operating.public_hostname or "ft8"
        actions = [
            (
                f"http, ⏮ Auf {expected_mode} zurueck, "
                f"http://{host}:8000/api/control/set-mode, "
                "method=POST, headers.content-type=application/json, "
                f'body={{"mode":"{expected_mode}"}}'
            ),
        ]
        await ntfy.notify(
            f"Rig-Modus ist {rig_mode} statt {expected_mode}. Damit funkt "
            f"FT8 nicht richtig (USB-MOD-Audio wird nicht zum Modulator "
            f"geroutet).",
            title="🛠 Rig-Modus extern geaendert",
            priority="high",
            tags=["warning"],
            actions=actions,
        )

    async def _notify_cq_idle_timeout(self, cq_count: int, elapsed_min: float) -> None:
        """Push: CQ ruft seit X min ohne Antwort. Pi macht NICHT von
        selbst aus — Sebastian entscheidet via Action-Button.
        """
        ntfy = self.integrations.ntfy
        if ntfy is None or not ntfy.enabled:
            return
        host = self.config.operating.public_hostname or "ft8"
        actions = [
            (f"http, ⏹ STOP CQ, http://{host}:8000/api/control/stop, method=POST"),
            (
                f"http, 🎯 Auf Hunting, http://{host}:8000/api/control/auto-answer, "
                "method=POST, headers.content-type=application/json, "
                'body={"enabled":true}'
            ),
        ]
        await ntfy.notify(
            f"CQ ruft seit {elapsed_min:.0f} min ohne Antwort "
            f"({cq_count} CQs gesendet). Band evtl. tot oder QRG belegt — "
            f"STOP oder auf Hunting wechseln? Pi laeuft weiter bis du was machst.",
            title="📡 CQ-Idle ohne Antwort",
            priority="default",
            tags=["warning"],
            actions=actions,
        )

    async def _notify_bandwidth_tamper(self, rig_bw: int, expected_bw: int) -> None:
        """Push: Filterbreite wurde am Rig verstellt."""
        ntfy = self.integrations.ntfy
        if ntfy is None or not ntfy.enabled:
            return
        await ntfy.notify(
            f"Filterbreite am Rig auf {rig_bw} Hz verstellt (Soll {expected_bw} Hz).",
            title="🛠 Rig-Filter extern geaendert",
            priority="default",
            tags=["warning"],
        )

    async def handle_tx_power(self, watts: int) -> None:
        """Set the rig's TX power, clamped to 1..rig.effective_max_power_w.

        Sebastian 2026-05-24: persistiert NUR ins runtime_state.json
        (zusammen mit audio_gain) — frueher haben wir den Wert in
        ``operator.default_power_w`` der YAML geschrieben, das aber
        wird auch als Slider-MAX-Cap benutzt und hat sich beim
        Runter-Schieben permanent eingebrannt. Jetzt ist die Trennung
        sauber: ``default_power_w`` = Boot-Default, runtime_state =
        aktueller Slider-Stand.
        """
        max_w = self.config.rig.effective_max_power_w
        watts = max(1, min(max_w, int(watts)))
        norm = watts / max_w  # Hamlib RFPOWER is 0.0..1.0 of rig's full scale
        try:
            await self.rig.set_rfpower(norm)
            # Echo-Registration: der naechste rig-Poll wird diesen Wert
            # zurueckmelden — markieren damit der Tamper-Detector nicht
            # ausloest.
            self._register_app_command("rfpower_norm", norm)
        except Exception as exc:
            log.warning("set_rfpower failed: %s", exc)
        self._tx_power_w = watts
        # Persistenz via runtime_state.json (selbe File wie audio_gain).
        # Throttle-Schutz steckt im _maybe_persist_runtime_state — der
        # wird ohnehin jedes Slot vom slot_loop aufgerufen, aber wir
        # forcen hier eine Persistenz weil der User explizit den
        # Slider bewegt hat.
        self._maybe_persist_runtime_state(force=True)

    def _compute_safe_default_power_w(self, band: str | None = None) -> int | None:
        """Effective-max / 2 als sicherer Default fuer das gegebene Band.

        Sebastian 2026-05-24 (v0.2.3): Safety-Floor-Default. Bei
        Reset-Events (Boot, Operator-Wechsel, Rig-Wechsel, Bandwechsel)
        nehmen wir max(1, effective_max // 2) als Obergrenze.

        v0.4.5 (Sebastian-Bug 1W-Clamp): wenn das Band fuer die Klasse
        gar nicht freigegeben ist (effective_max == 0), gibt es KEINEN
        sinnvollen Safety-Floor — Senden ist eh durch antenna_guard /
        license_guard blockiert. Wir returnen None damit der Caller
        skipt statt den User-Slider auf 1W zu kicken (war ein vergiftetes
        Geschenk: Pi wechselte spaeter aufs erlaubte Band zurueck und
        blieb bei 1W stehen).

        Wenn ``band`` None ist oder nicht in der Config existiert, fallen
        wir auf ``rig.effective_max_power_w`` zurueck (das ist der Hard-
        Cap des Rigs unabhaengig vom Band — niemals None).
        """
        eff_max: int | None = None
        if band is not None:
            try:
                eff_max = self.config.effective_max_power_w(band)
            except Exception:
                eff_max = None
        if eff_max is None:
            eff_max = self.config.rig.effective_max_power_w
        if eff_max <= 0:
            # Band nicht erlaubt fuer die Klasse → kein Floor anwenden
            return None
        return max(1, eff_max // 2)

    async def _apply_tx_power_safety_floor(
        self, reason: str, band: str | None = None
    ) -> None:
        """Clamp ``_tx_power_w`` auf safe-default WENN aktuell drueber.

        Variante B (Sebastian 2026-05-24, v0.2.3): wir greifen nur ein
        wenn die aktuelle Leistung ueber dem Safety-Floor liegt. QRP-
        Settings darunter bleiben unangetastet.

        v0.4.5: wenn das Band fuer die Klasse nicht freigegeben ist
        (safe == None), skippen wir die Floor-Anwendung komplett.

        Reasons (fuer Logs): "boot", "operator_switch", "rig_change",
        "band_change".
        """
        active_band = band if band is not None else self._last_active_band
        safe = self._compute_safe_default_power_w(active_band)
        if safe is None:
            log.info(
                "tx-power safety-floor (%s): band=%s nicht fuer Klasse "
                "freigegeben — skip (TX wird ohnehin von Guards blockiert)",
                reason, active_band or "?",
            )
            return
        if self._tx_power_w <= safe:
            log.info(
                "tx-power safety-floor (%s): aktuell %dW <= safe %dW — keine Aenderung",
                reason, self._tx_power_w, safe,
            )
            return
        old = self._tx_power_w
        log.info(
            "tx-power safety-floor (%s): clamp %dW -> %dW (band=%s)",
            reason, old, safe, active_band or "?",
        )
        # Best-effort: rig physisch auf den safe-Wert ziehen. Wenn das
        # Rig nicht erreichbar ist (z.B. beim Boot bevor rigctld up
        # ist), trotzdem den internen Wert setzen — der wird beim
        # naechsten erfolgreichen Set-Befehl ans Rig synchronisiert.
        max_w = self.config.rig.effective_max_power_w
        try:
            await self.rig.set_rfpower(safe / max_w)
            self._register_app_command("rfpower_norm", safe / max_w)
        except Exception as exc:
            log.warning(
                "tx-power safety-floor: set_rfpower failed: %s "
                "(internal value gesetzt, sync beim naechsten erfolgreichen Set)",
                exc,
            )
        self._tx_power_w = safe
        self._maybe_persist_runtime_state(force=True)

    async def handle_set_antenna(self, name: str) -> None:
        """Select the active antenna profile (drives band-lockout guard)."""
        self._active_antenna = name

    async def on_config_changed(self, new_cfg: AppConfig) -> None:
        """Hot-reload: a new config came in via /api/config PUT.

        Refresh anything that's cached from the old config:
        * active antenna might no longer exist → fall back to first
        * the state-machine context's callsign / limits change
        * online-integration clients get rebuilt with new credentials
        """
        self.config = new_cfg
        # Antenna validity
        antenna_names = {a.name for a in new_cfg.antennas}
        if self._active_antenna not in antenna_names:
            self._active_antenna = (
                new_cfg.antennas[0].name if new_cfg.antennas else None
            )
        # State-machine context: callsign + locator may have changed
        self.state_machine.ctx.callsign = new_cfg.operator.callsign
        if new_cfg.operator.default_locator:
            self.state_machine.ctx.my_grid = new_cfg.operator.default_locator
        # Operating limits → guards
        self.state_machine.limits.swr_max = new_cfg.operating.swr_max
        self.state_machine.limits.alc_max = new_cfg.operating.alc_max
        self.state_machine.qso_max_stale_slots = new_cfg.operating.qso_max_stale_slots
        self.state_machine.qso_max_cq_resends = new_cfg.operating.qso_max_cq_resends
        self.state_machine.qso_max_report_resends = new_cfg.operating.qso_max_report_resends
        self.state_machine.qso_failed_cooldown_s = float(
            new_cfg.operating.qso_failed_cooldown_min * 60
        )
        # Directed-CQ aus Config in ctx (Audit F7 v0.3.4).
        self.state_machine.ctx.cq_directed = (new_cfg.operating.cq_directed or "").upper()
        # FT8 ↔ FT4 Mode-Switch (Audit F6 v0.4.0): decode_source.mode
        # live umstellen damit der naechste Slot mit dem richtigen
        # Decoder + Window-Groesse arbeitet. Achtung: SlotClock-Tempo
        # bleibt unveraendert (running async iter), Service-Restart
        # ist fuer vollen Effekt noetig — bei Bedarf via ntfy melden.
        new_mode = new_cfg.operating.mode if new_cfg.operating.mode in ("FT8", "FT4") else "FT8"
        old_mode_for_dial = None
        if hasattr(self.decode_source, "mode") and self.decode_source.mode != new_mode:
            log.warning(
                "FT8/FT4 mode switch detected: %s -> %s. Decoder live-switched; "
                "slot_clock tempo requires service restart for full effect.",
                self.decode_source.mode, new_mode,
            )
            old_mode_for_dial = self.decode_source.mode
            self.decode_source.mode = new_mode
        # v0.6.0 Phase C: decoder_mode (standard|deep|multi) live-switch
        # auf der Pipeline ohne Restart. Pi-Last-adaptive Fallback bleibt
        # aktiv — wenn deep zu hoch laufen sollte, faellt Pipeline-Watchdog
        # selbst zurueck.
        new_decoder_mode = getattr(new_cfg.operating, "decoder_mode", "standard")
        if (hasattr(self.decode_source, "decoder_mode")
                and self.decode_source.decoder_mode != new_decoder_mode):
            log.info(
                "decoder_mode switch: %s -> %s",
                self.decode_source.decoder_mode, new_decoder_mode,
            )
            self.decode_source.decoder_mode = new_decoder_mode
            self.decode_source._consecutive_late_slots = 0
        # v0.7.0 Build 3: auto_notch_enabled live-toggle
        new_notch_enabled = getattr(new_cfg.operating, "auto_notch_enabled", True)
        has_notch_now = getattr(self.decode_source, "notch_detector", None) is not None
        if new_notch_enabled != has_notch_now:
            if new_notch_enabled:
                from ..audio.notch import NotchDetector
                self.decode_source.notch_detector = NotchDetector()
                log.info("auto-notch: ENABLED via config")
            else:
                self.decode_source.notch_detector = None
                log.info("auto-notch: DISABLED via config")
        # Sebastian v0.4.2: nach Mode-Switch Rig-Dial auf das richtige
        # Sub-Band setzen (FT4 hat eigene Frequenzen pro Band, sonst
        # hoert der FT4-Decoder weiter auf FT8-Freq).
        if old_mode_for_dial is not None:
            await self._ensure_dial_matches_mode(
                f"mode_switch:{old_mode_for_dial}->{new_mode}"
            )
        # Default TX power update too (if user changed it in the form)
        if new_cfg.operator.default_power_w != self._tx_power_w:
            self._tx_power_w = new_cfg.operator.default_power_w
        # Rig-Wechsel-Detection (Sebastian 2026-05-24, v0.2.3): wenn der
        # User in der Config einen anderen Rig-Typ (hamlib_id) gewaehlt
        # hat, triggert das den Safety-Floor — Power-Cap des neuen Rigs
        # koennte niedriger sein, und die User-Setting ist nicht mehr
        # garantiert sicher.
        try:
            new_hamlib_id = new_cfg.rig.hamlib_id
        except Exception:
            new_hamlib_id = None
        if (
            new_hamlib_id is not None
            and self._last_rig_hamlib_id is not None
            and new_hamlib_id != self._last_rig_hamlib_id
        ):
            log.info(
                "config hot-reload: rig change %s -> %s, applying tx-power safety-floor",
                self._last_rig_hamlib_id, new_hamlib_id,
            )
            await self._apply_tx_power_safety_floor("rig_change")
        self._last_rig_hamlib_id = new_hamlib_id
        # Online-integrations: tear down + rebuild
        self._init_integrations()
        # QRZ-Logbook-Drain-Loop bei Bedarf nachzünden — der ursprüngliche
        # Spawn passiert in start(), aber wenn der API-Key erst nach
        # dem Boot per Config-Save reinkommt, würde der Loop sonst nie
        # anlaufen. Wir prüfen ob er schon läuft, sonst starten wir ihn.
        drain_running = any(
            t.get_name() == "qrz-logbook-drain" and not t.done()
            for t in self._bg_tasks
        )
        if (
            self.db_enabled
            and new_cfg.integrations.qrz.logbook_auto_upload
            and new_cfg.integrations.qrz.logbook_api_key
            and not drain_running
        ):
            log.info("config hot-reload: starting QRZ-logbook-drain loop")
            self._bg_tasks.append(asyncio.create_task(
                self._qrz_logbook_drain_loop(), name="qrz-logbook-drain"
            ))
        log.info(
            "config hot-reloaded: callsign=%s antenna=%s",
            self.state_machine.ctx.callsign, self._active_antenna,
        )

    # ------------------------------------------------------------------ event subscriptions (SSE)
    def subscribe_decodes(self) -> asyncio.Queue[DecodedMsg]:
        q: asyncio.Queue[DecodedMsg] = asyncio.Queue(maxsize=200)
        self._decode_subscribers.append(q)
        return q

    def unsubscribe_decodes(self, q: asyncio.Queue[DecodedMsg]) -> None:
        with contextlib.suppress(ValueError):
            self._decode_subscribers.remove(q)

    def subscribe_status(self) -> asyncio.Queue[OrchestratorStatus]:
        q: asyncio.Queue[OrchestratorStatus] = asyncio.Queue(maxsize=20)
        self._state_subscribers.append(q)
        return q

    def unsubscribe_status(self, q: asyncio.Queue[OrchestratorStatus]) -> None:
        with contextlib.suppress(ValueError):
            self._state_subscribers.remove(q)

    # ------------------------------------------------------------------ slot loop
    _last_rig: RigSnapshot = field(default_factory=RigSnapshot, init=False)

    async def _slot_loop(self) -> None:
        try:
            async for tick in self.slot_clock:
                await self.process_slot(tick)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("slot loop crashed")
            raise

    async def process_slot(self, tick: SlotTick) -> None:
        """Public for tests — production drives this via :meth:`_slot_loop`."""
        return await self._on_slot(tick)

    async def _on_slot(self, tick: SlotTick) -> None:
        self._last_slot = tick

        # 1. refresh hardware state for guards
        await self._refresh_hardware_state(tick)

        # 2. ask decoder for this slot's decodes
        try:
            decodes = await self.decode_source(tick)
        except Exception as exc:
            log.warning("decode_source failed for slot %s: %s", tick.index, exc)
            decodes = []
        self._last_decodes = decodes
        # v0.15.0 Slot-Parity-Tracking: aktuelle Parity dieses Slots
        # in ctx setzen, dann pro Decode mit call_from die Vote-Tally
        # erhoehen. Bei klarer Praeferenz (>=3 Votes, eine Seite >=70%)
        # in _op_slot_parity einsetzen.
        slot_parity = self._compute_slot_parity(tick)
        self.state_machine.ctx.current_slot_parity = slot_parity
        if decodes:
            for d in decodes:
                if not d.call_from or d.call_from == self.config.operator.callsign:
                    continue
                # Nur "TX-Decodes" zaehlen — also nur wenn der Op tatsaechlich
                # in diesem Slot gesendet hat. Standard FT8 = jeder Decode = TX
                # in diesem Slot (RX-Slots haben keine Decodes vom selben Op).
                norm = d.call_from.upper()
                votes = self._op_slot_parity_votes.setdefault(
                    norm, {"even": 0, "odd": 0}
                )
                votes[slot_parity] = votes.get(slot_parity, 0) + 1
                total = votes["even"] + votes["odd"]
                if total >= 3:
                    if votes["even"] / total >= 0.7:
                        self._op_slot_parity[norm] = "even"
                    elif votes["odd"] / total >= 0.7:
                        self._op_slot_parity[norm] = "odd"
            # Persistenter Timestamp gegen Watchdog-Phase-Lock-Bug.
            self._last_decode_recv_at = time.time()
            # v0.14.0 Watchlist-Check: feuere ntfy-Push wenn ein
            # beobachteter Call drin ist. Throttle 1h pro Call —
            # _fire_watchlist_alert ignoriert Wiederholungen.
            if self._watchlist_calls:
                seen_in_slot: set[str] = set()
                for d in decodes:
                    for c in (d.call_from, d.call_to):
                        if not c:
                            continue
                        norm = c.upper()
                        if norm in seen_in_slot:
                            continue
                        if norm in self._watchlist_calls:
                            seen_in_slot.add(norm)
                            asyncio.create_task(
                                self._fire_watchlist_alert(norm, d)
                            )
            # v0.8.0 Build A: Hint-Decoder Live-Queue. Pushe alle frisch
            # decodierten Calls (auch unworked) in den C-Shim-Hash-Table
            # damit der Hint-Pass im naechsten Slot diese als "recently
            # active" boostet. JTDX-Recent-Decode-Bias.
            try:
                from ..decode.ft8_native import lib as _ft8_lib
                for d in decodes:
                    for c in (d.call_from, d.call_to):
                        if c and len(c) >= 3 and len(c) <= 13 and not c.startswith("<"):
                            _ft8_lib.ft8_shim_hash_table_save(
                                c.upper().encode("ascii"), 0,
                            )
            except Exception:
                pass
            # Sebastian v0.5.2: Funkstille-Single-Shot zuruecksetzen,
            # sobald wieder echte Decodes reinkommen. Damit ist beim
            # naechsten Stille-Event wieder genau EIN Push erlaubt.
            self._funkstille_push_active = False
            # v0.6.0 Phase A2: DT-Drift-Sample-Collector. Rolling-Fenster
            # ueber die letzten ~200 dt-Werte. Wenn 90%+ der Decodes
            # systematisch um >0.5s offset sind, ist unsere Clock schuld.
            for d in decodes:
                if d.dt_s is not None:
                    self._recent_dts.append(d.dt_s)
            if len(self._recent_dts) > 200:
                del self._recent_dts[: len(self._recent_dts) - 200]

        # 3. push to SSE subscribers + persist to DB (rolling 7 days)
        for d in decodes:
            self._push_decode(d)

        # v0.8.0 Build H: PSK-Reporter Upload (Community-Mehrwert).
        # Existierender Client (integrations/psk_reporter.py) hat upload_decode
        # bereits implementiert mit 5min-Flush — wir mussten ihn nur AUFRUFEN.
        # config.integrations.psk_reporter.upload_decodes (Default True) gating.
        psk = getattr(self.integrations, "psk_reporter", None)
        if psk is not None and decodes:
            try:
                rig_freq = self._last_rig.freq_hz if self._last_rig else None
                mode_str = self.config.operating.mode or "FT8"
                for d in decodes:
                    if not d.call_from or d.call_from.startswith("<"):
                        continue
                    # Reine Reception-Reports: nur Calls die wir GEHOERT
                    # haben, nicht Konversations-Partner (call_to).
                    band_hz = rig_freq if rig_freq else 0
                    if d.freq_offset_hz is not None and band_hz:
                        band_hz += int(d.freq_offset_hz)
                    if band_hz <= 0:
                        continue
                    await psk.upload_decode(
                        sender_call=d.call_from,
                        sender_grid=d.grid,
                        rx_callsign=self.config.operator.callsign,
                        snr_db=d.snr_db if d.snr_db is not None else -10,
                        band_hz=band_hz,
                        mode=mode_str,
                        decoded_at=d.ts,
                    )
            except Exception as exc:
                log.debug("psk_reporter upload skipped: %s", exc)
        if self.db_enabled and decodes:
            try:
                async with session_scope() as s:
                    for d in decodes:
                        await repository.insert_decode(
                            s,
                            ts=d.ts,
                            call_from=d.call_from,
                            call_to=d.call_to,
                            grid=d.grid,
                            message=d.message,
                            snr_db=d.snr_db,
                            dt_s=d.dt_s,
                            freq_offset_hz=d.freq_offset_hz,
                            band=d.band,
                        )
                        # Also tick the Heard table so the live map (and
                        # /api/map?mode=heard) light up. Skip CQ-side
                        # decodes that don't tell us the sender's grid:
                        # for those we wait until a later slot reveals
                        # a grid before pinning them to the map.
                        if d.call_from and d.grid:
                            await repository.upsert_heard(
                                s,
                                call=d.call_from,
                                grid=d.grid,
                                snr_db=d.snr_db,
                                now=d.ts,
                                user_callsign=self.config.operator.callsign,
                            )
            except Exception as exc:
                log.warning("decode db-write failed: %s", exc)

        # 3b. PSK-Reporter-Upload: jeden Decode mit Call+SNR puffern.
        # Der Client batcht intern alle 5 min in einem UDP-Flush.
        psk = self.integrations.psk_reporter
        if psk is not None and decodes:
            dial_hz = self._last_rig.freq_hz or 14_074_000
            for d in decodes:
                call = d.call_from
                if not call or d.snr_db is None:
                    continue
                # On-air-Frequenz = Rig-Dial + Audio-Offset
                band_hz = dial_hz + (d.freq_offset_hz or 0)
                try:
                    await psk.upload_decode(
                        sender_call=call,
                        sender_grid=d.grid,
                        rx_callsign=self.config.operator.callsign,
                        snr_db=int(d.snr_db),
                        band_hz=band_hz,
                        mode="FT8",
                        decoded_at=d.ts,
                    )
                except Exception:
                    pass  # never block the slot loop on PSK-Reporter

        # 4. drive the state machine
        self.state_machine.on_decodes(self._hardware_state, decodes)
        self.state_machine.on_slot_tick(self._hardware_state, tick)

        # 5. execute emitted actions
        await self._drain_actions()

        # 6. notify status subscribers
        self._push_status()

    async def _mode_watchdog_loop(self) -> None:
        """Push eine ntfy-Notification wenn der Mode hängt.

        Sebastians Wunsch: "ne Benachrichtigung wenn z.B. kein Auto
        Modus aktiv wäre". Wir prüfen alle 60 s ob boot_mode != "off"
        UND auto_answer + auto_cq beide False sind (= jemand hat den
        Pi gestoppt aber nicht den boot_mode geändert) — dann pingen
        wir ntfy mit Action-Buttons zum Wieder-Aktivieren.

        Zweite Bedingung: wir sind im Hunt-Mode aber haben seit X min
        weder Decodes noch QSO-Aktivität. Klingt nach toter Antenne /
        kaputtem Audio.
        """
        import time as _time
        watchdog_min = self.config.operating.mode_watchdog_min
        check_interval_s = 60.0
        last_alert_at: float = 0.0
        # Wann hat der Pi ZULETZT in einem Modus gestanden? Wird genullt
        # sobald wir Modus aktiv haben. Wenn länger als watchdog_min off
        # → Push als Erinnerung (Sebastians Wunsch: "Notify wenn x Min
        # gar kein Auto Modus aktiv").
        last_active_at: float = _time.time()
        alert_cooldown_s = 900.0  # nur 1 Alert / 15 min damit's nicht spammt

        while True:
            await asyncio.sleep(check_interval_s)
            try:
                ntfy = self.integrations.ntfy
                if ntfy is None or not ntfy.enabled:
                    continue
                # Kein Rig angeschlossen (z.B. ft8-2 als Standby-Pi ohne
                # IC-Anschluss) → Mode-Watchdog macht keinen Sinn, der
                # Pi soll ja gar nicht senden. Sebastian-Feedback
                # 2026-05-24: "boot_mode=off"-Pi pingt sonst alle 15min
                # umsonst, reine Nuisance.
                if self._last_rig.freq_hz is None:
                    continue
                now_t = _time.time()
                if now_t - last_alert_at < alert_cooldown_s:
                    continue

                bm = self.config.operating.boot_mode
                ctx = self.state_machine.ctx
                is_active = ctx.auto_answer or ctx.auto_cq
                if is_active:
                    last_active_at = now_t

                # Fall 1: User wollte hunt/cq laut boot_mode, aber beide
                # Flags sind off (Mode wurde händisch deaktiviert ohne
                # boot_mode umzuschreiben — z.B. Sebastian-Off-Button-Bug).
                if bm != "off" and not is_active:
                    await self._send_mode_alert(
                        ntfy,
                        f"Pi steht still — boot_mode={bm} aber kein Modus aktiv",
                        bm,
                    )
                    last_alert_at = now_t
                    continue

                # Fall 2: NIEMAND ist aktiv und das schon länger als
                # watchdog_min — Erinnerung dass der Pi sinnlos da steht.
                # (Sebastian-Wunsch: ping wenn boot_mode=off vergessen)
                idle_min = (now_t - last_active_at) / 60
                if not is_active and idle_min > watchdog_min:
                    await self._send_mode_alert(
                        ntfy,
                        f"Pi ist seit {idle_min:.0f} min ohne Auto-Modus. "
                        "Hunting starten?",
                        "off",
                    )
                    last_alert_at = now_t
                    last_active_at = now_t  # reset gegen Spam
                    continue

                # Fall 3: Wir sind aktiv, hatten aber lange keine Decodes
                # (Antennen-Bruch, Audio-Kabel los, Band tot).
                # Sebastian sah 2026-05-23: vorher las der Watchdog
                # _last_decodes (Slot-Snapshot). Bei CQ-Mode mit even-
                # only-TX trifft das 60s-Watchdog-Intervall genau die
                # TX-Slots (60/15=4), wo _last_decodes immer leer ist.
                # Phase-Lock → False-Positive-Funkstille. Fix: persistent
                # _last_decode_recv_at-Timestamp, der NUR beim Decode-
                # Empfang upgedated wird — nicht slot-bezogen.
                stale_min = (now_t - self._last_decode_recv_at) / 60
                if is_active and stale_min > watchdog_min and not self._funkstille_push_active:
                    # Single-Shot (Sebastian v0.5.2): pro Funkstille-
                    # Episode genau EIN Push. Flag wird erst beim
                    # naechsten echten Decode wieder zurueckgesetzt
                    # (siehe slot_loop wo _last_decode_recv_at gesetzt
                    # wird). Vorher: alle watchdog_min (=15min) Spam-
                    # Push wenn das Band nachts stundenlang tot ist.
                    await ntfy.notify(
                        f"Keine Decodes seit {stale_min:.0f} min — Antenne / Audio prüfen?",
                        title="📡 FT8 Pi: Funkstille",
                        priority="high",
                        tags=["warning"],
                    )
                    last_alert_at = now_t
                    self._funkstille_push_active = True
                    continue

                # Fall 4: CQ-Idle-Watchdog. Wir rufen seit
                # cq_idle_timeout_min Minuten CQ ohne dass jemand
                # antwortet (cq_count stieg, wurde aber nie auf 0
                # zurueckgesetzt = kein QSO durchgegangen). Sebastian
                # 2026-05-24 Audit-Finding 1: NIE State aendern, nur
                # ntfy-Push als „Hey, hier passiert nix mehr". Pi
                # ruft weiter, bis Sebastian per Action-Button
                # umschaltet oder stoppt.
                sm_state = self.state_machine.state.name
                cq_count = self.state_machine.ctx.cq_count
                cq_idle_timeout = self.config.operating.cq_idle_timeout_min
                if sm_state == "CQ_CALLING" and cq_idle_timeout > 0:
                    if cq_count == 0:
                        # QSO grad fertig oder CQ_CALLING grad erst betreten
                        # → Timer reset, Throttle reset.
                        self._cq_count_zero_at = now_t
                        self._cq_idle_alert_sent = False
                    elapsed_min = (now_t - self._cq_count_zero_at) / 60
                    if (elapsed_min >= cq_idle_timeout
                            and not self._cq_idle_alert_sent):
                        await self._notify_cq_idle_timeout(cq_count, elapsed_min)
                        self._cq_idle_alert_sent = True
                        last_alert_at = now_t
                else:
                    # Nicht in CQ_CALLING — Timer + Throttle clearen
                    # damit beim naechsten CQ-Start frisch gemessen wird.
                    self._cq_count_zero_at = 0.0
                    self._cq_idle_alert_sent = False

                # Fall 5 (v0.6.0 Phase A2): DT-Drift-Self-Diagnose. Wenn
                # 90%+ der letzten ~200 Decodes systematisch >0.5s offset
                # sind, ist UNSERE Clock schuld. WSJT-X kann das schweigend
                # produzieren ("alle Stationen sind off bei mir" = du
                # bist's). Push einmal pro Stunde — Re-Sync via chrony
                # passiert hoffentlich automatisch.
                # v0.6.1: Schwelle 0.5→1.5s erhoeht. USB-Audio + ALSA-Period-
                # Boundary erzeugt systemisch ~0.5-0.8s DT-Offset auch bei
                # perfekt sync'ter Clock (chrony <1ms). Push nur bei wirklich
                # auffaelligen Werten (>1.5s) — FT8-Toleranz ist eh 2.5s.
                # Text neutralisiert: "DT-Offset auffaellig" statt "Clock-Drift".
                # v0.8.0 Build B: DT-Auto-Kalibrierung (vor Drift-Alert!).
                # Wenn Median-DT der letzten 100+ Decodes systematic >0.3s
                # ist, applizieren wir es als negativ-Offset im SlotClock.
                # Update alle 5 min damit das Filter stabil ist.
                if (len(self._recent_dts) >= 100
                        and (now_t - self._last_dt_calibration_at) > 300):
                    sorted_dts_cal = sorted(self._recent_dts)
                    median_cal = sorted_dts_cal[len(sorted_dts_cal) // 2]
                    if abs(median_cal) > 0.3:
                        new_offset = self._dt_calibration_offset_s + median_cal
                        # Clamp: max 2s offset insgesamt (sanity)
                        new_offset = max(-2.0, min(2.0, new_offset))
                        if abs(new_offset - self._dt_calibration_offset_s) > 0.05:
                            log.info(
                                "DT-Auto-Calibration: offset %+.3fs → %+.3fs "
                                "(median_dt=%+.3fs)",
                                self._dt_calibration_offset_s, new_offset, median_cal,
                            )
                            self._dt_calibration_offset_s = new_offset
                            # Pipeline picken den Offset im naechsten Slot
                            if hasattr(self.decode_source, "dt_calibration_s"):
                                self.decode_source.dt_calibration_s = new_offset
                    self._last_dt_calibration_at = now_t
                    # Reset DT-Sample-Pool damit das naechste Fenster
                    # die NEUE Position misst (sonst kreisst Kalibrierung)
                    self._recent_dts.clear()

                if len(self._recent_dts) >= 100:
                    sorted_dts = sorted(self._recent_dts)
                    median_dt = sorted_dts[len(sorted_dts) // 2]
                    if abs(median_dt) > 1.5 and (now_t - self._last_dt_drift_alert_at) > 3600:
                        await ntfy.notify(
                            f"Median-DT der letzten {len(self._recent_dts)} Decodes "
                            f"= {median_dt:+.2f}s. Innerhalb FT8-Toleranz (2.5s), "
                            "aber ungewoehnlich — Audio-Buffer / Slot-Sync pruefen.",
                            title="⏱️ FT8 Pi: DT-Offset auffaellig",
                            priority="default",
                            tags=["warning"],
                        )
                        self._last_dt_drift_alert_at = now_t
                        last_alert_at = now_t

                # Fall 6 (v0.6.0 Phase A1): Decoder-Late-Slot-Watchdog.
                # Wenn die Pipeline 3+ konsekutive Slots >80% der Slot-
                # Laenge braucht, ist die CPU am Limit. WSJT-X verliert
                # in dem Fall stillschweigend Slots. Wir pushen.
                pipeline_metrics = getattr(self.decode_source, "metrics", None)
                if pipeline_metrics is not None and pipeline_metrics.late_slot_count >= 3:
                    if (now_t - self._last_late_slot_alert_at) > 3600:
                        await ntfy.notify(
                            f"Decoder-Late: {pipeline_metrics.late_slot_count} Slots "
                            f">80% Laufzeit (max {pipeline_metrics.max_decode_duration_s:.1f}s). "
                            "CPU vermutlich am Limit — Deep-Mode aus, andere "
                            "Loads reduzieren?",
                            title="🐢 FT8 Pi: Decoder-Last hoch",
                            priority="default",
                            tags=["warning"],
                        )
                        self._last_late_slot_alert_at = now_t
                        last_alert_at = now_t
                        pipeline_metrics.late_slot_count = 0  # Reset

            except Exception as exc:
                log.debug("mode watchdog hiccup: %s", exc)

    async def _notify_freq_tamper(self, actual_hz: int, expected_hz: int, band) -> None:
        """Push wenn jemand am VFO dreht, mit Button zum Zurückstellen."""
        ntfy = self.integrations.ntfy
        if ntfy is None or not ntfy.enabled:
            return
        delta = actual_hz - expected_hz
        host = self.config.operating.public_hostname or "ft8"
        actions = [
            (
                f"http, 🔄 Auf {band.name} zurück, "
                f"http://{host}:8000/api/control/set-freq, method=POST, "
                "headers.content-type=application/json, "
                f'body={{"freq_hz":{expected_hz}}}'
            ),
        ]
        await ntfy.notify(
            f"Rig ist auf {actual_hz/1e6:.4f} MHz ({delta:+d} Hz von "
            f"{band.name}/{band.freq_khz} kHz). Wer hat gedreht?",
            title="📻 Frequenz wurde verstellt",
            priority="high",
            tags=["warning"],
            actions=actions,
        )

    async def _send_mode_alert(self, ntfy, message: str, target_mode: str) -> None:
        """Schickt einen Mode-Alert mit Action-Buttons (Hunt/CQ-Start).

        ``target_mode`` ist nur für die Action-URLs relevant (welche
        Buttons wir anbieten). Der Titel zeigt einheitlich „Auto-Modus
        inaktiv" — Sebastian-Feedback: „off-Modus inaktiv" liest sich
        widersprüchlich, der User-mentale Begriff ist „Auto-Modus".
        """
        host = self.config.operating.public_hostname or "ft8"
        # ntfy-Action-Format: "http, <label>, <url>, method=POST, body=<json>"
        actions = [
            (
                f"http, Hunting starten, http://{host}:8000/api/control/auto-answer, "
                'method=POST, headers.content-type=application/json, '
                'body={"enabled":true}'
            ),
            (
                f"http, CQ starten, http://{host}:8000/api/control/cq, "
                "method=POST"
            ),
        ]
        await ntfy.notify(
            message,
            title=f"⚠️ FT8 {host}: Auto-Modus inaktiv",
            priority="high",
            tags=["warning"],
            actions=actions,
        )

    async def _daily_summary_loop(self) -> None:
        """Push einmal pro Tag um ~08:00 Lokalzeit eine Übersicht.

        Aggregiert die letzten 24 h aus der QSO-Tabelle und schickt eine
        Push: total QSOs, einzigartige DXCCs, beste DX, QRZ-Backlog.
        Wird auf "schon heute gesendet"-Cache gehalten damit Restart
        nicht erneut feuert.
        """
        import time as _time
        from datetime import datetime, time as dtime
        from sqlalchemy import text
        last_pushed_date: str | None = None
        while True:
            # Zwischen 07:55 und 08:05 lokal — etwas Toleranz für
            # Schlafrhythmus + Service-Restarts.
            now = datetime.now().astimezone()
            today_str = now.strftime("%Y-%m-%d")
            send_window = (
                dtime(8, 0) <= now.time() <= dtime(8, 10)
                and last_pushed_date != today_str
            )
            if send_window:
                try:
                    ntfy = self.integrations.ntfy
                    if ntfy is not None and ntfy.enabled:
                        msg = await self._build_daily_summary()
                        await ntfy.notify(
                            msg,
                            title="🌅 FT8 Pi: 24h-Übersicht",
                            priority="default",
                            tags=["sun_with_face"],
                        )
                        last_pushed_date = today_str
                        log.info("daily summary pushed: %s", msg[:80])
                except Exception as exc:
                    log.warning("daily summary failed: %s", exc)
            await asyncio.sleep(60.0)  # alle 60 s checken

    async def _build_daily_summary(self) -> str:
        """Generate the multi-line summary text from DB + state."""
        from sqlalchemy import text
        from ..db import session_scope
        async with session_scope() as s:
            row_qsos = (await s.execute(text(
                "SELECT COUNT(*), COUNT(DISTINCT call) FROM qso "
                "WHERE qso_start > datetime('now', '-24 hours')"
            ))).first()
            n_qsos = row_qsos[0] if row_qsos else 0
            n_uniq = row_qsos[1] if row_qsos else 0
            row_pending = (await s.execute(text(
                "SELECT COUNT(*) FROM qso WHERE qrz_uploaded = 0"
            ))).first()
            qrz_pending = row_pending[0] if row_pending else 0
            best = (await s.execute(text(
                "SELECT call, grid_rcvd FROM qso "
                "WHERE qso_start > datetime('now', '-24 hours') "
                "  AND grid_rcvd IS NOT NULL "
                "ORDER BY length(grid_rcvd) DESC, id ASC LIMIT 1"
            ))).first()
        # DXCC count aus _worked_dxccs (laufender Set, RAM)
        n_dxccs = len(self._worked_dxccs)
        lines = [
            f"📡 QSOs letzte 24h: {n_qsos} ({n_uniq} unique Calls)",
            f"🌍 DXCCs gesamt: {n_dxccs}",
        ]
        if best:
            lines.append(f"⭐ Best DX: {best[0]} ({best[1]})")
        if qrz_pending:
            lines.append(f"⏳ QRZ-Pending: {qrz_pending} (warten auf Connectivity)")
        else:
            lines.append("✅ Alle QSOs bei QRZ")
        return "\n".join(lines)

    async def _dx_cluster_hint_loop(self) -> None:
        """Schickt ntfy-Push wenn ein neuer DXCC-spot in der Cluster-
        Feed auftaucht. Nur Text-Hinweis, kein Auto-Band-Switch
        (Sebastian explizit so gewollt).
        """
        import time as _time
        check_interval_s = 60.0
        seen_keys: set[str] = set()
        # Cooldown pro Spot damit derselbe rare Op nicht alle 5 min
        # die App klingelt — wenn er weiter spottet, max 1×/Stunde.
        spot_cooldown: dict[str, float] = {}
        while True:
            await asyncio.sleep(check_interval_s)
            try:
                cluster = self.integrations.dx_cluster
                ntfy = self.integrations.ntfy
                if cluster is None or not cluster.enabled or ntfy is None or not ntfy.enabled:
                    continue
                cty = self.integrations.cty
                if cty is None:
                    continue
                spots = cluster.recent(ft8_only=True, minutes=10)
                now_t = _time.time()
                for spot in spots:
                    key = spot.spotted
                    if spot_cooldown.get(key, 0) > now_t:
                        continue
                    rec = cty.lookup(key)
                    if rec is None:
                        continue
                    if rec.entity.name in self._worked_dxccs:
                        continue  # Land schon im Sack — kein DXCC-Wert
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)
                    spot_cooldown[key] = now_t + 3600  # 1h pro call
                    band = spot.band or f"{spot.freq_hz / 1e6:.3f} MHz"
                    from ..integrations.flags import flag_for_call
                    spot_flag = flag_for_call(key, cty)
                    await ntfy.notify(
                        f"{key} aus {rec.entity.name} ({rec.entity.continent}) "
                        f"auf {band}.  Aktuelles Band: {self.config.bands[0].name}"
                        + (" — passt!" if (spot.band == self.config.bands[0].name) else " — Band-Wechsel nötig"),
                        title=f"🆕 DXCC-Spot: {rec.entity.name}",
                        priority="high",
                        tags=["dart"],
                        flag=spot_flag,
                    )
                    log.info("DX-Cluster-Hint pushed: %s (%s)", key, rec.entity.name)
            except Exception as exc:
                log.debug("dx-cluster-hint loop hiccup: %s", exc)

    async def _psk_reciprocity_refresh_loop(self) -> None:
        """v0.10.0: Periodisch pskreporter.info abfragen — wer hat uns gehört?

        Befüllt ``self._psk_heard_us_cache`` mit normalisierten Calls aus
        den letzten ~1h Reception-Reports. Der Hunting-Picker liest das
        pro Slot in den ctx (siehe Z. 3170-ish) und nutzt die Tiers
        "marine_psk", "new_dxcc_psk", "psk_heard_us".

        Schedule: erstmal 30s nach Boot (PSK-Server etwas warmlaufen
        lassen + nicht zur selben Sekunde wie 100 andere Pis anfragen),
        danach im konfigurierten Intervall (Default 600s = 10 min).
        Fehler werden geloggt + alter Cache behalten — Picker arbeitet
        weiter mit den letzten bekannten Spots (fail-open).
        """
        log.info("psk-reciprocity: refresh-loop started, first fetch in 30s")
        try:
            await asyncio.sleep(30)
            psk_client = self.integrations.psk_reporter
            if psk_client is None:
                log.info("psk-reciprocity: client not configured, exiting refresh loop")
                return
            # Operator-Calls die wir abfragen — Multi-Op-Setup berücksichtigen.
            # Hinweis v0.10.4: AppConfig hat operators auf TOP-Level, nicht
            # unter config.operator. Vorheriger Pfad self.config.operator.
            # operators → AttributeError → silent task death (keine logs).
            operator_calls: list[str] = []
            if self.config.operator.callsign:
                operator_calls.append(self.config.operator.callsign)
            for op in (self.config.operators or []):
                if op.callsign and op.callsign not in operator_calls:
                    operator_calls.append(op.callsign)
            log.info("psk-reciprocity: monitoring %d operator-call(s): %s",
                     len(operator_calls), operator_calls)
        except Exception as exc:
            log.error("psk-reciprocity: setup failed, refresh-loop dead: %s",
                      exc, exc_info=True)
            return
        while True:
            try:
                all_callsets: set[str] = set()
                for call in operator_calls:
                    try:
                        reports = await psk_client.who_heard_me(call, hours=1)
                    except Exception as exc:  # network/parse — log+skip
                        log.warning("psk-reciprocity: fetch failed for %s: %s", call, exc)
                        continue
                    for r in reports:
                        if r.rx_call:
                            all_callsets.add(r.rx_call.upper())
                # In-place swap — Picker liest atomar
                self._psk_heard_us_cache = all_callsets
                self._psk_last_refresh_at = time.time()
                self._psk_last_refresh_ok = True
                log.info(
                    "psk-reciprocity: %d unique stations heard us (across %d operator-call(s))",
                    len(all_callsets), len(operator_calls),
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("psk-reciprocity refresh-loop hiccup: %s", exc)
                self._psk_last_refresh_ok = False
            await asyncio.sleep(self.config.operating.psk_reciprocity_refresh_s)

    async def _solar_refresh_loop(self) -> None:
        """v0.14.0 — Periodischer hamqsl-Refresh fuer Band-Conditions.

        hamqsl-Cache hat 30 min TTL (siehe HamQslClient.cache_ttl_s) →
        wir poll'en alle 30 min damit ctx.band_conditions_day/night
        aktuell bleibt fuer den `band_open`-Tier.
        """
        await asyncio.sleep(5)  # Boot-grace
        while True:
            try:
                hamqsl = self.integrations.hamqsl
                if hamqsl is None or not hamqsl.enabled:
                    await asyncio.sleep(1800)
                    continue
                sd = await hamqsl.solar()
                if sd is not None:
                    self._band_conditions_day = dict(sd.band_conditions_day or {})
                    self._band_conditions_night = dict(sd.band_conditions_night or {})
                    self._solar_last_refresh_at = time.time()
                    log.info(
                        "solar-refresh: %d day-conditions, %d night-conditions",
                        len(self._band_conditions_day),
                        len(self._band_conditions_night),
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("solar-refresh hiccup: %s", exc)
            await asyncio.sleep(1800)

    async def _blitzortung_ws_loop(self) -> None:
        """v0.13.0: Liest den Blitzortung.org Live-WS und ingestiert Strikes
        in den ``BlitzortungClient``-Ringbuffer.

        Bewusst KEIN try/except am aeusseren Rand — der Generator selbst
        haelt sich am Leben (Reconnect-Loop intern). Wenn dieser Task
        stirbt, ist der Storm-Watchdog blind — das wollen wir in den Logs
        sehen.
        """
        from ..integrations.blitzortung_ws import stream_strikes
        bz = self.integrations.blitzortung
        if bz is None:
            return
        log.info("blitzortung: ws-reader started")
        async for strike in stream_strikes():
            bz.ingest(strike)

    async def _blitzortung_watchdog_loop(self) -> None:
        """v0.13.0: pollt is_storm_nearby alle 60s und schickt ntfy-Push
        wenn ein Strike innerhalb von ``alarm_radius_km`` liegt.

        Throttle: maximal 1 Push pro 15 min — ausser das Gewitter ist
        deutlich naeher gerueckt (>= 5 km Annaeherung), dann darf re-pushed
        werden ("Sturm kommt naeher").

        Schedule: erst 30 s nach Boot starten damit der WS-Reader etwas
        Daten sammeln kann + GPS warm wird.
        """
        log.info("blitzortung: watchdog started, first check in 30s")
        await asyncio.sleep(30)
        bz = self.integrations.blitzortung
        if bz is None:
            return
        while True:
            try:
                self._blitzortung_check_and_alert(bz)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("blitzortung watchdog hiccup: %s", exc)
            await asyncio.sleep(60)

    # Push-Throttle: 15 min Fenster. Frueher re-push nur wenn Strike um
    # mindestens 5 km naeher gerueckt ist.
    _STORM_THROTTLE_S = 15 * 60
    _STORM_CLOSER_KM = 5.0

    def _blitzortung_check_and_alert(self, bz) -> None:
        """Ein-Tick-Check des Storm-Watchdogs. Ausgelagert damit's testbar
        ist ohne den ganzen 60-s-Loop zu fahren.
        """
        if not bz.enabled:
            return
        gps = self.gps.snapshot
        if gps.lat is None or gps.lon is None:
            # Ohne GPS kein Distance-Check moeglich. Auch nicht meckern —
            # GPS-Loss hat eigenen Pfad.
            return
        here = (gps.lat, gps.lon)
        nearest_km = bz.nearest_strike_km(here)
        if nearest_km is None or nearest_km > bz.alarm_radius_km:
            return
        # Throttle-Check
        now = time.time()
        since_last = now - self._last_storm_alert_at
        if since_last < self._STORM_THROTTLE_S:
            prev = self._last_storm_alert_km
            # "Deutlich naeher" = aktuelles minimum ist >= 5 km dichter
            # als das letzte gemeldete. Sonst skip — kein Spam wenn die
            # Front in derselben Distanz hin- und herwackelt.
            if prev is not None and nearest_km > prev - self._STORM_CLOSER_KM:
                return
        # Push!
        ntfy = self.integrations.ntfy
        if ntfy is None or not ntfy.enabled:
            return
        # Distanz auf int runden — 27.3 km macht im Push keinen Mehrwert.
        km = int(round(nearest_km))
        if since_last < self._STORM_THROTTLE_S and self._last_storm_alert_km is not None:
            msg = f"Gewitter rueckt naeher — {km} km Entfernung"
        else:
            msg = f"Gewitter in {km} km Entfernung (Radius {bz.alarm_radius_km} km)"
        log.warning("blitzortung: storm alert — %s", msg)
        # Fire-and-forget — Push darf nicht den Watchdog blockieren.
        asyncio.create_task(ntfy.push(
            message=msg,
            title="Gewitterwarnung",
            priority="high",
            tags=["thunder_cloud", "warning"],
        ))
        self._last_storm_alert_at = now
        self._last_storm_alert_km = nearest_km

    async def _qrz_logbook_sync_loop(self) -> None:
        """Holt Dads komplettes QRZ-Logbook und füllt die Worked-Sets.

        Wirkung: das "noch nie gearbeitet"-Filter im Hunting-Picker
        wird akkurat — wir wissen jetzt nicht nur was DIESER Pi seit
        Boot gemacht hat, sondern auch Dads Lifetime-Historie. Stand-
        Mai-2026 sind das vermutlich tausende QSOs.

        Schedule: einmalig 60 s nach Boot (Service muss erst laufen,
        Internet warm), danach alle 24 h. Bei Fehler 5-min-Backoff.
        """
        from ..integrations.qrz_logbook import QrzLogbookError, fetch_log_adif
        api_key = self.config.integrations.qrz.logbook_api_key
        if not api_key:
            return
        # Erste Sync ein Minütchen nach Boot — gibt Netzwerk + chrony
        # Zeit sauber zu landen.
        await asyncio.sleep(60.0)
        while True:
            try:
                log.info("QRZ logbook sync starting…")
                # Mit hartem Timeout damit ein hängender QRZ-Server
                # uns nicht endlos im await-State festhält.
                records = await asyncio.wait_for(
                    fetch_log_adif(api_key, timeout=90.0),
                    timeout=120.0,
                )
                added_calls = 0
                added_dxccs: set[str] = set()
                for rec in records:
                    call = rec.get("call", "").upper().strip()
                    if not call:
                        continue
                    if call not in self._worked_calls:
                        self._worked_calls.add(call)
                        added_calls += 1
                    # DXCC: ADIF nutzt entweder Nummer (dxcc-Field) oder
                    # cty.dat-Lookup. Wir lookup'en immer selbst über
                    # cty.dat damit Konsistenz mit der Live-Klassifikation
                    # gewahrt bleibt.
                    # Band extrahieren BEVOR DXCC-Lookup damit wir's für
                    # 5BWAS-Tracking nutzen können (v0.10.0)
                    band = rec.get("band", "").lower().strip()
                    if self.integrations.cty is not None:
                        ctyrec = self.integrations.cty.lookup(call)
                        if ctyrec is not None:
                            country = ctyrec.entity.name
                            if country not in self._worked_dxccs:
                                self._worked_dxccs.add(country)
                                added_dxccs.add(country)
                            # 5BWAS: DXCC-Band-Combo immer adden, auch
                            # wenn das Land schon worked ist (jetzt vielleicht
                            # auf neuem Band).
                            if band:
                                self._worked_dxcc_band.add((country, band))
                    # Grids: 4-Char-Normalisierung
                    grid = rec.get("gridsquare", "").upper().strip()
                    if len(grid) >= 4:
                        g4 = grid[:4]
                        self._worked_grids.add(g4)
                        if band:
                            self._worked_grid_band.add((g4, band))
                log.info(
                    "QRZ logbook sync done: %d records, +%d new calls, "
                    "+%d new DXCCs (total worked: %d calls / %d DXCCs)",
                    len(records), added_calls, len(added_dxccs),
                    len(self._worked_calls), len(self._worked_dxccs),
                )
            except QrzLogbookError as exc:
                log.warning("QRZ logbook sync rejected: %s", exc)
                await asyncio.sleep(300.0)
                continue
            except Exception as exc:
                log.warning("QRZ logbook sync hiccup: %s — retry 5 min",
                            exc)
                await asyncio.sleep(300.0)
                continue
            # Erfolgreicher Sync — 24 h warten bis zum nächsten
            await asyncio.sleep(86400.0)

    async def _qrz_logbook_drain_loop(self) -> None:
        """Drain unuploaded QSOs to QRZ Logbook in the background.

        Designed for offline-tolerance: when Dad is on vacation with
        flaky 4G, the appliance just keeps logging locally; whenever
        connectivity returns we catch up the queue. Order-stable
        (oldest QSOs first), exponential backoff per row via
        qrz_upload_attempts.
        """
        from datetime import UTC, datetime, timedelta
        from sqlalchemy import select
        from ..db import session_scope
        from ..db.models import Qso
        from ..integrations.qrz_logbook import QrzLogbookError, upload_qso

        api_key = self.config.integrations.qrz.logbook_api_key
        my_call = self.config.operator.callsign
        interval_s = 300.0  # 5 min between sweeps — chatty enough, gentle on QRZ
        log.info("QRZ logbook drain loop active (interval=%.0fs)", interval_s)

        while True:
            try:
                async with session_scope() as s:
                    # Pending = not yet uploaded AND either never tried,
                    # or last try is longer ago than the back-off (the
                    # back-off grows with attempts: 5min, 15min, 1h, ...).
                    now = datetime.now(UTC)
                    rows = list(
                        (await s.execute(
                            select(Qso)
                            .where(Qso.qrz_uploaded == False)  # noqa: E712
                            .order_by(Qso.qso_start.asc())
                            .limit(20)
                        )).scalars()
                    )
                    for qso in rows:
                        backoff_s = min(3600.0, 300.0 * (2 ** qso.qrz_upload_attempts))
                        if (
                            qso.qrz_last_attempt_at
                            and (now - qso.qrz_last_attempt_at).total_seconds() < backoff_s
                        ):
                            continue
                        qso.qrz_upload_attempts += 1
                        qso.qrz_last_attempt_at = now
                        try:
                            result = await upload_qso(api_key, my_call, qso)
                        except QrzLogbookError as exc:
                            log.warning("QRZ rejected %s (%s) — won't retry",
                                        qso.call, exc)
                            # Hard reject (bad key, duplicate, etc.): mark
                            # uploaded so we don't retry forever. Stored
                            # logbook_id stays None so the operator can
                            # tell something went wrong.
                            qso.qrz_uploaded = True
                        except Exception as exc:
                            # Network / transient errors — leave for next round
                            log.info("QRZ upload deferred for %s: %s", qso.call, exc)
                        else:
                            qso.qrz_uploaded = True
                            qso.qrz_logbook_id = result.logbook_id
                            log.info("QRZ uploaded QSO %s (logid=%s)",
                                     qso.call, result.logbook_id)
            except Exception as exc:
                log.warning("QRZ drain loop hiccup: %s", exc)
            await asyncio.sleep(interval_s)

    async def _rig_poll_loop(self) -> None:
        """Refresh the cached rig snapshot every second outside slot-time.

        Also runs the PTT-stuck watchdog: if PTT has been on for longer
        than ``operating.max_ptt_s`` (i.e. an FT8 transmission should
        have ended by now), drop PTT forcefully. Catches firmware glitches
        and orchestrator-side hangs that didn't clear PTT.
        """
        ptt_on_since: float | None = None
        max_ptt_s = float(self.config.operating.max_ptt_s)
        while True:
            try:
                self._last_rig = await self.rig.snapshot()
            except Exception as exc:
                log.debug("rig snapshot failed: %s", exc)
                await asyncio.sleep(1.0)
                continue

            # TX-Power bidirektional syncen: wenn Dad am Front-Panel
            # an der Power-Pegelung drehte, ist die Rig-Anzeige die
            # echte Quelle der Wahrheit — die UI muss nachziehen,
            # sonst zeigen wir 100 W obwohl der Sender 50 W rausjagt.
            # Nur bei spürbarer Abweichung (>=5 %) updaten damit
            # Mess-Jitter nicht die Anzeige zappeln lässt.
            rfp = self._last_rig.rfpower_norm
            if rfp is not None:
                max_w = self.config.rig.effective_max_power_w
                rig_watts = max(1, int(round(rfp * max_w)))
                if abs(rig_watts - self._tx_power_w) >= max(1, max_w // 20):
                    # Echo-Check: war das unser eigener set_rfpower-Befehl
                    # aus den letzten paar Sekunden, oder hat jemand am
                    # Frontpanel gedreht?
                    is_echo = self._is_app_echo("rfpower_norm", rfp, tolerance=0.02)
                    if is_echo:
                        log.info("TX-Power sync (echo): rig confirms %dW", rig_watts)
                    elif not self._tamper_armed:
                        log.info("TX-Power initial sync: %dW (vor Tamper-Arming)",
                                 rig_watts)
                    else:
                        log.info(
                            "TX-Power sync: rig reports %dW (was %dW) — EXTERN verstellt",
                            rig_watts, self._tx_power_w,
                        )
                        # Throttle: nur EIN Push pro neuem Wert. Wenn er
                        # weiter dreht (24 → 30 → 5), kriegt jeder Schritt
                        # einen Push. Wenn er auf 24 stehen bleibt, nicht.
                        if self._last_power_alert_w != rig_watts:
                            self._last_power_alert_w = rig_watts
                            asyncio.create_task(
                                self._notify_power_tamper(rig_watts, self._tx_power_w),
                                name="power-tamper-push",
                            )
                    self._tx_power_w = rig_watts
                    # WICHTIG: hier NICHT operator.default_power_w mit
                    # ueberschreiben — das ist der persistente "max
                    # erlaubt"-Cap der Operator-Preference, nicht der
                    # Live-Wert. Frueher wurde es gemirrort und hat den
                    # Power-Slider-Max permanent runtergezogen wenn am
                    # Rig kurz auf weniger Watt gedreht wurde (Sebastian
                    # 2026-05-24: Slider klebte bei 24W trotz E-Klasse-
                    # 100W-Limit auf 15m).
                else:
                    # Wert ist wieder in der Naehe vom App-Stand — Throttle reset
                    self._last_power_alert_w = None

            # Mode-Tamper: wenn jemand am Rig von PKTUSB auf USB schaltet
            # (oder sonstwas), funkt FT8 nicht mehr — USB-Mod-Audio wird
            # vom Rig nicht zum Modulator geroutet. Catcht z.B. den
            # band-switch-Bug-Symptom retroaktiv.
            rig_mode = self._last_rig.mode
            expected_mode = "PKTUSB"  # FT8 = data mode
            if rig_mode is not None:
                if rig_mode != expected_mode:
                    if not self._is_app_echo("mode", rig_mode) and self._tamper_armed:
                        if self._last_mode_alert != rig_mode:
                            log.info("Mode-Tamper: rig=%s (Soll %s) — EXTERN verstellt",
                                     rig_mode, expected_mode)
                            self._last_mode_alert = rig_mode
                            asyncio.create_task(
                                self._notify_mode_tamper(rig_mode, expected_mode),
                                name="mode-tamper-push",
                            )
                else:
                    self._last_mode_alert = None

            # Filter-Tamper: ICom-Rigs haben 3 Filter-Slots pro Mode
            # (FIL1/2/3), oft 3600/2700/500. Alle Werte >=2000 Hz sind
            # ok fuer FT8. Wirklich problematisch ist nur ein Schmal-
            # filter (z.B. CW-500-Hz-Filter) der die meisten Decodes
            # wegschneidet. Sebastian 2026-05-24.
            rig_bw = self._last_rig.bandwidth_hz
            BW_MIN_OK = 2000   # alles drueber ist breit genug fuer FT8
            BW_MAX_OK = 6000   # alles drunter ist normal SSB-Breite
            bw_problematic = rig_bw is not None and (
                rig_bw < BW_MIN_OK or rig_bw > BW_MAX_OK
            )
            if bw_problematic:
                if not self._is_app_echo("bandwidth_hz", rig_bw, tolerance=200) \
                        and self._tamper_armed:
                    if self._last_bandwidth_alert_hz != rig_bw:
                        log.info("Filter-Tamper: bandwidth=%d Hz (Schmal-Filter?) — EXTERN",
                                 rig_bw)
                        self._last_bandwidth_alert_hz = rig_bw
                        asyncio.create_task(
                            self._notify_bandwidth_tamper(rig_bw, 2700),
                            name="bandwidth-tamper-push",
                        )
            elif rig_bw is not None:
                self._last_bandwidth_alert_hz = None

            # Boot-Gate: nach dem ersten kompletten Sync-Durchlauf
            # Tamper-Detection scharfschalten.
            if not self._tamper_armed and self._last_rig.freq_hz is not None:
                self._tamper_armed = True
                log.info("Tamper-Detection scharfgeschaltet (initial rig state geladen)")

            # Frequenz-Drift: wenn die Rig-Frequenz mehr als 100 Hz vom
            # konfigurierten Band abweicht, loggen + ntfy mit Rollback-
            # Button. Sebastian's Sicht: Dad pfuscht heimlich an der QRG,
            # Pi pingt → 1-Tap zurück.
            actual_hz = self._last_rig.freq_hz
            if actual_hz is not None and self.config.bands:
                band = self._band_for_rig_freq(actual_hz)
                if band is not None:
                    # Sebastian-Bugfix v0.4.3: Tamper-Check muss die
                    # mode-passende Sub-Band-Freq als expected nehmen,
                    # nicht starr freq_khz (FT8-Default). Sonst feuert
                    # nach unserem eigenen FT4-Mode-Switch der Push
                    # "Frequenz wurde verstellt".
                    expected_hz = band.freq_for_mode(self.config.operating.mode) * 1000
                    delta_hz = actual_hz - expected_hz
                    if abs(delta_hz) > 100:
                        last = getattr(self, "_last_logged_drift_hz", None)
                        if last is None or abs(delta_hz - last) > 50:
                            log.info(
                                "Frequenz-Drift: %d Hz (%+.0f Hz von %s/%d kHz)",
                                actual_hz, delta_hz, band.name, band.freq_khz,
                            )
                            # ntfy-Push beim ERSTEN Verlassen des
                            # Toleranzfensters — nicht bei jedem Drift-
                            # Sprung (sonst Spam beim Tunen).
                            # Sebastian v0.5.2: Skippe Push wenn wir
                            # selbst gerade per handle_set_freq die
                            # Frequenz gesetzt haben (< 5s zurueck).
                            # Der rig-poll sieht oft noch die ALTE
                            # Freq und feuert sonst False-Positive
                            # nach jedem Mode-Switch FT8<->FT4.
                            self_set_age = time.time() - self._dial_set_at
                            recently_self_set = self_set_age < 5.0
                            if last is None and not recently_self_set:
                                asyncio.create_task(self._notify_freq_tamper(
                                    actual_hz, expected_hz, band,
                                ), name="freq-tamper-push")
                            elif last is None and recently_self_set:
                                log.info(
                                    "freq-tamper-push skipped: self-set %.1fs ago",
                                    self_set_age,
                                )
                            self._last_logged_drift_hz = delta_hz
                    else:
                        if getattr(self, "_last_logged_drift_hz", None) is not None:
                            log.info(
                                "Frequenz-Drift wieder im Soll: %d Hz (Δ %+.0f)",
                                actual_hz, delta_hz,
                            )
                            self._last_logged_drift_hz = None

            # RX-Pegel-Watchdog: wenn das Audio dauerhaft (>=30 s) bei
            # ≥-3 dBFS hängt, sind wir nah am Clipping. ntfy-Push mit
            # Tipp zum USB-AF-Output-Setting am Rig. Throttle 30 min
            # damit's nicht spammt wenn Sebastian den Wert sieht aber
            # gerade nicht ans Rig kann.
            if not self._last_rig.ptt:
                # Nur während RX messen — TX-Halbduplex muted die Capture
                # eh und würde den Watchdog fälschlich resetten.
                self._check_audio_clipping()
            else:
                # SWR + ALC nur während TX sinnvoll — beide sind sonst 1.0/0.
                # Vorwarn-Stufe vor den Hard-Locks.
                self._check_swr_warn()
                self._check_alc_warn()

            now = __import__("time").monotonic()
            if self._last_rig.ptt:
                if ptt_on_since is None:
                    ptt_on_since = now
                elif now - ptt_on_since > max_ptt_s + 2.0:
                    log.warning(
                        "PTT stuck on for %.1fs > max %.1fs — forcing off",
                        now - ptt_on_since, max_ptt_s,
                    )
                    try:
                        await self.rig.set_ptt(False)
                    except Exception:
                        pass
                    self.state_machine.ctx.last_lock_reason = "PTT-stuck recovery"
                    ptt_on_since = None
            else:
                ptt_on_since = None
            # ALC closed-loop: muss jeden Tick laufen damit die PTT-
            # Abfallflanke erkannt wird (Burst-Ende = Decision-Point).
            self._apply_alc_closed_loop()
            await asyncio.sleep(1.0)

    def _apply_alc_closed_loop(self) -> None:
        """Burst-Sampler fuer den PI-Regler auf rfpower_meter.

        Sammelt waehrend PTT=on Burst-Samples von ALC (fuer den Safety-
        Watchdog + UI) und rfpower_meter (fuer den Hauptregler). Bei
        der PTT-Abfallflanke wird _apply_burst_loop_update() aufgerufen,
        das einen klassischen PI-Schritt auf dem Power-Peak fahrt.

        Sebastian + Claude haben am 2026-05-22 den vorherigen Bang-Bang-
        Regler auf ALC durch dieses Design ersetzt, weil ALC als Haupt-
        messgroesse mehrdeutig ist (peak=0% kann Sweet-Spot ODER
        Underdrive heissen). rfpower_meter ist monoton in gain.
        """
        if self._last_rig is None:
            return
        ptt_on = bool(self._last_rig.ptt)
        alc = self._last_rig.alc
        pwr_m = self._last_rig.rfpower_meter
        if ptt_on and not self._ptt_was_on:
            # PTT-Anstiegsflanke — neuen Burst beginnen, Samples leeren
            self._tx_alc_samples = []
            self._tx_pwr_samples = []
        elif ptt_on:
            if alc is not None:
                pct = int(round(max(0.0, min(1.0, alc)) * 100))
                self._tx_alc_samples.append(pct)
                # Live-Update der UI-Anzeige + Warn-Watchdog-Input
                # damit _check_alc_warn nicht auf stale Werte triggert.
                self._last_alc_pct = pct
            if pwr_m is not None:
                self._tx_pwr_samples.append(max(0.0, min(1.0, pwr_m)))
        elif not ptt_on and self._ptt_was_on:
            # PTT-Abfallflanke — Burst auswerten, einmal justieren
            self._apply_burst_loop_update()
        self._ptt_was_on = ptt_on

    def _apply_burst_loop_update(self) -> None:
        """Burst-Loop-Update: PI auf ALC mit pwr-Underdrive-Fallback.

        Architektur (Sebastian + Claude, 2026-05-22 Abend):

        1. Safety-Watchdog (immer zuerst): alc_peak > alc_safety_threshold
           → gain ×= alc_safety_factor, Integrator reset, return.

        2. Sensor-Sync-Gate: alc_peak == 0 UND pwr_peak == 0 → Hamlib
           noch nicht synchron (typisch direkt nach Service-Start) → skip.

        3. Regime-Wahl basierend auf alc_peak:
           a) alc_peak > 0 → ALC-Regime
              w = alc_target_pct / 100, y = alc_peak / 100, e = w − y.
              ALC ist im Sweet-Spot-Bereich monoton in gain (gemessen
              2026-05-22: gain 0.25..0.35 → alc 3..35) und hat klare
              Empfindlichkeit ~3 (Δalc/Δgain) — perfekte Process Variable.
           b) alc_peak == 0 UND pwr_peak < pwr_target_ratio * pwr_norm
              → PWR-Underdrive-Regime
              w = pwr_target_ratio * pwr_norm, y = pwr_peak, e = w − y.
              Das ist der Cold-Start-Fall oder massiver Underdrive, wo
              ALC noch nichts sagt aber pwr_meter klare Information gibt.
           c) alc_peak == 0 UND pwr_peak >= pwr_target_ratio * pwr_norm
              → Sweet-Spot mit Limiter inaktiv → KEIN Update.

        4. PI-Update mit klassischer Anti-Windup + I-Deadband.

        Warum nicht direkt pwr_meter als PV im Sweet-Spot? Live-Messung
        2026-05-22 abends: bei gain 0.25..0.35 saturierte pwr_meter
        auf 0.43..0.45 (Streckenverstaerkung 0.5). Mit pwr_meter als PV
        wanderte der Loop ohne stationaeren Punkt, weil der Setpoint
        physikalisch unerreichbar war. ALC dagegen ist hier exzellent
        skaliert: alc 3..35 bei gleichem gain-Bereich, Verstaerkung ~3.
        """
        if not self._tx_alc_samples and not self._tx_pwr_samples:
            return
        op = self.config.operating

        # ---- 1. Safety-Watchdog (ALC) -----------------------------------
        alc_peak = max(self._tx_alc_samples) if self._tx_alc_samples else 0
        pwr_peak = max(self._tx_pwr_samples) if self._tx_pwr_samples else 0.0
        self._last_alc_pct = alc_peak
        if alc_peak > op.alc_safety_threshold:
            new_gain = max(0.05, self._audio_gain * op.alc_safety_factor)
            if new_gain < self._audio_gain:
                log.warning(
                    "ALC-Watchdog: peak %d%% > %d%% — gain %.2f → %.2f, integrator reset",
                    alc_peak, op.alc_safety_threshold, self._audio_gain, new_gain,
                )
                self._audio_gain = new_gain
                self._pwr_integrator = 0.0
                self._maybe_persist_runtime_state()
            self._tx_alc_samples = []
            self._tx_pwr_samples = []
            return

        # ---- 2. Sensor-Sync-Gate ----------------------------------------
        if alc_peak == 0 and pwr_peak == 0.0:
            log.debug("PI: Sensor-Sync-Glitch (alc=0, pwr=0) — kein Update")
            self._tx_alc_samples = []
            self._tx_pwr_samples = []
            return

        # ---- 3. Regime-Wahl ---------------------------------------------
        pwr_norm = self._last_rig.rfpower_norm if self._last_rig else None
        if pwr_norm is None or pwr_norm < 0.05:
            self._tx_alc_samples = []
            self._tx_pwr_samples = []
            return

        underdrive_threshold = op.pwr_target_ratio * pwr_norm
        target: float
        pv: float
        regime: str
        if alc_peak > 0:
            # ALC-Regime: monoton, eindeutig — die wuenschenswerte PV.
            target = op.alc_target_pct / 100.0
            pv = alc_peak / 100.0
            regime = "ALC"
            deadband = op.alc_deadband_pct / 100.0
        elif pwr_peak < underdrive_threshold:
            # PWR-Underdrive-Regime: Cold-Start oder echter Underdrive.
            # Hier ist ALC=0 weil Audio so leise dass Limiter nicht beisst,
            # UND Power liegt deutlich unter Setting — wir muessen hoch.
            target = underdrive_threshold
            pv = pwr_peak
            regime = "PWR"
            deadband = 0.005
        else:
            # Sweet-Spot mit Limiter inaktiv (alc=0 ist hier "perfekt"):
            # pwr_meter zeigt voll-rated Power, ALC zeigt keinen Limiter-
            # Eingriff. Loop bleibt stehen.
            log.debug(
                "PI: Sweet-Spot (alc=0, pwr=%.2f >= %.2f) — kein Update",
                pwr_peak, underdrive_threshold,
            )
            self._tx_alc_samples = []
            self._tx_pwr_samples = []
            return

        error = target - pv

        # ---- 4. Deadband-Skip + PI-Update -------------------------------
        # Deadband ist absolutes Leave-Alone: kein P, kein I, kein gain-
        # Update. Sebastian sah 2026-05-22 abends dass der reine I-Deadband
        # nicht reichte — der P-Anteil triggerte bei e=+0.03 (in Deadband)
        # noch Δu=+0.006 Updates, die unter |Δu|≥0.005 fielen → langsamer
        # Drift Richtung Setpoint trotz "Deadband". Mit komplettem Skip
        # bleibt der Loop STILL im Soll-Bereich.
        if abs(error) < deadband:
            self._tx_alc_samples = []
            self._tx_pwr_samples = []
            return

        # Erweiterte Sweet-Spot-Erkennung im ALC-Regime: ein Up-Step
        # (error > 0 = alc zu niedrig) ist nur dann sinnvoll wenn der
        # Rig tatsaechlich Underdrive zeigt. Wenn pwr_meter schon nahe
        # am Setting liegt, ist niedriger ALC einfach "Limiter inaktiv"
        # = der gewuenschte FT8-Arbeitspunkt. Sebastian sah 2026-05-22
        # spaetabends das Loop-Pendeln zwischen gain 0.27..0.29: alc
        # sprang gelegentlich auf 3-5 % (Limiter ueberhaupt nicht aktiv)
        # bei gain=0.27, pwr=0.43 → unnoetiger Up-Step der dann beim
        # naechsten Burst mit alc=22 wieder zurueckgeholt wurde.
        # Down-Steps bleiben immer aktiv — Splatter-Schutz ist
        # unabhaengig vom Power-Status.
        if regime == "ALC" and error > 0 and pwr_peak >= underdrive_threshold:
            log.debug(
                "PI [ALC]: alc %d %% < target aber pwr %.2f >= %.2f → Sweet-Spot, kein Up-Step",
                alc_peak, pwr_peak, underdrive_threshold,
            )
            self._tx_alc_samples = []
            self._tx_pwr_samples = []
            return

        # Anti-Windup: Integrator nicht weiter aufbauen wenn die Stell-
        # groesse am Saturation-Limit klemmt und der Fehler in die gleiche
        # Richtung zeigt.
        at_upper = self._audio_gain >= 0.999 and error > 0
        at_lower = self._audio_gain <= 0.051 and error < 0
        if not (at_upper or at_lower):
            self._pwr_integrator += error
            self._pwr_integrator = max(-0.5, min(0.5, self._pwr_integrator))

        delta_u = op.gain_loop_kp * error + op.gain_loop_ki * self._pwr_integrator
        # PWR-Regime Rate-Limiter: maximaler Δu pro Burst beschneiden.
        # Sebastian sah 2026-05-22 wie der Loop bei einem QSO mit Audio-
        # Frequenz 262 Hz (unter dem IC-7300-Bandpass) dachte "Underdrive!"
        # und in 3 Bursts gain von 0.27 auf 0.44 hochkurbelte → ALC-Spike
        # auf 54 % im 4. Burst, Watchdog-Cut. Mit Rate-Limit bekommt der
        # Watchdog 5+ Slots Zeit zu greifen statt 3.
        if regime == "PWR":
            limit = op.pwr_regime_max_delta
            if delta_u > limit:
                delta_u = limit
            elif delta_u < -limit:
                delta_u = -limit
        new_gain = max(0.05, min(1.0, self._audio_gain + delta_u))

        # Output-Deadband 0.5 % damit Quantisierungs-Mikro-Drift ausserhalb
        # des Soll-Fensters trotzdem geschluckt wird (selten relevant, aber
        # symmetrisch sicher).
        if abs(new_gain - self._audio_gain) >= 0.005:
            log.info(
                "PI [%s]: pv %.3f, sp %.3f, e %+.3f, I %+.3f, gain %.3f → %.3f",
                regime, pv, target, error, self._pwr_integrator,
                self._audio_gain, new_gain,
            )
            self._audio_gain = new_gain
            self._maybe_persist_runtime_state()

        self._tx_alc_samples = []
        self._tx_pwr_samples = []

    def _check_audio_clipping(self) -> None:
        """RX-Audio-Pegel-Watchdog — ntfy wenn dauerhaft im roten Bereich.

        Schwellwert -3 dBFS = 3 dB Headroom bis Vollausschlag. Wenn der
        Wert ≥30 s am Stück darüber liegt, läuft das Risiko von Clipping
        in den FT8-Bursts (= unbrauchbare Symbole für den Decoder).

        Throttle: max alle 30 min eine Push, sonst spamt's wenn der
        Operator den Wert sieht aber gerade nicht ans Rig kann.
        """
        slot_buf = getattr(self.decode_source, "slot_buffer", None)
        if slot_buf is None:
            return
        try:
            dbfs = slot_buf.rms_dbfs_recent()
        except Exception:
            return
        if dbfs is None:
            return

        CLIP_THRESHOLD_DBFS = -3.0
        SUSTAIN_SECONDS = 30.0
        THROTTLE_SECONDS = 30 * 60.0

        now = time.monotonic()
        if dbfs >= CLIP_THRESHOLD_DBFS:
            if self._audio_clip_since is None:
                self._audio_clip_since = now
                return
            sustained = now - self._audio_clip_since
            if sustained < SUSTAIN_SECONDS:
                return
            # Throttle: nur alle 30 min erneut pingen.
            if now - self._last_audio_clip_ntfy_at < THROTTLE_SECONDS:
                return
            self._last_audio_clip_ntfy_at = now
            asyncio.create_task(
                self._notify_audio_clipping(dbfs, sustained),
                name="audio-clip-push",
            )
        else:
            # Pegel ist wieder unter Schwelle — Watchdog zurücksetzen.
            self._audio_clip_since = None

    # ------------------------------------------------------------------ SWR / ALC warnings
    def _check_swr_warn(self) -> None:
        """SWR-Live-Monitor während laufender TX (PTT=True).

        Drei Stufen:
        - ``swr >= swr_max``: SWR-Runaway → **sofortiger PTT-Cut**,
          TX_LOCKED + ntfy high-priority. Pre-TX-Guard kann den
          Overshoot nicht erkennen weil ``rig.swr`` zwischen Bursts
          auf den RX-Default 1.0 zurueckfaellt; nur waehrend PTT-On
          sehen wir den echten Wert. Sebastian 2026-05-24 Audit:
          9 TX-Slots mit SWR 2.9 durchgelaufen ohne jeden Schutz.
        - ``swr_warn <= swr < swr_max`` fuer >=3s: Vorwarn-ntfy
          (Throttle 10 min).
        - ``swr < swr_warn``: Watchdogs zurueckgesetzt.
        """
        if self._last_rig is None:
            return
        swr = self._last_rig.swr
        if swr is None:
            return
        op = self.config.operating
        warn = op.swr_warn
        hard = op.swr_max
        now = time.monotonic()

        # SWR-Settling-Period: erste 1.5 s nach PTT-On ignorieren wir
        # SWR-Readings, weil das IC-7300-Meter den Peak-Hold-Wert vom
        # letzten Burst noch zeigt. Sebastian 2026-05-24: nach 20m-Test
        # (SWR 2.88) hat der erste 15m-CQ-Burst den Stale-Wert ge-
        # lesen und faelschlich Runaway-Cut ausgeloest.
        SWR_SETTLING_S = 1.5
        if self._ptt_on_at > 0 and now - self._ptt_on_at < SWR_SETTLING_S:
            return

        # Stufe 1: Hard-Runaway → sofort cut
        if swr >= hard:
            if self._swr_runaway_active:
                return  # bereits in diesem Burst gehandled, nicht spammen
            self._swr_runaway_active = True
            log.warning(
                "SWR-Runaway: %.2f >= hard %.2f — forcing PTT off + TX_LOCKED",
                swr, hard,
            )
            # State Machine in TX_LOCKED setzen mit Reason.
            # Layer-Verletzung ist akzeptabel — der Pre-TX-Guard kann
            # den Overshoot konstruktionsbedingt nicht sehen, daher
            # muss der Orchestrator hier eingreifen.
            from ..statemachine.states import State as _State
            self.state_machine.state = _State.TX_LOCKED
            self.state_machine.ctx.last_lock_reason = (
                f"swr_guard: SWR {swr:.2f} ueber Limit {hard:.2f} "
                "— Antenne pruefen (Live-Cut waehrend TX)"
            )
            # PTT direkt abdrehen — nicht auf naechsten Slot warten
            asyncio.create_task(self._handle_swr_runaway(swr, hard),
                                name="swr-runaway-cut")
            return

        # Reset Runaway-Flag wenn wir wieder unter hard sind
        if self._swr_runaway_active and swr < hard:
            self._swr_runaway_active = False

        # Stufe 2: Vorwarn-Range
        if warn <= swr < hard:
            if self._swr_warn_since is None:
                self._swr_warn_since = now
                return
            if now - self._swr_warn_since < 3.0:
                return
            if now - self._last_swr_warn_ntfy_at < 600.0:
                return
            self._last_swr_warn_ntfy_at = now
            asyncio.create_task(self._notify_swr_warn(swr, warn, hard),
                                name="swr-warn-push")
        else:
            self._swr_warn_since = None

    async def _handle_swr_runaway(self, swr: float, hard: float) -> None:
        """Sofortiger PTT-Cut + ntfy bei SWR-Runaway."""
        try:
            await self.rig.set_ptt(False)
        except Exception as exc:
            log.warning("SWR-Runaway: set_ptt(False) failed: %s", exc)
        ntfy = self.integrations.ntfy
        if ntfy is None or not ntfy.enabled:
            return
        host = self.config.operating.public_hostname or "ft8"
        actions = [
            (
                f"http, 🔓 Sperre loesen, "
                f"http://{host}:8000/api/control/reset-lock, method=POST"
            ),
        ]
        await ntfy.notify(
            f"SWR auf {swr:.2f} gestiegen (Limit {hard:.2f}) — TX wurde "
            f"sofort abgebrochen. Antenne pruefen!",
            title="🚨 SWR-Notabschaltung",
            priority="high",
            tags=["rotating_light"],
            actions=actions,
        )

    async def _notify_swr_warn(self, swr: float, warn: float, hard: float) -> None:
        ntfy = self.integrations.ntfy
        if not (ntfy and ntfy.enabled):
            return
        msg = (
            f"SWR steigt auf {swr:.2f} (Warn-Schwelle {warn:.2f}, "
            f"Lock bei {hard:.2f}). Antenne checken — Stehwelle könnte "
            f"sich verschlechtert haben. Bei Erreichen von {hard:.2f} "
            f"sperrt der Pi TX automatisch."
        )
        try:
            await ntfy.notify(
                msg,
                title="⚠ SWR-Vorwarnung",
                priority="default",
                tags=["warning"],
            )
        except Exception as exc:
            log.warning("swr-warn ntfy push failed: %s", exc)

    def _check_alc_warn(self) -> None:
        """Vorwarn-Stufe für ALC. Analoge Logik zu _check_swr_warn."""
        if self._last_alc_pct is None:
            return
        alc = self._last_alc_pct
        op = self.config.operating
        warn = op.alc_warn
        # alc_max=0 ist der Default (ALC-Closed-Loop trimmt selbst).
        # Wenn alc_max=0 → Hard-Lock wäre quasi immer scharf. Wir
        # behandeln 0 als "Hard-Lock aus" → nur Warn aktiv.
        hard = op.alc_max if op.alc_max > 0 else 100
        now = time.monotonic()
        if warn <= alc < hard:
            if self._alc_warn_since is None:
                self._alc_warn_since = now
                return
            if now - self._alc_warn_since < 3.0:
                return
            if now - self._last_alc_warn_ntfy_at < 600.0:
                return
            self._last_alc_warn_ntfy_at = now
            asyncio.create_task(self._notify_alc_warn(alc, warn), name="alc-warn-push")
        else:
            self._alc_warn_since = None

    async def _notify_alc_warn(self, alc: int, warn: int) -> None:
        ntfy = self.integrations.ntfy
        if not (ntfy and ntfy.enabled):
            return
        msg = (
            f"ALC bei {alc}% (Warn-Schwelle {warn}%). Audio-Pegel zu "
            f"hoch — der Closed-Loop sollte das selbst runter trimmen. "
            f"Falls's nicht zurückgeht, manuell im Konfig audio_gain "
            f"reduzieren oder TX-Leistung am Rig anpassen."
        )
        try:
            await ntfy.notify(
                msg,
                title="⚠ ALC-Vorwarnung",
                priority="default",
                tags=["warning"],
            )
        except Exception as exc:
            log.warning("alc-warn ntfy push failed: %s", exc)

    async def _notify_audio_clipping(self, dbfs: float, sustained_s: float) -> None:
        """ntfy-Push beim RX-Pegel-Clipping-Watchdog."""
        ntfy = self.integrations.ntfy
        if not (ntfy and ntfy.enabled):
            return
        msg = (
            f"RX-Pegel bei {dbfs:.1f} dBFS seit {int(sustained_s)}s — "
            f"nahe am Clipping. Im IC-7300 MENU → SET → Connectors → "
            f"AF/SQL Control → USB AF Output Level reduzieren "
            f"(Default ~13, probier 8-10)."
        )
        try:
            await ntfy.notify(
                msg,
                title=f"🔊 FT8 {self.config.operating.public_hostname or 'Pi'} — RX-Pegel zu hoch",
                priority="default",  # nicht urgent — Decoder funktioniert noch
                tags=["loud_sound"],
            )
        except Exception as exc:
            log.warning("audio-clipping ntfy push failed: %s", exc)

    # ------------------------------------------------------------------ runtime state
    def _load_runtime_state(self) -> None:
        """Read persisted runtime-state (audio_gain, tx_power_w) at start().

        Silent no-op if file missing or unreadable — we just keep
        the config-defaults already loaded.
        """
        try:
            raw = self._runtime_state_path.read_text(encoding="utf-8")
            data = json.loads(raw)
            persisted = float(data.get("audio_gain", -1.0))
            if 0.05 <= persisted <= 1.0:
                log.info("runtime_state: loaded audio_gain %.2f (config-default was %.2f)",
                         persisted, self._audio_gain)
                self._audio_gain = persisted
                self._last_persisted_gain = persisted
                self._last_persisted_gain_at = time.monotonic()
            # tx_power_w: Sebastian 2026-05-24 — jetzt im runtime_state
            # statt in operator.default_power_w (siehe handle_tx_power).
            persisted_pwr = data.get("tx_power_w")
            if persisted_pwr is not None:
                try:
                    pw = int(persisted_pwr)
                    max_w = self.config.rig.effective_max_power_w
                    if 1 <= pw <= max_w:
                        log.info("runtime_state: loaded tx_power_w %dW "
                                 "(config-default was %dW)",
                                 pw, self._tx_power_w)
                        self._tx_power_w = pw
                except (TypeError, ValueError):
                    pass
        except FileNotFoundError:
            pass
        except Exception as exc:
            log.warning("runtime_state load failed: %s", exc)

    def _maybe_persist_runtime_state(self, force: bool = False) -> None:
        """Write runtime-state (audio_gain, tx_power_w) to disk.

        Throttle-Regel: nur schreiben wenn |gain - last_persisted| ≥ 0.02
        ODER mindestens 60 s seit letztem Write — vermeidet Flash-Wear
        ohne den persistierten Wert zu sehr hinter dem live-Wert
        zurückbleiben zu lassen. ``force=True`` umgeht das Throttle
        (z.B. wenn der User explizit den TX-Power-Slider bewegt).
        """
        now = time.monotonic()
        if not force:
            delta = abs(self._audio_gain - self._last_persisted_gain)
            if delta < 0.02 and (now - self._last_persisted_gain_at) < 60.0:
                return
        try:
            self._runtime_state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._runtime_state_path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps({
                    "audio_gain": round(self._audio_gain, 3),
                    "tx_power_w": int(self._tx_power_w),
                }),
                encoding="utf-8",
            )
            tmp.replace(self._runtime_state_path)
            self._last_persisted_gain = self._audio_gain
            self._last_persisted_gain_at = now
        except Exception as exc:
            log.warning("runtime_state persist failed: %s", exc)

    _chrony: ChronyStatus | None = field(default=None, init=False)

    def _detect_pile_ups(
        self, decodes: list, rarity_scores: dict[str, int]
    ) -> set[str]:
        """v0.19.0 — Pile-Up-Detection aus aktuellen Slot-Decodes.

        Zwei Indikatoren machen einen Pile-Up wahrscheinlich:

        1. **Rare-DXCC-Threshold**: rarity_score >= 70 → automatisch
           als Pile-Up-Verdacht markiert. Sebastian-Setup (Klasse E,
           50 W, fixed Antenne) hat bei Top-rare-DX <5% Erfolgsrate.

        2. **Decode-Density**: 4+ unique call_from auf ±50 Hz Audio-Freq
           des Decoded-CQ-Calls → klassisches Pile-Up-Muster mit vielen
           Callern auf der DX-Frequenz.

        Output: set normalisierter Calls die "in Pile-Up" sind.
        """
        pile_ups: set[str] = set()
        # Pass 1: rare-DX-Auto-Flag
        for d in decodes:
            if not d.call_from or d.call_to is not None:
                continue
            if not (d.message or "").startswith("CQ"):
                continue
            norm = d.call_from.upper()
            if rarity_scores.get(norm, 0) >= 70:
                pile_ups.add(norm)
        # Pass 2: Density-Detection auf der DX-Audio-Freq
        # Build freq → set(call_from) map
        from collections import defaultdict
        freq_callers: dict[int, set[str]] = defaultdict(set)
        for d in decodes:
            if not d.call_from or d.freq_offset_hz is None:
                continue
            bin_hz = int(d.freq_offset_hz // 50) * 50  # 50Hz Bin
            freq_callers[bin_hz].add(d.call_from.upper())
        # Fuer jeden CQ-Decode: schau ob auf seiner Freq ±50Hz viele
        # andere Calls aktiv sind.
        for d in decodes:
            if not d.call_from or d.call_to is not None:
                continue
            if not (d.message or "").startswith("CQ"):
                continue
            if d.freq_offset_hz is None:
                continue
            target_bin = int(d.freq_offset_hz // 50) * 50
            unique_callers: set[str] = set()
            for nb in (target_bin - 50, target_bin, target_bin + 50):
                unique_callers.update(freq_callers.get(nb, set()))
            # Subtrahiere den DX selbst — wir wollen ANDERE Caller zaehlen
            unique_callers.discard(d.call_from.upper())
            if len(unique_callers) >= 4:
                pile_ups.add(d.call_from.upper())
        return pile_ups

    def _compute_slot_parity(self, tick) -> str:
        """v0.15.0 — Slot-Parity ('even' oder 'odd') aus SlotTick ableiten.

        Robust gegen die +/-0.0005s-Boundary-Jitter der SlotClock (siehe
        state_machine.on_slot_tick fuer den round()-Workaround).
        """
        if tick is None:
            return "even"
        slot_seconds = getattr(tick, "slot_seconds", None) or 15.0
        try:
            n = round(tick.posix / slot_seconds)
        except Exception:
            return "even"
        return "even" if n % 2 == 0 else "odd"

    def _current_band(self) -> str | None:
        """Welches Band aus self.config.bands trifft auf rig.freq_hz zu?

        Sebastian 2026-05-23: QSO mit IS0YHV wurde als 20m geloggt obwohl
        wir auf 21074 (15m) standen. Ursache: DecodePipeline.band_hint
        wurde beim Boot auf config.bands[0]=20m fest verdrahtet und nicht
        beim Band-Switch nachgezogen. Wir leiten das Band jetzt zentral
        aus der Rig-Dial-Frequenz ab (±50 kHz Toleranz, FT8-Subband ist
        eng) — und nutzen das in _do_log_qso UND als band_hint-Override
        fuer die Decode-Pipeline.
        """
        if self._last_rig.freq_hz is None:
            return None
        freq_khz = self._last_rig.freq_hz / 1000.0
        for band in self.config.bands:
            if abs(band.freq_khz - freq_khz) <= 50:
                return band.name
        return None

    async def _refresh_hardware_state(self, tick: SlotTick) -> None:
        """Materialise the current :class:`HardwareState` for the guards."""
        from ..util.system_health import _read_cpu_temp  # local import: Pi-only path

        try:
            self._chrony = await read_chrony_tracking()
        except Exception:
            self._chrony = None

        gps = self.gps.snapshot
        rig = self._last_rig
        cpu_temp = _read_cpu_temp()
        antenna_ok = self._antenna_covers_current_band()

        # Decode-Pipeline-band_hint nachfuehren falls der User das Band
        # gewechselt hat. Wirkt sich auf neue Decodes/Heard/Qso-Rows aus.
        current_band = self._current_band()
        if current_band is not None:
            cur_hint = getattr(self.decode_source, "band_hint", None)
            if cur_hint != current_band:
                try:
                    self.decode_source.band_hint = current_band
                    log.info("band_hint sync: %s → %s (rig freq %s Hz)",
                             cur_hint, current_band, self._last_rig.freq_hz)
                except Exception:
                    # decode_source ist auf manchen Testpfaden ein
                    # Closure ohne settable Attribut — dann ignorieren
                    pass

        # Auto-QTH from GPS (architecture.md §6.4). Only when config doesn't
        # pin a locator. We re-compute on each slot — cheap.
        if (
            gps.lat is not None and gps.lon is not None
            and self.config.operator.default_locator is None
        ):
            try:
                grid = latlon_to_locator(gps.lat, gps.lon, precision=6)
                self.state_machine.ctx.my_grid = grid
            except Exception:
                pass

        # Mirror the worked-set into the state machine for the hunting-picker
        self.state_machine.ctx.worked = self._worked_calls
        # Filter-Flags aus der Config in den State-Machine-Context
        # spiegeln — der Hunting-Picker liest sie pro Slot.
        self.state_machine.ctx.skip_worked = self.config.operating.hunt_skip_worked
        self.state_machine.ctx.dxcc_only_mode = self.config.operating.hunt_dxcc_only
        self.state_machine.ctx.hunt_snr_floor_db = self.config.operating.hunt_snr_floor_db
        self.state_machine.ctx.hunt_audio_freq_min_hz = self.config.operating.hunt_audio_freq_min_hz
        self.state_machine.ctx.hunt_audio_freq_max_hz = self.config.operating.hunt_audio_freq_max_hz
        self.state_machine.ctx.cq_tx_slot_parity = self.config.operating.cq_tx_slot_parity
        # v0.10.0 Hunt-Priority-Tiers: Reihenfolge aus Config in den ctx
        # spiegeln. User kann via UI permutieren — hier wird's pro Slot
        # frisch eingelesen damit Änderungen ohne Restart greifen.
        self.state_machine.ctx.hunt_priority = list(self.config.operating.hunt_priority)
        # v0.11.0 Tail-End-Hunter: Toggle pro Slot spiegeln. Detection
        # + Synth-Injection im State-Machine-Pfad sind no-op wenn aus.
        self.state_machine.ctx.tail_end_hunter_enabled = (
            self.config.operating.tail_end_hunter_enabled
        )
        # New-DXCC-Set für den Hunting-Picker bauen: alle aktuell
        # dekodierten CQ-Calls deren Country wir noch nicht gearbeitet
        # haben. Plus call_to_dxcc-Mapping für 5BWAS-Tier (jeden CQ-Call
        # auf seine DXCC-Entität mappen) und rarity_scores für den
        # DXCC-Rarity-Tier.
        new_dxcc: set[str] = set()
        call_to_dxcc: dict[str, str] = {}
        call_to_latlon: dict[str, tuple[float, float]] = {}
        call_to_continent: dict[str, str] = {}  # v0.16.0
        rarity_scores: dict[str, int] = {}
        if self.integrations.cty is not None:
            # Lazy-Import damit Tests ohne dxcc_rarity-Daten nicht failen
            try:
                from ..integrations.dxcc_rarity import rarity_for
            except ImportError:
                rarity_for = lambda _c: 0  # type: ignore[assignment]
            for d in self._last_decodes:
                call = d.call_from
                if not call or d.call_to is not None:
                    continue
                if not (d.message or "").startswith("CQ"):
                    continue
                norm = call.upper()
                rec = self.integrations.cty.lookup(call)
                if rec is not None:
                    call_to_dxcc[norm] = rec.entity.name
                    if rec.entity.name not in self._worked_dxccs:
                        new_dxcc.add(call)
                    # v0.14.0 Grayline-Tier: Lat/Lon der DXCC-Entity
                    # cachen damit der Tier ohne weiteren Lookup auskommt.
                    if rec.entity.lat is not None and rec.entity.lon is not None:
                        call_to_latlon[norm] = (rec.entity.lat, rec.entity.lon)
                    # v0.16.0 Hour-of-Day-Tier: Continent cachen
                    if rec.entity.continent:
                        call_to_continent[norm] = rec.entity.continent
                # Rarity-Score (0..100) per Call — basiert auf cty-prefix
                # fallback, daher unabhängig vom cty-Lookup verfügbar.
                score = rarity_for(call)
                if score > 0:
                    rarity_scores[norm] = score
        self.state_machine.ctx.new_dxcc_calls = new_dxcc
        self.state_machine.ctx.call_to_dxcc = call_to_dxcc
        self.state_machine.ctx.call_to_latlon = call_to_latlon
        self.state_machine.ctx.call_to_continent = call_to_continent
        self.state_machine.ctx.rarity_scores = rarity_scores
        # v0.16.0 active_continent_hours aus DB-Aggregat
        self.state_machine.ctx.active_continent_hours = set(self._active_continent_hours)
        # v0.14.0 Band-Conditions aus hamqsl-Cache spiegeln. Daten werden
        # vom HamQslClient mit 30-min-TTL gecached, hier ist's also ein
        # billiger Memory-Read. Wenn Client gerade keine Daten hat → leere
        # Dicts → Tier liefert 0.
        self.state_machine.ctx.band_conditions_day = dict(self._band_conditions_day)
        self.state_machine.ctx.band_conditions_night = dict(self._band_conditions_night)
        # Watchlist-Mirror
        self.state_machine.ctx.watchlist_calls = set(self._watchlist_calls)
        # v0.15.0 Soft-Blacklist + Slot-Parity in ctx spiegeln
        self.state_machine.ctx.soft_blacklist = set(self._soft_blacklist)
        self.state_machine.ctx.op_slot_parity = dict(self._op_slot_parity)
        # v0.17.0 Buddy-Seen: (call, band) Set in ctx spiegeln
        self.state_machine.ctx.worked_call_band = set(self._worked_call_band)
        # v0.18.0 Freq-Reputation in ctx spiegeln fuer Smart-CQ-Picker
        self.state_machine.ctx.freq_reputation = dict(self._freq_reputation)
        # v0.19.0 Pile-Up-Detection: pro Slot aus aktuellen Decodes.
        self.state_machine.ctx.pile_up_calls = self._detect_pile_ups(
            self._last_decodes, rarity_scores,
        )
        # PSK-Reciprocity: aktueller Set "wer hat uns recently gehört".
        # Wird vom _psk_reciprocity_refresh-Loop periodisch upgedated;
        # hier nur in den ctx kopieren damit der Picker O(1) lookup hat.
        self.state_machine.ctx.psk_heard_us = set(self._psk_heard_us_cache)
        # marine_calls + worked_dxcc_band werden beim Boot/Operator-Switch
        # gesetzt — siehe _refresh_worked_sets. Hier nicht jeden Slot
        # neu laden (statische Daten).

        # chrony reports an Offset only when it has reached at least one
        # upstream peer (stratum < 16). We accept any stratum-tracked
        # chrony as "synced" — the time_guard's offset check is the real
        # gate. None means chronyc tracking didn't return data at all,
        # which we treat as not-synced.
        chrony_synced = (
            self._chrony is not None
            and self._chrony.stratum is not None
            and self._chrony.stratum < 16
        )

        self._hardware_state = HardwareState(
            gps_fix_mode=gps.mode,
            time_offset_s=self._chrony.offset_s if self._chrony else 0.0,
            swr=rig.swr if rig.swr is not None else 1.0,
            alc_pct=0,
            battery_v=None,
            cpu_temp_c=cpu_temp if cpu_temp is not None else 50.0,
            audio_drift_samples=0,
            antenna_covers_band=antenna_ok,
            chrony_synced=chrony_synced,
        )

    def _resolve_current_band_name(self) -> str | None:
        """Live-Band-Name aus dem aktuellen Rig-Snapshot. Wird vom
        DecodePipeline-Band-Resolver aufgerufen damit Decodes (und
        downstream QSOs/ntfy) den korrekten Band-Tag bekommen.

        Sebastian-Bug v0.5.1: vorher war band_hint statisch auf
        config.bands[0].name -> "20m" obwohl Pi auf 15m FT4.
        """
        if self._last_rig.freq_hz is None:
            return None
        return _band_from_freq_hz(self._last_rig.freq_hz)

    def _band_for_rig_freq(self, hz: int):
        """Welcher konfigurierte BandConfig deckt diese On-Air-Frequenz ab?

        Nutzt den bandplan-Util (verlässt sich auf IARU-Bandgrenzen, nicht
        auf die exakte FT8-Frequenz) und matcht den Namen gegen unsere
        konfigurierten Bänder. Liefert None wenn das Band nicht in der
        Config steht (z.B. Dad ist nach 40m gewechselt, hatten wir aber
        nie konfiguriert).
        """
        name = _band_from_freq_hz(hz)
        if name is None:
            return None
        for b in self.config.bands:
            if b.name == name:
                return b
        return None

    def _antenna_covers_current_band(self) -> bool:
        """Resolve current rig freq to a band name, then check the
        active antenna profile. Returns True if mismatch can't be
        established (no rig freq known yet, no antenna chosen) — those
        are start-up states, not violations.
        """
        if not self._active_antenna:
            return True
        if self._last_rig.freq_hz is None:
            return True
        band = _band_from_freq_hz(self._last_rig.freq_hz)
        if band is None:
            return True
        antenna = next(
            (a for a in self.config.antennas if a.name == self._active_antenna),
            None,
        )
        if antenna is None:
            return True  # invalid name in state — don't lock TX over a config typo
        return band in antenna.bands

    # ------------------------------------------------------------------ action dispatch
    async def _drain_actions(self) -> None:
        for action in self.state_machine.drain_actions():
            self._action_log.append(LoggedAction(ts=datetime.now(UTC), action=action))
            handler = self._action_handlers.get(action.kind)
            if handler is None:
                log.warning("no handler for action %s", action.kind)
                continue
            try:
                await handler(action.payload)
            except Exception as exc:
                log.warning("action %s failed: %s", action.kind, exc)

    async def _do_tx_message(self, payload: dict) -> None:
        """Real TX path: synth FT8 audio, key PTT, push samples, drop PTT.

        Stamps the current audio_gain into the payload so logs and the
        action-replay reflect what amplitude was actually used. When no
        playback adapter is attached (tests, dev workstation without ALSA)
        we skip the audio + PTT sequence and just log.
        """
        text = payload.get("message", "")
        audio_freq_hz = float(payload.get("freq_offset_hz", 1500))
        amplitude = 0.9 * max(0.0, min(1.0, self._audio_gain))
        payload["audio_gain"] = self._audio_gain
        # v0.18.0 Freq-Reputation: bei CQ-Bursts den (band, bin) tracken.
        # Erfolge werden im LOG_QSO-Handler verbucht via _last_cq_band_bin.
        kind = payload.get("kind") or ""
        if kind == "cq":
            band_now = self._current_band() or self.state_machine.ctx.band
            bin_hz = int(audio_freq_hz // 100) * 100
            key = (band_now, bin_hz)
            self._last_cq_band_bin = key
            att, succ = self._freq_reputation.get(key, (0, 0))
            self._freq_reputation[key] = (att + 1, succ)
            try:
                asyncio.create_task(self._persist_freq_reputation_attempt(key))
            except Exception:
                pass
        # Mark that we're actually radiating right now. _observe_alc_during_tx
        # gates upward gain adjustments by recency of this timestamp — without
        # this gate, ALC=0% during inter-burst gaps gets misread as "too quiet"
        # and the loop runs away.
        self._last_tx_message_at = time.monotonic()
        log.info("TX_MESSAGE: %s @ %.0fHz gain=%.2f alc_last=%s",
                 text, audio_freq_hz, self._audio_gain, self._last_alc_pct)

        if self.playback is None:
            return  # noop in dev / tests

        # FT4 uses a different synth function and shorter symbols.
        from ..decode.ft8_native import synth_message, synth_message_ft4
        mode = getattr(self.config.operating, "mode", "FT8")
        try:
            if mode == "FT4":
                pcm = synth_message_ft4(text, audio_freq_hz, amplitude)
            else:
                pcm = synth_message(text, audio_freq_hz, amplitude)
        except Exception as exc:
            log.error("FT8 synth failed for %r: %s", text, exc)
            return

        loop = asyncio.get_running_loop()
        try:
            await self.rig.set_ptt(True)
            # Mark PTT-On-Zeitpunkt fuer Settling-Period im SWR-Live-Cut.
            self._ptt_on_at = time.monotonic()
        except Exception as exc:
            log.error("set_ptt(True) failed: %s — aborting TX", exc)
            return
        try:
            # ALSA write is blocking; push to default executor so the slot
            # loop, rig poll, and SSE streams stay responsive during the
            # ~12.6 s burst.
            await loop.run_in_executor(None, self.playback.play, pcm)
        except Exception as exc:
            log.error("playback failed mid-burst: %s", exc)
        finally:
            try:
                await self.rig.set_ptt(False)
            except Exception as exc:
                log.error("set_ptt(False) post-TX failed: %s", exc)

    async def _do_stop_tx(self, _: dict) -> None:
        try:
            await self.rig.set_ptt(False)
        except Exception as exc:
            log.warning("STOP_TX failed: %s", exc)

    async def _do_log_qso(self, payload: dict) -> None:
        # Band aus der aktuellen Rig-Frequenz herleiten — Sicherheitsnetz
        # falls der DecodePipeline-band_hint nicht synchron war (Bug
        # 2026-05-23: erstes DO3XR-QSO mit IS0YHV wurde als 20m statt 15m
        # geloggt obwohl wir auf 21074 standen).
        current_band = self._current_band()
        if current_band is not None and payload.get("band") != current_band:
            log.info(
                "LOG_QSO band override: %s → %s (from rig freq)",
                payload.get("band"), current_band,
            )
            payload = {**payload, "band": current_band}
        log.info("LOG_QSO: %s", payload)
        call = (payload.get("call") or "").upper()
        is_new_dxcc = False
        if call:
            # Look up country/continent if cty.dat is loaded — used both
            # for the DXCC-new detection and for the ntfy push payload.
            if self.integrations.cty:
                rec = self.integrations.cty.lookup(call)
                if rec is not None:
                    country = rec.entity.name
                    if country not in self._worked_dxccs:
                        is_new_dxcc = True
                        self._worked_dxccs.add(country)
                    # v0.10.0: 5BWAS-Tracking. Auch wenn das DXCC schon
                    # gearbeitet ist, kann's auf diesem Band noch neu sein.
                    band_for_5bwas = payload.get("band")
                    if band_for_5bwas:
                        self._worked_dxcc_band.add((country, band_for_5bwas))
            self._worked_calls.add(call)
            # v0.17.0 Buddy-Seen: (call, band) inkremental pflegen
            band_for_buddy = payload.get("band")
            if band_for_buddy:
                self._worked_call_band.add((call, band_for_buddy))
            # v0.18.0 Freq-Reputation: wenn dieser QSO Resultat unseres
            # letzten CQ-Bursts ist (= jemand hat geantwortet, kein
            # Hunting-Pick), Success in den Bin verbuchen.
            if self._last_cq_band_bin is not None:
                key = self._last_cq_band_bin
                att, succ = self._freq_reputation.get(key, (0, 0))
                self._freq_reputation[key] = (att, succ + 1)
                try:
                    asyncio.create_task(self._persist_freq_reputation_success(key))
                except Exception:
                    pass
                # Verbraucht — nicht doppelt zaehlen
                self._last_cq_band_bin = None
            # v0.15.0 Reputation: erfolgreiches QSO vergibt Bail-Score.
            # Fire-and-forget — DB-Schreiben darf kein QSO blockieren.
            try:
                asyncio.create_task(self._record_qso_success(call))
            except Exception:
                pass
            # Cooldown registrieren — Hunting-Picker überspringt diesen
            # Call solange das Fenster läuft. 0 = aus.
            #
            # Adaptive Skalierung:
            #   * Neues DXCC → 1/4 Cooldown (war Award-Punkt, gleicher
            #     Op interessant für Confirm-Followup)
            #   * Neue Grid (kein neues DXCC) → 1/2 Cooldown
            #   * Sonst → voller Cooldown (Routine-QSO, lange Pause)
            cd_min = self.config.operating.qso_cooldown_min
            if cd_min > 0:
                import time as _time
                # v0.17.0 — Adaptive Cooldown basiert auf Rarity:
                #   * Rare DXCC (rarity_score >= 70: P5, 3Y, etc.) → 4× cd_min
                #     (= ~2h Default). Nach erstem QSO ist Award-Punkt
                #     im Sack, wir wollen nicht 30 min spaeter nochmal
                #     den selben rare Op picken — andere brauchen auch
                #     eine Chance, und wir picken andere wichtigere DX.
                #   * Routine-Op (rarity_score < 20, kein new DXCC, kein
                #     new Grid) → 1/3 cd_min (=~10 min). Confirms via
                #     Mehrfachkontakt sind erlaubt, andere Ops auf
                #     anderem Band sollen schnell wieder pickbar sein.
                #   * Sonst → cd_min Default.
                try:
                    from ..integrations.dxcc_rarity import rarity_for
                    rarity = rarity_for(call)
                except Exception:
                    rarity = 0
                grid_full = payload.get("grid_rcvd")
                g4 = grid_full[:4].upper() if grid_full else None
                band = payload.get("band")
                is_new_grid = (
                    g4 is not None
                    and band is not None
                    and (g4, band) not in self._worked_grid_band
                )
                if rarity >= 70:
                    effective_min = cd_min * 4
                    cd_kind = "rare-DXCC"
                elif (rarity < 20
                        and not is_new_dxcc
                        and not is_new_grid):
                    effective_min = max(1, cd_min // 3)
                    cd_kind = "routine"
                else:
                    effective_min = cd_min
                    cd_kind = "default"
                self.state_machine.ctx.recent_until[call] = (
                    _time.time() + effective_min * 60
                )
                log.debug(
                    "QSO-Cooldown %s: %s = %d min (rarity=%d, new_dxcc=%s, new_grid=%s)",
                    call, cd_kind, effective_min, rarity, is_new_dxcc, is_new_grid,
                )
        # Also update the grid + grid-band sets so future decodes from
        # the same grid stop pulsing "new grid" in the UI.
        grid_full = payload.get("grid_rcvd")
        band = payload.get("band")
        if grid_full:
            g4 = grid_full[:4].upper()
            if len(g4) == 4:
                self._worked_grids.add(g4)
                if band:
                    self._worked_grid_band.add((g4, band))
        # Push to ntfy.sh (fire-and-forget, must not block)
        if self.integrations.ntfy and self.integrations.ntfy.enabled:
            from ..integrations.flags import flag_for_call
            from ..integrations.mf_lookup import get_mf_lookup
            band = payload.get("band", "?")
            grid = payload.get("grid_rcvd", "?")
            qso_flag = flag_for_call(call, self.integrations.cty)
            # Marinefunker-Lookup (Sebastian v0.9.0): wenn Partner aktives
            # MF-Mitglied → Badge ⚓ + MFNr in den Push einbauen
            mf_member = get_mf_lookup().lookup(call) if call else None
            mf_suffix = f" ⚓ Marinefunker MF #{mf_member.mfnr}" if mf_member else ""
            title = ("🆕 New DXCC! " if is_new_dxcc else "📡 QSO complete: ") + (call or "?") + mf_suffix
            # Action-Buttons damit Dad nach jedem QSO direkt vom
            # Lockscreen aus den Modus wechseln kann ohne in die
            # Web-UI rein zu müssen. Tap = HTTP POST gegen Pi-Tailnet.
            host = self.config.operating.public_hostname or "ft8"
            actions = [
                (
                    f"http, ⏹ Stoppen, http://{host}:8000/api/control/stop, "
                    "method=POST"
                ),
                (
                    f"http, 🎯 Hunting, http://{host}:8000/api/control/auto-answer, "
                    "method=POST, headers.content-type=application/json, "
                    'body={"enabled":true}'
                ),
                (
                    f"http, 📢 CQ, http://{host}:8000/api/control/cq, "
                    "method=POST"
                ),
            ]
            # Sebastian v0.4.6: Mode mit in den Push damit auf einen
            # Blick erkennbar ist ob FT8 oder FT4. Format: "DK9XR 15m
            # FT4 IO91" — Mode zwischen Band und Grid einsortiert.
            qso_mode = self.config.operating.mode
            asyncio.create_task(self.integrations.ntfy.notify(
                f"{call} {band} {qso_mode} {grid}",
                title=title,
                priority="high" if is_new_dxcc else "default",
                tags=["radio", "new"] if is_new_dxcc else ["radio"],
                actions=actions,
                flag=qso_flag,
            ))
        if not self.db_enabled:
            return
        my_grid = self.config.operator.default_locator or self.state_machine.ctx.my_grid
        # On-air frequency = rig dial + audio offset of the QSO. The state
        # machine carries the audio offset in the payload; if missing
        # (legacy producer), fall back to dial-only.
        dial_hz = self._last_rig.freq_hz or 0
        audio_offset_hz = payload.get("freq_offset_hz", 0)
        freq_hz = dial_hz + audio_offset_hz
        gps = self.gps.snapshot
        try:
            async with session_scope() as s:
                await repository.insert_qso(
                    s,
                    call=payload["call"],
                    band=payload.get("band", "20m"),
                    freq_hz=freq_hz,
                    # Sebastian-Bug v0.4.2: state_machine-Payload hat
                    # kein mode-Feld -> default "FT8" hat alle QSOs
                    # falsch als FT8 geloggt auch wenn wir FT4 fuhren.
                    # Jetzt: live aus operating.mode lesen.
                    mode=payload.get("mode") or self.config.operating.mode,
                    rst_sent=payload.get("rst_sent"),
                    rst_rcvd=payload.get("rst_rcvd"),
                    grid_rcvd=payload.get("grid_rcvd"),
                    qso_start=payload["qso_start"],
                    qso_end=payload["qso_end"],
                    my_grid=my_grid,
                    my_power_w=self._tx_power_w,
                    swr_avg=self._last_rig.swr,
                    my_lat=gps.lat,
                    my_lon=gps.lon,
                    # Multi-Operator-Tracking: welcher Operator hat diesen QSO?
                    user_callsign=self.config.operator.callsign,
                    # Marinefunker-Snapshot (Sebastian v0.9.0): MFNr zum
                    # QSO-Zeitpunkt einfrieren — bleibt stabil ueber spaetere
                    # PDF-Updates. Null wenn Partner kein aktives Mitglied.
                    mf_mfnr=_mf_snapshot_mfnr(payload["call"]),
                )
        except Exception as exc:
            log.warning("LOG_QSO db write failed: %s", exc)

    # ------------------------------------------------------------------ v0.15.0 reputation
    # Bail-Reason → Score-Delta. picked_another zaehlt NICHT (das ist
    # Pech, nicht Verhalten der Station). ClassVar damit der dataclass
    # die nicht als Field interpretiert.
    _BAIL_SCORE_WEIGHTS: typing.ClassVar[dict[str, int]] = {
        "picked_another": 0,
        "max_resends": 2,
        "went_silent": 1,
        "report_never_closed": 1,
    }
    _SOFT_BLACKLIST_THRESHOLD: typing.ClassVar[int] = 5
    _MIN_ATTEMPTS_FOR_BLACKLIST: typing.ClassVar[int] = 3
    _SUCCESS_SCORE_DELTA: typing.ClassVar[int] = -5

    async def _do_qso_bail(self, payload: dict) -> None:
        """Reputation-Update bei jedem Bail aus dem State-Machine.

        Updated DB-Eintrag (attempts +1, score += weight, last_reason).
        Triggert ggf. Soft-Blacklist-Aufnahme.
        """
        call = (payload.get("call") or "").upper().strip()
        reason = payload.get("reason") or "unknown"
        if not call:
            return
        weight = self._BAIL_SCORE_WEIGHTS.get(reason, 0)
        my_user = self.config.operator.callsign
        try:
            async with session_scope() as s:
                row = await s.get(DbCallReputation, call)
                now = datetime.now(UTC)
                if row is None:
                    row = DbCallReputation(
                        call=call,
                        user_callsign=my_user,
                        score=weight,
                        attempts=1,
                        successes=0,
                        last_attempt_at=now,
                        last_reason=reason,
                    )
                    s.add(row)
                else:
                    row.score = (row.score or 0) + weight
                    row.attempts = (row.attempts or 0) + 1
                    row.last_attempt_at = now
                    row.last_reason = reason
                    if row.user_callsign is None:
                        row.user_callsign = my_user
                # In-Memory-Set updaten falls jetzt ueber Threshold
                if (row.score >= self._SOFT_BLACKLIST_THRESHOLD
                        and row.attempts >= self._MIN_ATTEMPTS_FOR_BLACKLIST):
                    self._soft_blacklist.add(call)
                    log.info(
                        "Soft-Blacklist: %s (score=%d, attempts=%d, reason=%s)",
                        call, row.score, row.attempts, reason,
                    )
        except Exception as exc:
            log.warning("reputation bail-update failed for %s: %s", call, exc)

    async def _aggregate_active_hours(self, s, my_call: str) -> set[tuple[str, int]]:
        """v0.16.0 — Aggregate QSO-DB into (continent, hour) Top-50% pro
        Continent.

        Geht die letzten 90 Tage durch, mapped call → continent ueber
        cty.dat, zählt QSOs pro (continent, hour-of-day-UTC). Nimmt
        pro Continent die Stunden mit count >= Median als "aktiv".
        """
        from sqlalchemy import select as _select
        cty = self.integrations.cty
        if cty is None:
            return set()
        try:
            rows = (await s.execute(
                _select(Qso.call, Qso.qso_start).where(
                    Qso.user_callsign == my_call,
                    Qso.qso_start.isnot(None),
                )
            )).all()
        except Exception as exc:
            log.warning("active-hours aggregation: db query failed: %s", exc)
            return set()
        # Count: continent → {hour: count}
        counts: dict[str, dict[int, int]] = {}
        for call, ts in rows:
            if not call or ts is None:
                continue
            try:
                rec = cty.lookup(call)
            except Exception:
                continue
            if rec is None or rec.entity is None:
                continue
            cont = rec.entity.continent
            if not cont:
                continue
            hour = ts.hour if hasattr(ts, "hour") else None
            if hour is None:
                continue
            counts.setdefault(cont, {}).setdefault(hour, 0)
            counts[cont][hour] += 1
        # Pro Continent: nimm Stunden mit count >= Median als aktiv.
        active: set[tuple[str, int]] = set()
        for cont, hour_counts in counts.items():
            if len(hour_counts) < 2:
                # Zu wenig Datenpunkte → alle Stunden aktiv (keine
                # falsche Negativ-Annahme).
                for h in hour_counts:
                    active.add((cont, h))
                continue
            sorted_counts = sorted(hour_counts.values())
            median = sorted_counts[len(sorted_counts) // 2]
            for hour, c in hour_counts.items():
                if c >= median:
                    active.add((cont, hour))
        if active:
            log.info(
                "active-hours: %d (continent, hour) Tuples aus %d QSOs",
                len(active), len(rows),
            )
        return active

    async def _persist_freq_reputation_attempt(self, key: tuple[str, int]) -> None:
        """v0.18.0 — Inkrementiere attempts-Counter im DB-Eintrag."""
        band, bin_hz = key
        try:
            async with session_scope() as s:
                row = await s.get(DbFreqReputation, (band, bin_hz))
                now = datetime.now(UTC)
                if row is None:
                    s.add(DbFreqReputation(
                        band=band, audio_bin_hz=bin_hz,
                        attempts=1, successes=0, last_used_at=now,
                    ))
                else:
                    row.attempts = (row.attempts or 0) + 1
                    row.last_used_at = now
        except Exception as exc:
            log.debug("freq-rep attempt persist failed: %s", exc)

    async def _persist_freq_reputation_success(self, key: tuple[str, int]) -> None:
        """v0.18.0 — Inkrementiere successes-Counter im DB-Eintrag."""
        band, bin_hz = key
        try:
            async with session_scope() as s:
                row = await s.get(DbFreqReputation, (band, bin_hz))
                now = datetime.now(UTC)
                if row is None:
                    s.add(DbFreqReputation(
                        band=band, audio_bin_hz=bin_hz,
                        attempts=0, successes=1, last_used_at=now,
                    ))
                else:
                    row.successes = (row.successes or 0) + 1
                    row.last_used_at = now
        except Exception as exc:
            log.debug("freq-rep success persist failed: %s", exc)

    async def handle_dxpedition_add(
        self, call: str, start_date: datetime, end_date: datetime,
        note: str | None = None,
    ) -> None:
        """v0.19.0 — DXpedition-Schedule-Eintrag hinzufuegen."""
        call = (call or "").upper().strip()
        if not call:
            return
        my_call = self.config.operator.callsign
        try:
            async with session_scope() as s:
                exists = await s.get(DbDxpeditionSchedule, call)
                if exists is None:
                    s.add(DbDxpeditionSchedule(
                        call=call, user_callsign=my_call,
                        start_date=start_date, end_date=end_date,
                        note=note, added=datetime.now(UTC),
                        auto_added_to_watchlist=False, reminder_sent=False,
                    ))
                else:
                    exists.start_date = start_date
                    exists.end_date = end_date
                    if note is not None:
                        exists.note = note
                    if exists.user_callsign is None:
                        exists.user_callsign = my_call
        except Exception as exc:
            log.warning("dxpedition add DB failed: %s", exc)

    async def handle_dxpedition_remove(self, call: str) -> None:
        call = (call or "").upper().strip()
        if not call:
            return
        try:
            async with session_scope() as s:
                row = await s.get(DbDxpeditionSchedule, call)
                if row is not None:
                    was_auto = row.auto_added_to_watchlist
                    await s.delete(row)
                    if was_auto:
                        # Watchlist-Eintrag auch entfernen
                        wl_row = await s.get(DbWatchlist, call)
                        if wl_row is not None:
                            await s.delete(wl_row)
                        self._watchlist_calls.discard(call)
        except Exception as exc:
            log.warning("dxpedition remove DB failed: %s", exc)

    async def _dxped_ng3k_import_loop(self) -> None:
        """v0.19.1 — Periodischer Auto-Import von ng3k.com/Misc/adxo.html.

        Alle 6h fetched + merged in dxpedition_schedule:
        - Neue Calls → einfuegen mit source='ng3k'
        - Existierende ng3k-Eintraege → Dates/Info aktualisieren
        - source='manual' Eintraege werden NIE ueberschrieben

        Boot-Grace 30s damit Service-Start nicht direkt mit Netz-Call
        blockiert wird.
        """
        from ..integrations.dxped_ng3k import fetch_ng3k
        await asyncio.sleep(30)
        while True:
            try:
                entries = await fetch_ng3k()
                added = 0
                updated = 0
                async with session_scope() as s:
                    for e in entries:
                        existing = await s.get(DbDxpeditionSchedule, e.call)
                        if existing is None:
                            s.add(DbDxpeditionSchedule(
                                call=e.call,
                                user_callsign=self.config.operator.callsign,
                                start_date=e.start,
                                end_date=e.end,
                                note=f"{e.dxcc_name}: {e.info}".strip(": "),
                                added=datetime.now(UTC),
                                auto_added_to_watchlist=False,
                                reminder_sent=False,
                                source="ng3k",
                            ))
                            added += 1
                        elif existing.source == "ng3k":
                            # Auto-Import: refresh dates + info, behalte
                            # auto_added_to_watchlist + reminder_sent.
                            existing.start_date = e.start
                            existing.end_date = e.end
                            new_note = f"{e.dxcc_name}: {e.info}".strip(": ")
                            if new_note and new_note != existing.note:
                                existing.note = new_note
                            updated += 1
                        # source='manual' → NICHT anfassen
                log.info(
                    "ng3k-import: %d entries fetched, %d added, %d updated",
                    len(entries), added, updated,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("ng3k-import hiccup: %s", exc)
            await asyncio.sleep(6 * 3600)

    async def _dxpedition_schedule_loop(self) -> None:
        """v0.19.0 — Background-Loop pflegt die Watchlist auf Basis
        des DXpedition-Schedule.

        Alle 30 min:
        - Innerhalb [start, end] UND nicht auto_added → in Watchlist + Mark
        - 24h vor start UND noch keine reminder_sent → ntfy-Push, Mark
        - Nach end + auto_added → aus Watchlist raus + Eintrag bleibt
          (User kann ihn manuell loeschen)
        """
        from datetime import timedelta
        await asyncio.sleep(10)  # boot grace
        while True:
            try:
                now = datetime.now(UTC)
                async with session_scope() as s:
                    rows = list(
                        (await s.execute(select(DbDxpeditionSchedule))).scalars()
                    )
                    for row in rows:
                        # 24h-Reminder
                        if (not row.reminder_sent
                                and row.start_date - timedelta(hours=24) <= now < row.start_date):
                            ntfy = self.integrations.ntfy
                            if ntfy and ntfy.enabled:
                                try:
                                    await ntfy.push(
                                        title=f"📡 DXpedition morgen QRV: {row.call}",
                                        message=(
                                            f"{row.note or ''} · "
                                            f"{row.start_date:%Y-%m-%d %H:%M}–"
                                            f"{row.end_date:%Y-%m-%d %H:%M} UTC"
                                        ),
                                        tags="satellite_antenna",
                                    )
                                except Exception:
                                    pass
                            row.reminder_sent = True
                        # Auto-add zur Watchlist wenn aktiv
                        active = row.start_date <= now <= row.end_date
                        if active and not row.auto_added_to_watchlist:
                            self._watchlist_calls.add(row.call.upper())
                            wl_row = await s.get(DbWatchlist, row.call)
                            if wl_row is None:
                                s.add(DbWatchlist(
                                    call=row.call,
                                    user_callsign=row.user_callsign,
                                    added=now,
                                    note=f"DXpedition (auto): {row.note or ''}".strip(),
                                ))
                            row.auto_added_to_watchlist = True
                            log.info(
                                "DXpedition QRV: %s in Watchlist (%s–%s)",
                                row.call, row.start_date, row.end_date,
                            )
                        # Auto-remove wenn vorbei
                        if not active and row.auto_added_to_watchlist and now > row.end_date:
                            self._watchlist_calls.discard(row.call.upper())
                            wl_row = await s.get(DbWatchlist, row.call)
                            if wl_row is not None:
                                await s.delete(wl_row)
                            row.auto_added_to_watchlist = False
                            log.info(
                                "DXpedition vorbei: %s aus Watchlist entfernt",
                                row.call,
                            )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("dxpedition-schedule-loop hiccup: %s", exc)
            await asyncio.sleep(1800)  # 30 min

    async def handle_reputation_reset(self, call: str) -> None:
        """v0.15.0 — User-triggered Soft-Blacklist-Removal.

        Entfernt den DB-Eintrag UND das In-Memory-Set. Nach Reset
        ist der Call wieder neutral fuer den Picker.
        """
        call = (call or "").upper().strip()
        if not call:
            return
        self._soft_blacklist.discard(call)
        try:
            async with session_scope() as s:
                row = await s.get(DbCallReputation, call)
                if row is not None:
                    await s.delete(row)
        except Exception as exc:
            log.warning("reputation reset DB-delete failed: %s", exc)

    async def _record_qso_success(self, call: str) -> None:
        """Erfolg vergibt — Score Richtung 0 ziehen, ggf. Soft-Blacklist
        verlassen."""
        call = (call or "").upper().strip()
        if not call:
            return
        my_user = self.config.operator.callsign
        try:
            async with session_scope() as s:
                row = await s.get(DbCallReputation, call)
                now = datetime.now(UTC)
                if row is None:
                    row = DbCallReputation(
                        call=call,
                        user_callsign=my_user,
                        score=self._SUCCESS_SCORE_DELTA,
                        attempts=0,
                        successes=1,
                        last_attempt_at=now,
                        last_reason="success",
                    )
                    s.add(row)
                else:
                    row.score = (row.score or 0) + self._SUCCESS_SCORE_DELTA
                    row.successes = (row.successes or 0) + 1
                    row.last_attempt_at = now
                    row.last_reason = "success"
                    if row.user_callsign is None:
                        row.user_callsign = my_user
                # Aus Soft-Blacklist raus wenn unter Threshold
                if row.score < self._SOFT_BLACKLIST_THRESHOLD:
                    self._soft_blacklist.discard(call)
        except Exception as exc:
            log.warning("reputation success-update failed for %s: %s", call, exc)

    async def _do_tx_locked(self, payload: dict) -> None:
        reason = payload.get("reason") or "unbekannt"
        log.warning("TX_LOCKED: %s", reason)
        # ntfy-Push wenn aktiviert. Wichtig bei SWR/PA-Schutz-Locks
        # weil Dad sonst evtl. nicht merkt dass die Kiste still steht.
        # priority=urgent damit das Handy klingelt auch bei stumm-Modus.
        ntfy = self.integrations.ntfy
        if ntfy and ntfy.enabled:
            host = self.config.operating.public_hostname or "ft8"
            actions = [
                f"http, Sperre lösen, http://{host}:8000/api/control/reset-lock, method=POST, clear=true",
            ]
            # Tags fürs Symbol — warning fürs Allgemeine, antenna-Symbol
            # wenn SWR der Auslöser war (häufigster Lock-Grund).
            tags = ["warning"]
            if "swr" in reason.lower():
                tags.append("antenna_bars")
            try:
                await ntfy.notify(
                    f"TX gesperrt: {reason}",
                    title=f"⚠️ FT8 {self.config.operating.public_hostname or 'Pi'} — TX-Lock",
                    priority="urgent",
                    tags=tags,
                    actions=actions,
                )
            except Exception as exc:
                log.warning("tx_locked ntfy push failed: %s", exc)

    # ------------------------------------------------------------------ fan-out helpers
    def _push_decode(self, decode: DecodedMsg) -> None:
        for q in list(self._decode_subscribers):
            try:
                q.put_nowait(decode)
            except asyncio.QueueFull:
                # Slow consumer — drop oldest to make room
                with contextlib.suppress(asyncio.QueueEmpty):
                    q.get_nowait()
                with contextlib.suppress(asyncio.QueueFull):
                    q.put_nowait(decode)

    def _push_status(self) -> None:
        snap = self.status()
        for q in list(self._state_subscribers):
            try:
                q.put_nowait(snap)
            except asyncio.QueueFull:
                with contextlib.suppress(asyncio.QueueEmpty):
                    q.get_nowait()
                with contextlib.suppress(asyncio.QueueFull):
                    q.put_nowait(snap)
