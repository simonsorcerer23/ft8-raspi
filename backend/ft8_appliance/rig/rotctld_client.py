"""Hamlib rotctld — schlanker TCP-Client fuer die Rotor-Nachfuehrung.

rotctld spricht dasselbe Zeilenprotokoll wie rigctld (``man rotctld``):

* ``p``            → zwei Zeilen: Azimut, Elevation
* ``P <az> <el>``  → ``RPRT 0`` bei Erfolg, ``RPRT -n`` bei Fehler
* ``S``            → Stopp, ``RPRT 0``

Fuer Raymonds Create RC5B-3 ist das Interface ein DF9GR ERC, in hamlib
Modell 404. Getestet wird gegen ``rotctld -m 1`` (Dummy).

Jede Abfrage oeffnet die Verbindung bei Bedarf neu: Ein Rotor-Interface
haengt am USB und verschwindet beim Ausschalten des Steuergeraets, ein
gehaltener Socket waere dann tot.
"""

from __future__ import annotations

import asyncio


class RotctldFehler(RuntimeError):
    pass


class RotctldClient:
    def __init__(self, host: str = "127.0.0.1", port: int = 4533, timeout: float = 5.0) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._lock = asyncio.Lock()

    async def _verbinden(self) -> None:
        self._reader, self._writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port), timeout=self.timeout
        )

    async def close(self) -> None:
        w, self._reader, self._writer = self._writer, None, None
        if w is not None:
            try:
                w.close()
                await w.wait_closed()
            except Exception:
                pass

    async def _befehl(self, zeile: str, antwortzeilen: int) -> list[str]:
        async with self._lock:
            for versuch in (1, 2):
                try:
                    if self._writer is None:
                        await self._verbinden()
                    assert self._reader is not None and self._writer is not None
                    self._writer.write((zeile + "\n").encode())
                    await self._writer.drain()
                    out: list[str] = []
                    for _ in range(antwortzeilen):
                        raw = await asyncio.wait_for(self._reader.readline(), timeout=self.timeout)
                        if not raw:
                            raise ConnectionError("rotctld hat die Verbindung geschlossen")
                        text = raw.decode(errors="replace").strip()
                        if text.startswith("RPRT") and text != "RPRT 0":
                            raise RotctldFehler(f"{zeile!r}: {text}")
                        out.append(text)
                        if text.startswith("RPRT"):
                            break
                    return out
                except RotctldFehler:
                    raise
                except Exception:
                    await self.close()
                    if versuch == 2:
                        raise
        raise RotctldFehler("unerreichbar")  # pragma: no cover

    async def position(self) -> float:
        """Azimut in Grad, 0..360."""
        zeilen = await self._befehl("p", 2)
        try:
            return float(zeilen[0]) % 360.0
        except (IndexError, ValueError) as exc:
            raise RotctldFehler(f"unlesbare Position: {zeilen!r}") from exc

    async def drehe_auf(self, azimut: float) -> None:
        await self._befehl(f"P {float(azimut):.1f} 0", 1)

    async def stopp(self) -> None:
        await self._befehl("S", 1)
