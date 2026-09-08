"""Stufe 3 des Decoders: WSJT-X' eigener Decoder ``jt9`` als Unterprozess (2026-09-08).

WSJT-X ist GPL und Fortran; wir kopieren nichts, sondern rufen das Programm
``jt9`` aus dem Debian-Paket ``wsjtx`` mit einer WAV-Datei des Slots auf und
lesen seine Decode-Zeilen. Gemessen am Pi 4B auf den 22 Referenzaufnahmen:
Tiefe 2 trifft 97,5 % der WSJT-X-Decodes in 6,0 s (max 9,1 s); Tiefe 3
98,9 % in 11,1 s (max 16,7 s) — zu langsam fuer jeden Slot. Zusammen mit
unserem Decoder (Stufe 2) 98,3 % plus 49 Decodes, die die Referenz nicht hat.

Kein jt9 installiert -> Stufe 3 bleibt still (available() == False).
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path

from .ft8_native import ShimDecode

log = logging.getLogger(__name__)

_LINE = re.compile(r"^\d{6}\s+(-?\d+)\s+(-?[\d.]+)\s+(\d+)\s+~\s+(.*?)\s*$")
_SAMPLE_RATE = 12000


def jt9_path() -> str | None:
    return shutil.which("jt9")


def available() -> bool:
    return jt9_path() is not None


def work_dir() -> Path:
    """tmpfs, wenn vorhanden — jede Sekunde 360 kB WAV auf die SD-Karte waere Unsinn."""
    base = Path("/dev/shm") if Path("/dev/shm").is_dir() else Path(tempfile.gettempdir())
    d = base / "ft8-jt9"
    d.mkdir(parents=True, exist_ok=True)
    return d


def parse_jt9_output(text: str) -> list[ShimDecode]:
    """WSJT-X-Zeilen ``HHMMSS  snr  dt  freq ~  message`` -> ShimDecode.
    dt ist die WSJT-X-Konvention (relativ zu 0,5 s nach der Slotgrenze) — seit
    v0.70.0 dieselbe wie bei unserem Decoder."""
    out: list[ShimDecode] = []
    for line in text.splitlines():
        m = _LINE.match(line.rstrip())
        if not m:
            continue
        msg = " ".join(m.group(4).split())
        if not msg:
            continue
        out.append(ShimDecode(message=msg, snr_db_est=int(m.group(1)), dt_s=float(m.group(2)),
                              freq_hz=float(m.group(3)), score=0))
    return out


def run_jt9(pcm: bytes, *, depth: int = 2, timeout_s: float = 12.0, mode: str = "FT8") -> list[ShimDecode]:
    """Einen Slot (int16 mono 12 kHz) durch jt9 decodieren. Blockiert — im Thread aufrufen."""
    exe = jt9_path()
    if exe is None:
        return []
    if mode != "FT8":
        return []   # FT4-Flag von jt9 nicht verifiziert — erst messen, dann freigeben
    d = work_dir()
    wav_path = d / "slot.wav"
    with wave.open(str(wav_path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(_SAMPLE_RATE)
        w.writeframes(pcm)
    cmd = [exe, "-8", "-d", str(int(depth)), "-a", str(d), "-e", os.path.dirname(exe), str(wav_path)]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        log.warning("jt9: Timeout nach %.0f s (Tiefe %d)", timeout_s, depth)
        return []
    if res.returncode != 0:
        log.warning("jt9: rc=%s %s", res.returncode, (res.stderr or "")[:200])
    return parse_jt9_output(res.stdout)
