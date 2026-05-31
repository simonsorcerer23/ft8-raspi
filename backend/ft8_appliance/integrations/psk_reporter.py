"""PSK Reporter client — Download ("who heard me") + Real-IPFIX-Upload.

* Download: HTTP GET on https://pskreporter.info/cgi-bin/pskquery5.pl
  mit XML-Response der letzten Reception-Reports.
* Upload: PSK Reporter's IPFIX-over-UDP-Protokoll an
  report.pskreporter.info:4739. Spec siehe https://pskreporter.info/pskdev.html.
  Wir batchen Decodes in 1-Minuten-Fenstern damit der Spotter nicht für
  jeden Decode ein eigenes UDP-Paket bekommt.

Cache: 5 min für Download-Queries (PSK Reporter aggregiert in 5-min-Slots).
"""

from __future__ import annotations

import asyncio
import logging
import random
import socket
import struct
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from xml.etree import ElementTree as ET

from .base import Integration

log = logging.getLogger(__name__)

PSK_QUERY_URL = "https://pskreporter.info/cgi-bin/pskquery5.pl"
PSK_UPLOAD_HOST = "report.pskreporter.info"
PSK_UPLOAD_PORT = 4739


@dataclass(slots=True)
class _PendingSpot:
    """Eine Empfangs-Meldung im Upload-Buffer."""
    sender_call: str
    sender_grid: str | None
    snr_db: int
    band_hz: int
    mode: str
    decoded_at: datetime


@dataclass(frozen=True, slots=True)
class HeardReport:
    """One reception report — somebody heard *us*."""

    rx_call: str
    rx_grid: str | None
    snr_db: int | None
    band: str | None
    mode: str | None
    received_at: datetime


