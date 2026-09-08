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

# FT8-Zeilen tragen "~", FT4-Zeilen "+" als Modus-Marker; hinter der Nachricht
# kann eine AP-Kennung (a1..a6) oder ein Qualitaetswert stehen — die Nachricht
# endet vor mindestens zwei Leerzeichen.
_LINE = re.compile(r"^\d{6}\s+(-?\d+)\s+(-?[\d.]+)\s+(\d+)\s+[~+]\s+(.*?)(?:\s{2,}(.*))?\s*$")
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


def parse_jt9_output(text: str, *, drop_uncertain: bool = True) -> list[ShimDecode]:
    """WSJT-X-Zeilen ``HHMMSS  snr  dt  freq ~  message   [a2|? a2]`` -> ShimDecode.
    dt ist die WSJT-X-Konvention (relativ zu 0,5 s nach der Slotgrenze) — seit
    v0.70.0 dieselbe wie bei unserem Decoder. AP-Decodes tragen ``a1..a6``;
    ``?`` davor heisst „unsicher" — die werden verworfen (gemessen 2026-09-08:
    AP -X 1 holt an der Grenze 2 von 6 verrauschten Antworten an uns, davon
    eine mit ``?``)."""
    out: list[ShimDecode] = []
    for line in text.splitlines():
        m = _LINE.match(line.rstrip())
        if not m:
            continue
        msg = " ".join(m.group(4).split())
        if not msg:
            continue
        tail = (m.group(5) or "").strip()
        if drop_uncertain and tail.startswith("?"):
            continue
        out.append(ShimDecode(message=msg, snr_db_est=int(m.group(1)), dt_s=float(m.group(2)),
                              freq_hz=float(m.group(3)), score=0))
    return out


def build_cmd(exe: str, wav_path: Path, d: Path, *, depth: int, mode: str,
              my_call: str | None = None, my_grid: str | None = None,
              his_call: str | None = None, his_grid: str | None = None,
              ap_flags: int = 0) -> list[str]:
    """jt9-Kommandozeile (Optionen aus lib/jt9.f90 in WSJT-X): -8 FT8 / -5 FT4,
    -d Tiefe, -c/-G eigener Call+Grid, -x/-g Partner, -X „experience based
    decoding flags" (AP). Call/Grid nur, wenn gesetzt."""
    cmd = [exe, "-5" if mode == "FT4" else "-8", "-d", str(int(depth)), "-a", str(d), "-e", os.path.dirname(exe)]
    if my_call:
        cmd += ["-c", my_call]
    if my_grid:
        cmd += ["-G", my_grid[:4]]
    if his_call:
        cmd += ["-x", his_call]
    if his_grid:
        cmd += ["-g", his_grid[:4]]
    if ap_flags:
        cmd += ["-X", str(int(ap_flags))]
    cmd.append(str(wav_path))
    return cmd


def run_jt9(pcm: bytes, *, depth: int = 2, timeout_s: float = 12.0, mode: str = "FT8",
            my_call: str | None = None, my_grid: str | None = None,
            his_call: str | None = None, his_grid: str | None = None,
            ap_flags: int = 0) -> list[ShimDecode]:
    """Einen Slot (int16 mono 12 kHz; FT8 15 s, FT4 7,5 s) durch jt9 decodieren.
    Blockiert — im Thread aufrufen."""
    exe = jt9_path()
    if exe is None:
        return []
    d = work_dir()
    wav_path = d / ("slot_ft4.wav" if mode == "FT4" else "slot.wav")
    with wave.open(str(wav_path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(_SAMPLE_RATE)
        w.writeframes(pcm)
    cmd = build_cmd(exe, wav_path, d, depth=depth, mode=mode, my_call=my_call, my_grid=my_grid,
                    his_call=his_call, his_grid=his_grid, ap_flags=ap_flags)
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        log.warning("jt9: Timeout nach %.0f s (Tiefe %d)", timeout_s, depth)
        return []
    if res.returncode != 0:
        log.warning("jt9: rc=%s %s", res.returncode, (res.stderr or "")[:200])
    return parse_jt9_output(res.stdout)
