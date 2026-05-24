"""USB rig auto-detection via ``/dev/serial/by-id/``.

The kernel populates that directory with symlinks named after the USB
descriptor's vendor + product strings, e.g.
``usb-Icom_Inc._IC-705-if00``. Icom radios with native USB (705, 9700,
7610) carry the model in the string and are unambiguous. The IC-7300
uses a generic Silicon Labs CP2102N USB-UART bridge, indistinguishable
from many other CAT cables and sensors — best we can do there is mark
it as a low-confidence candidate.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

log = logging.getLogger(__name__)

_BY_ID = Path("/dev/serial/by-id")

# (regex on the symlink basename, friendly model id, confidence, human label)
_PATTERNS: list[tuple[re.Pattern[str], str, str, str]] = [
    (re.compile(r"usb-Icom_Inc\._IC-705-"),  "ic705",  "high", "Icom IC-705"),
    (re.compile(r"usb-Icom_Inc\._IC-9700-"), "ic9700", "high", "Icom IC-9700"),
    (re.compile(r"usb-Icom_Inc\._IC-7610-"), "ic7610", "high", "Icom IC-7610"),
    # QRP Labs QMX/QMX+: native USB-CDC vom STM32 onboard, eindeutig
    # erkennbar an "QRP_Labs" im Hersteller-String. Wir mappen
    # generisch auf qmx_plus, da QMX und QMX+ aus Software-Sicht
    # identisch sind (gleiche Firmware-Familie, gleiche CAT).
    (re.compile(r"usb-QRP_Labs"),            "qmx_plus", "high", "QRP Labs QMX/QMX+"),
    # IC-7300 uses a Silicon Labs CP2102N USB-UART bridge. Strictly speaking
    # that chip also appears in unrelated CAT cables / sensors / weather
    # stations, but the deploy target is an Icom-only ham shack — treat the
    # match as a confident IC-7300. If you later add a Yaesu/CAT-cable that
    # also uses CP210x, lower this back to "low" and surface the warning.
    (re.compile(r"usb-Silicon_Labs_CP2102"), "ic7300", "high", "Icom IC-7300"),
]


@dataclass(frozen=True)
class RigDetection:
    """One detected USB serial device that might be a supported rig."""

    model: Literal["ic705", "ic7300", "ic9700", "ic7610", "qmx_plus"]
    confidence: Literal["high", "low"]
    serial_device: str           # /dev/serial/by-id/usb-...
    description: str             # human-readable

    def as_dict(self) -> dict[str, str]:
        return {
            "model": self.model,
            "confidence": self.confidence,
            "serial_device": self.serial_device,
            "description": self.description,
        }


def detect_rigs(by_id_dir: Path | None = None) -> list[RigDetection]:
    """Scan /dev/serial/by-id/ and return candidate matches.

    Empty list = no supported rig pattern matched (rig not plugged in, or
    something exotic). High-confidence entries first.
    """
    root = by_id_dir or _BY_ID
    if not root.is_dir():
        return []
    out: list[RigDetection] = []
    for entry in sorted(root.iterdir()):
        name = entry.name
        for pattern, model, conf, label in _PATTERNS:
            if pattern.search(name):
                out.append(
                    RigDetection(
                        model=model,  # type: ignore[arg-type]
                        confidence=conf,  # type: ignore[arg-type]
                        serial_device=str(entry),
                        description=label,
                    )
                )
                break  # one match per entry — first pattern wins
    # High-confidence Icom natives first, then CP210x guesses.
    out.sort(key=lambda d: (0 if d.confidence == "high" else 1, d.serial_device))
    return out