class PskReporterClient(Integration):
    name = "psk_reporter"

    def __init__(
        self,
        *,
        enabled: bool = True,
        upload_decodes: bool = True,
        timeout: float = 5.0,
        cache_ttl_s: float = 300.0,  # 5 minutes
        my_call: str = "",
        my_grid: str = "",
        contact_email: str | None = None,
    ) -> None:
        super().__init__(
            enabled=enabled, base_url=None, timeout=timeout, cache_ttl_s=cache_ttl_s
        )
        self.upload_decodes = upload_decodes
        # appcontact-Param (pskreporter.info-Policy) — Betreiber kann uns
        # bei Last-Problemen kontaktieren statt blind zu blocken.
        self.contact_email = (contact_email or "").strip() or None
        self.my_call = my_call.upper()
        self.my_grid = my_grid.upper()
        # IPFIX-Observation-Domain-ID: 1 ist Standard für PSK Reporter.
        self._observation_domain_id = 1
        # Sequence-Counter über die UDP-Verbindungs-Lebensdauer.
        self._sequence_number = 0
        # Random Source-ID — bleibt für die ganze Session konstant.
        self._source_id = random.randint(1, 0xFFFF_FFFF)
        # Decode-Buffer (sammelt bis zum nächsten Flush)
        self._pending: list[_PendingSpot] = []
        # Wann ist der nächste Flush fällig? Frequenz auf 1× / 5 min
        # damit wir den Server nicht überlasten.
        self._next_flush_at: float = 0.0
        self._flush_interval_s: float = 300.0  # 5 min
        # Template wird einmalig vor dem ersten Data-Set geschickt.
        # Wir senden's bei jedem Flush erneut damit der Server uns auch
        # nach Timeout-Verlust noch versteht — UDP-Cost vernachlässigbar.

    async def who_heard_me(self, callsign: str, hours: int = 24) -> list[HeardReport]:
        """Return reception reports for *callsign* in the last *hours*."""
        if not self.enabled:
            return []
        key = f"{callsign.upper()}:{hours}"
        cached = await self.cache.get(key)
        if cached is not None:
            return cached  # type: ignore[no-any-return]
        params = {
            "senderCallsign": callsign.upper(),
            "flowStartSeconds": -hours * 3600,
            "mode": "FT8",
        }
        if self.contact_email:
            params["appcontact"] = self.contact_email
        try:
            r = await self._get(PSK_QUERY_URL, params=params)
        except Exception:
            stale, _ = await self.cache.get_stale_ok(key)
            return list(stale) if stale else []  # type: ignore[arg-type]
        reports = _parse_query(r.text)
        await self.cache.set(key, reports)
        return reports

    async def upload_decode(
        self,
        *,
        sender_call: str,
        sender_grid: str | None,
        rx_callsign: str,
        snr_db: int,
        band_hz: int,
        mode: str = "FT8",
        decoded_at: datetime | None = None,
    ) -> None:
        """Buffer one decode; PSK Reporter UDP-Flush passiert alle 5 min.

        Nicht-blockierend. Wenn Buffer leer war und seit dem letzten
        Flush genug Zeit vergangen ist, kicken wir async den Flush an.
        """
        if not self.enabled or not self.upload_decodes:
            return
        if not self.my_call:
            log.debug("psk_reporter upload skipped: my_call not configured")
            return
        self._pending.append(_PendingSpot(
            sender_call=sender_call.upper(),
            sender_grid=sender_grid.upper() if sender_grid else None,
            snr_db=snr_db,
            band_hz=band_hz,
            mode=mode,
            decoded_at=decoded_at or datetime.now(UTC),
        ))
        now = time.time()
        if now >= self._next_flush_at:
            self._next_flush_at = now + self._flush_interval_s
            # Fire-and-forget; falls UDP-Send fehlschlägt loggen wir's
            # nur und versuchen's beim nächsten Slot wieder.
            asyncio.create_task(self._flush(), name="psk-reporter-flush")

    async def _flush(self) -> None:
        if not self._pending:
            return
        spots = self._pending[:]  # snapshot
        self._pending.clear()
        try:
            pkt = self._build_ipfix_packet(spots)
            # asyncio.DatagramTransport wäre eleganter, aber synchroner
            # sendto in einem run_in_executor reicht völlig — wir
            # schicken ein Paket pro Flush.
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._send_udp, pkt)
            log.info("psk_reporter: %d decodes uploaded", len(spots))
        except Exception as exc:
            log.warning("psk_reporter UDP flush failed: %s", exc)

    def _send_udp(self, packet: bytes) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(3.0)
            s.sendto(packet, (PSK_UPLOAD_HOST, PSK_UPLOAD_PORT))

    # --- IPFIX-Encoding ----------------------------------------------------
    # PSK Reporter erwartet:
    #   Header (16 B)
    #   Template-Sets für Receiver + Sender
    #   Data-Set Receiver (einmal) + Data-Set Sender (n×)
    # Template-IDs ab 256 (256-65535 = data set IDs frei wählbar).

    _RX_TEMPLATE_ID = 0x0507  # PSK Reporter docs setzen das exakt so
    _TX_TEMPLATE_ID = 0x0508

    def _build_ipfix_packet(self, spots: list[_PendingSpot]) -> bytes:
        """Pack header + 2 templates + 2 data sets in ein UDP-Paket."""
        export_time = int(time.time())
        self._sequence_number += 1

        # --- Template-Set: Definition der zwei Records ---
        # Receiver-Template (was wir = unser Pi melden)
        rx_template = self._pack_template(
            self._RX_TEMPLATE_ID,
            [
                (0x8002 | 0x8000, 0xFFFF),  # ReceiverCallsign (varlen) PEN=Adif
                (0x8004 | 0x8000, 0xFFFF),  # ReceiverLocator (varlen)
                (0x8008 | 0x8000, 0xFFFF),  # DecodingSoftware (varlen)
                (0x8009 | 0x8000, 0xFFFF),  # AntennaInformation (varlen)
            ],
            enterprise_id=30351,
        )
        # Sender-Template (eine pro empfangener Station)
        tx_template = self._pack_template(
            self._TX_TEMPLATE_ID,
            [
                (0x8001 | 0x8000, 0xFFFF),  # SenderCallsign (varlen)
                (0x8005, 4),                # Frequency uint32
                (0x8006, 1),                # sNR int8
                (0x8003 | 0x8000, 0xFFFF),  # Mode (varlen)
                (0x8004 | 0x8000, 0xFFFF),  # SenderLocator (varlen) — re-use
                (0x8096, 4),                # InfoSource uint32 (4=automatic)
                (150, 4),                   # flowStartSeconds (IANA std)
            ],
            enterprise_id=30351,
        )
        template_set = self._pack_set(2, rx_template + tx_template)

        # --- Data-Set Receiver (genau 1 Record) ---
        rx_record = (
            self._pack_varstr(self.my_call)
            + self._pack_varstr(self.my_grid)
            + self._pack_varstr("ft8-hochgericht/0.1")
            + self._pack_varstr("IC-7300 + EFHW")
        )
        rx_data = self._pack_set(self._RX_TEMPLATE_ID, rx_record)

        # --- Data-Set Sender (n Records) ---
        tx_records = b""
        for s in spots:
            tx_records += (
                self._pack_varstr(s.sender_call)
                + struct.pack(">I", s.band_hz)
                + struct.pack(">b", max(-128, min(127, s.snr_db)))
                + self._pack_varstr(s.mode)
                + self._pack_varstr(s.sender_grid or "")
                + struct.pack(">I", 4)  # automatic
                + struct.pack(">I", int(s.decoded_at.timestamp()))
            )
        tx_data = self._pack_set(self._TX_TEMPLATE_ID, tx_records)

        body = template_set + rx_data + tx_data
        header = struct.pack(
            ">HHIII",
            10,                  # IPFIX version
            16 + len(body),      # total length
            export_time,
            self._sequence_number,
            self._observation_domain_id,
        )
        return header + body

    @staticmethod
    def _pack_template(template_id: int, fields: list[tuple[int, int]],
                       enterprise_id: int = 30351) -> bytes:
        """Eine Template-Definition. fields = [(field_id, length), ...].
        Enterprise-Bit (0x8000) auf field_id signalisiert PEN-Verwendung."""
        out = struct.pack(">HH", template_id, len(fields))
        for fid, flen in fields:
            out += struct.pack(">HH", fid, flen)
            if fid & 0x8000:
                out += struct.pack(">I", enterprise_id)
        return out

    @staticmethod
    def _pack_set(set_id: int, body: bytes) -> bytes:
        """Set-Header (4 B) + Body, gepaddet auf 4-Byte-Boundary."""
        total = 4 + len(body)
        pad = (4 - total % 4) % 4
        return struct.pack(">HH", set_id, total + pad) + body + b"\x00" * pad

    @staticmethod
    def _pack_varstr(s: str) -> bytes:
        """IPFIX-Varlen-String: 1 B Länge (oder 3 B wenn >254) + UTF-8."""
        b = s.encode("utf-8", errors="replace")
        if len(b) < 255:
            return struct.pack(">B", len(b)) + b
        return struct.pack(">BH", 255, len(b)) + b


