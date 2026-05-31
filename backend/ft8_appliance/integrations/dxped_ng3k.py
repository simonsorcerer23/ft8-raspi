"""NG3K ADXO Parser — Announced DX Operations.

Quelle: https://www.ng3k.com/Misc/adxo.html — kanonische Liste anstehender
DXpeditions, seit Jahren stabiles HTML-Format mit klaren CSS-Klassen.

HTML-Struktur (relevante Teile):

  <tr class="adxoitem" bgcolor="#FFDAB9">
    <td class="date">2026 Mar25</td>           <!-- Start -->
    <td class="date">2026 May31</td>           <!-- Ende -->
    <td class="cty">Galapagos</td>             <!-- DXCC-Entity -->
    <td>
      <span class="call">HD8R</span>
      ...
    </td>
    <td class="qsl">M0OXO</td>
    <td class="rep">...</td>
    <td class="info">By LU5DX; HF; QRV for CQ WPX SSB</td>
  </tr>

Wir parsen jeden ``<tr class="adxoitem">`` als ein Entry und extrahieren
(start, end, dxcc, call, info) als ``DxpedEntry``.

Robustness:
- Date-Format ``%Y %b%d`` (z.B. "2026 Mar25") wird mit strptime geparst.
- Falls Format mal kippt → Entry uebersprungen, nicht crashen.
- Mehrfach-Calls in einer Zeile (z.B. "K3LR/3 + W3LPL") sind selten und
  werden nur als erster Call extrahiert.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

log = logging.getLogger(__name__)

NG3K_URL = "https://www.ng3k.com/Misc/adxo.html"

# <tr class="adxoitem" ...> ... </tr> — Block je DXpedition
_ROW_RE = re.compile(
    r'<tr\s+class="adxoitem"[^>]*>(.*?)</tr>',
    re.DOTALL | re.IGNORECASE,
)

# <td class="date">2026 Mar25</td>
_DATE_TD_RE = re.compile(
    r'<td\s+class="date">\s*([^<]+?)\s*</td>',
    re.IGNORECASE,
)

# <span class="call">HD8R</span>  (call kann Slashes enthalten: FO/F6BCW)
_CALL_RE = re.compile(
    r'<span\s+class="call">\s*([^<\s]+?)\s*</span>',
    re.IGNORECASE,
)

# <td class="cty">Galapagos</td>
_CTY_RE = re.compile(
    r'<td\s+class="cty">\s*([^<]+?)\s*</td>',
    re.IGNORECASE,
)

# <td class="info">...</td>
_INFO_RE = re.compile(
    r'<td\s+class="info">\s*(.+?)\s*</td>',
    re.DOTALL | re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class DxpedEntry:
    call: str
    start: datetime
    end: datetime
    dxcc_name: str
    info: str


def _parse_date(s: str) -> datetime | None:
    """Parse '2026 Mar25' → datetime(2026, 3, 25, 0, 0, UTC)."""
    s = s.strip()
    # Format '2026 Mar25' (kein Leerzeichen zwischen Mar und 25)
    # → strptime %Y %b%d
    for fmt in ("%Y %b%d", "%Y %b %d"):
        try:
            d = datetime.strptime(s, fmt)
            return d.replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


def _strip_html(s: str) -> str:
    """Sehr einfacher HTML-Stripper fuer info-Felder (lasse Text uebrig)."""
    s = re.sub(r"<[^>]+>", "", s)
    s = re.sub(r"&nbsp;", " ", s)
    s = re.sub(r"&amp;", "&", s)
    s = re.sub(r"&lt;", "<", s)
    s = re.sub(r"&gt;", ">", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def parse_ng3k_html(html: str) -> list[DxpedEntry]:
    """Parse NG3K ADXO HTML → Liste von DxpedEntry.

    Fehlerhafte/unparsbare Zeilen werden uebersprungen, nicht throwt.
    """
    entries: list[DxpedEntry] = []
    for row_match in _ROW_RE.finditer(html):
        row = row_match.group(1)
        dates = _DATE_TD_RE.findall(row)
        if len(dates) < 2:
            continue
        start = _parse_date(dates[0])
        end = _parse_date(dates[1])
        if start is None or end is None:
            continue
        if end < start:
            continue  # absurd → skip
        call_match = _CALL_RE.search(row)
        if call_match is None:
            continue
        call = call_match.group(1).upper().strip()
        if not call or len(call) > 13:
            continue
        cty_match = _CTY_RE.search(row)
        dxcc_name = _strip_html(cty_match.group(1)) if cty_match else ""
        info_match = _INFO_RE.search(row)
        info = _strip_html(info_match.group(1)) if info_match else ""
        # Info auf 200 Zeichen begrenzen (DB-freundlich)
        if len(info) > 200:
            info = info[:197] + "..."
        entries.append(DxpedEntry(
            call=call, start=start, end=end,
            dxcc_name=dxcc_name, info=info,
        ))
    return entries


async def fetch_ng3k(url: str = NG3K_URL, timeout: float = 15.0) -> list[DxpedEntry]:
    """HTTP-Fetch + Parse NG3K ADXO.

    Wirft auf Netzwerk-Fehler durch (Caller entscheidet was zu tun ist —
    typisch silent-skip mit Log-Hinweis).
    """
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.get(url, headers={"User-Agent": "ft8-appliance/1.0"})
        r.raise_for_status()
        # NG3K liefert ISO-8859-1, httpx erkennt das aus dem META-Tag.
        html = r.text
    entries = parse_ng3k_html(html)
    log.info("ng3k: %d DXpedition entries parsed", len(entries))
    return entries
