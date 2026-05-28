"""SQLAlchemy ORM models for the appliance's local SQLite database.

Schema follows ``architecture.md`` §7.2. We deliberately stay close to
the SQL there — no fancy ORM features, just plain tables — so the
schema can also be queried from ``sqlite3`` on the Pi during pi-check.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
class Qso(Base):
    __tablename__ = "qso"

    id: Mapped[int] = mapped_column(primary_key=True)
    call: Mapped[str] = mapped_column(String, index=True)
    band: Mapped[str] = mapped_column(String, index=True)
    freq_hz: Mapped[int] = mapped_column(Integer)
    mode: Mapped[str] = mapped_column(String, default="FT8")
    rst_sent: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rst_rcvd: Mapped[int | None] = mapped_column(Integer, nullable=True)
    grid_rcvd: Mapped[str | None] = mapped_column(String, nullable=True)
    qso_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    qso_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    my_grid: Mapped[str] = mapped_column(String)
    my_power_w: Mapped[int | None] = mapped_column(Integer, nullable=True)
    swr_avg: Mapped[float | None] = mapped_column(Float, nullable=True)
    notes: Mapped[str | None] = mapped_column(String, nullable=True)
    # Operator location at the moment of QSO — drives the "trip map"
    # (Bonus 12). Null when no GPS fix was available.
    my_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    my_lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Multi-Operator-Tracking (Sebastian 2026-05-23). Speichert welcher
    # Operator den QSO gemacht hat — bei mehreren Profilen am gleichen
    # Pi (DK9XR, DL2XYZ, …) bleibt das Log sauber getrennt. Nullable
    # fuer Backward-Compat mit alten QSOs aus der Pre-Multi-User-Aera;
    # die Migration weist denen den damaligen Single-Operator zu.
    user_callsign: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    # QRZ.com Logbook upload tracking. The appliance must work offline
    # (vacation, intermittent hotspot) — every QSO is persisted locally
    # immediately, then a background task drains the unuploaded ones
    # whenever connectivity returns. qrz_logbook_id is what QRZ returns
    # on successful insert; we keep it to support future delete/update.
    qrz_uploaded: Mapped[bool] = mapped_column(default=False, index=True)
    qrz_logbook_id: Mapped[str | None] = mapped_column(String, nullable=True)
    qrz_upload_attempts: Mapped[int] = mapped_column(Integer, default=0)
    qrz_last_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # v0.21.0 — ClubLog Logbook upload tracking. Analog QRZ: jedes QSO
    # wird lokal sofort persistiert, der Drain-Loop schiebt es im
    # Hintergrund nach ClubLog hoch. ClubLog liefert kein eigenes
    # logbook_id zurueck (nur OK/FAIL), daher reicht das bool + attempts.
    clublog_uploaded: Mapped[bool] = mapped_column(default=False, index=True)
    clublog_upload_attempts: Mapped[int] = mapped_column(Integer, default=0)
    clublog_last_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # v0.22.0 — TX-Callsign zum QSO-Zeitpunkt (mit DX-Prefix wenn
    # Auslandsbetrieb). user_callsign bleibt der Heimat-Call (Multi-Op-
    # Filter), station_callsign ist was wir tatsaechlich gesendet
    # haben. ADIF-Upload nutzt station_callsign damit QRZ + ClubLog
    # den DX-Prefix erkennen. Null = wie user_callsign (Heimat).
    station_callsign: Mapped[str | None] = mapped_column(String, nullable=True)
    # Marinefunker-Snapshot (Sebastian 2026-05-26 v0.9.0).
    # mf_mfnr ist die Mitgliedsnummer aus der MF-Dipl.Such-Abhakliste
    # ZUM ZEITPUNKT DES QSO eingefroren — bleibt korrekt auch wenn die
    # PDF/JSON spaeter aktualisiert wird (Mitglied tritt aus / stirbt).
    # Null = Partner war zum QSO-Zeitpunkt kein aktiver Marinefunker.
    mf_mfnr: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)


# ---------------------------------------------------------------------------
class Decode(Base):
    __tablename__ = "decode"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    call_from: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    call_to: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    grid: Mapped[str | None] = mapped_column(String, nullable=True)
    message: Mapped[str] = mapped_column(String)
    snr_db: Mapped[int | None] = mapped_column(Integer, nullable=True)
    dt_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    freq_offset_hz: Mapped[int | None] = mapped_column(Integer, nullable=True)
    band: Mapped[str | None] = mapped_column(String, index=True, nullable=True)


# ---------------------------------------------------------------------------
class Heard(Base):
    __tablename__ = "heard"

    # PK bleibt single-column (SQLite kann composite-PK nicht in-place
    # migrieren). Multi-Operator-Trennung via user_callsign-Column +
    # WHERE-Filter in der Repository-Schicht — Application garantiert
    # Eindeutigkeit per upsert-Logik.
    call: Mapped[str] = mapped_column(String, primary_key=True)
    user_callsign: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    count: Mapped[int] = mapped_column(Integer, default=1)
    grid: Mapped[str | None] = mapped_column(String, nullable=True)
    best_snr: Mapped[int | None] = mapped_column(Integer, nullable=True)


# ---------------------------------------------------------------------------
class PskReporterIn(Base):
    """'Wer hat mich gehört' inbound reports from pskreporter.info."""

    __tablename__ = "psk_reporter_in"

    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    rx_call: Mapped[str] = mapped_column(String, primary_key=True)
    rx_grid: Mapped[str | None] = mapped_column(String, nullable=True)
    snr_db: Mapped[int | None] = mapped_column(Integer, nullable=True)
    band: Mapped[str | None] = mapped_column(String, nullable=True)


# ---------------------------------------------------------------------------
class SwrLog(Base):
    __tablename__ = "swr_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    band: Mapped[str] = mapped_column(String, index=True)
    freq_hz: Mapped[int] = mapped_column(Integer)
    swr: Mapped[float] = mapped_column(Float)


# ---------------------------------------------------------------------------
class Blacklist(Base):
    __tablename__ = "blacklist"

    # PK bleibt single-column aus SQLite-Migrations-Gruenden — Operator-
    # Isolation via user_callsign-Filter in Repository.
    call: Mapped[str] = mapped_column(String, primary_key=True)
    user_callsign: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    added: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str | None] = mapped_column(String, nullable=True)


# ---------------------------------------------------------------------------
class FreqReputation(Base):
    """v0.18.0 — Erfolgsrate pro (Band, 100Hz-Audio-Bin) tracken.

    Beim CQ wechseln wir die Audio-Freq laut Smart-Picker. Ueber die
    Zeit lernen wir welche Bins erfolgreicher sind (mehr LOG_QSOs nach
    CQ in dem Bin). Picker biased dann zu den erfolgreichen Bins
    (Bayesian-Bandit-style, mit kleinem Exploration-Anteil).

    KEINE Operator-Isolation: die Antenne/Rig/Standort-Eigenschaften
    sind pro Pi, nicht pro Op. Wenn DK9XR und DO3XR sich denselben
    Pi teilen, profitieren beide vom selben Reputation-Set.
    """
    __tablename__ = "freq_reputation"

    band: Mapped[str] = mapped_column(String, primary_key=True)
    audio_bin_hz: Mapped[int] = mapped_column(Integer, primary_key=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    successes: Mapped[int] = mapped_column(Integer, default=0)
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


# ---------------------------------------------------------------------------
class CallReputation(Base):
    """v0.15.0 — Bail-Reason-aware Soft-Blacklist-Tracking.

    Pro Call ein Score-Bucket. Bail-Reasons werden mit Gewichten
    verbucht:

    * ``picked_another`` → +0  (Pech — er hat staerkeren Caller gepickt)
    * ``max_resends``    → +2  (er hoert uns systematisch nicht)
    * ``went_silent``    → +1  (ambivalent — QSB / abgehauen)
    * ``report_never_closed`` → +1 (ambivalent — Decode-Fehler)
    * Erfolgreiches QSO   → −5 (Vergebung, Reset Richtung 0)

    Score >= ``SOFT_BLACKLIST_THRESHOLD`` (Default 5) UND
    ``attempts >= MIN_ATTEMPTS`` (Default 3) → Soft-Blacklist.

    Multi-Operator-Isolation via user_callsign.
    """
    __tablename__ = "call_reputation"

    call: Mapped[str] = mapped_column(String, primary_key=True)
    user_callsign: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    score: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    successes: Mapped[int] = mapped_column(Integer, default=0)
    last_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_reason: Mapped[str | None] = mapped_column(String, nullable=True)


# ---------------------------------------------------------------------------
class Watchlist(Base):
    """v0.14.0 — Calls die wir aktiv beobachten. Bei jedem Decode eines
    Watchlist-Calls feuert der Orchestrator eine ntfy-Push mit Action-
    Buttons "Anrufen / Ignorieren". Throttle 1× pro Call pro 1h damit
    DXpedition-Calls die im selben Slot 30× decoden nicht in Push-Spam
    ausarten.

    Multi-Operator: pro user_callsign isoliert (DK9XR's Watchlist ist
    nicht DO3XR's).
    """
    __tablename__ = "watchlist"

    call: Mapped[str] = mapped_column(String, primary_key=True)
    user_callsign: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    added: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(String, nullable=True)
    last_alert_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # v0.19.2 — Herkunft: 'manual' (User-Eingabe) oder 'ng3k_auto'
    # (DXpedition-Schedule-Loop hat den Call automatisch eingetragen).
    # Push-Verhalten differenziert: manual = 1h-Throttle (User
    # wollte's), ng3k_auto = 24h-Throttle + rarity-gated.
    source: Mapped[str] = mapped_column(String, default="manual")


# ---------------------------------------------------------------------------
class DxpeditionSchedule(Base):
    """v0.19.0 — Geplante DXpeditions die wir nicht verpassen wollen.

    Manueller Eintrag pro Op (User kennt seine DXpeditions besser als
    irgendeine Scraper-API). Background-Loop pflegt automatisch die
    Watchlist:

    * 24h vor ``start_date`` → ntfy-Reminder "morgen QRV"
    * ``start_date`` erreicht → Call kommt in Watchlist (auto_added=True)
    * nach ``end_date`` → Call wieder aus Watchlist raus

    So vergisst der Op nie ne Aktivierungszeit; und die Watchlist bleibt
    schlank ohne abgelaufene DXpeditions.
    """
    __tablename__ = "dxpedition_schedule"

    call: Mapped[str] = mapped_column(String, primary_key=True)
    user_callsign: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    start_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_date: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(String, nullable=True)
    added: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    auto_added_to_watchlist: Mapped[bool] = mapped_column(
        Boolean, default=False
    )
    reminder_sent: Mapped[bool] = mapped_column(Boolean, default=False)
    # v0.19.1 — Herkunft: 'manual' (User-Eingabe) oder 'ng3k' (Auto-Import).
    # Manuelle Eintraege werden vom Auto-Import NICHT ueberschrieben.
    source: Mapped[str] = mapped_column(String, default="manual")


# ---------------------------------------------------------------------------
class ConfigHistory(Base):
    __tablename__ = "config_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    yaml_snapshot: Mapped[str] = mapped_column(String)