# ---------------------------------------------------------------------------
def _parse_query(xml_text: str) -> list[HeardReport]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    out: list[HeardReport] = []
    for rec in root.iter("receptionReport"):
        try:
            ts = int(rec.attrib.get("flowStartSeconds", "0"))
            received_at = datetime.fromtimestamp(ts) if ts else datetime.utcnow()
            snr = rec.attrib.get("sNR")
            band_hz_str = rec.attrib.get("frequency")
            band = _band_from_freq(int(band_hz_str)) if band_hz_str else None
            out.append(
                HeardReport(
                    rx_call=rec.attrib.get("receiverCallsign", ""),
                    rx_grid=rec.attrib.get("receiverLocator"),
                    snr_db=int(snr) if snr is not None else None,
                    band=band,
                    mode=rec.attrib.get("mode"),
                    received_at=received_at,
                )
            )
        except (ValueError, KeyError):
            continue
    return out


_BANDS = (
    (1_800_000, 2_000_000, "160m"),
    (3_500_000, 4_000_000, "80m"),
    (5_330_000, 5_410_000, "60m"),
    (7_000_000, 7_300_000, "40m"),
    (10_100_000, 10_150_000, "30m"),
    (14_000_000, 14_350_000, "20m"),
    (18_068_000, 18_168_000, "17m"),
    (21_000_000, 21_450_000, "15m"),
    (24_890_000, 24_990_000, "12m"),
    (28_000_000, 29_700_000, "10m"),
    (50_000_000, 54_000_000, "6m"),
    (144_000_000, 148_000_000, "2m"),
    (430_000_000, 450_000_000, "70cm"),
)


def _band_from_freq(hz: int) -> str | None:
    for lo, hi, name in _BANDS:
        if lo <= hz <= hi:
            return name
    return None
