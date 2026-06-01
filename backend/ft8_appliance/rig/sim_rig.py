"""Simulierter Transceiver fuer den Demo-Modus (kein echtes rigctld noetig).

Implementiert die vom Orchestrator genutzte Teilmenge der RigctldClient-API
(connect/close/snapshot + set_freq/set_mode/set_ptt/set_rfpower) und liefert
plausible, lebendige Werte — damit das Rig-Panel im Demo nicht „offline"
aussieht. Reagiert auf Set-Kommandos, damit Frequenz/PTT konsistent bleiben.
"""
from __future__ import annotations

from .rigctld_client import RigSnapshot


class SimRig:
    """Fake-Rig fuer demo_mode. Keine Netzwerk-/Hardware-Abhaengigkeit."""

    def __init__(self, freq_hz: int = 21_074_000, model: str = "IC-7300 (Demo)") -> None:
        self._freq_hz = freq_hz
        self._mode = "PKTUSB"
        self._bandwidth_hz = 2700
        self._ptt = False
        self._rfpower_norm = 0.5  # 50 % → ~50 W
        self.model = model

    async def connect(self) -> None:  # immer „verbunden"
        return None

    async def close(self) -> None:
        return None

    async def set_freq(self, hz: int) -> None:
        self._freq_hz = int(hz)

    async def set_mode(self, mode: str, bandwidth_hz: int = 2700) -> None:
        self._mode = mode
        self._bandwidth_hz = bandwidth_hz

    async def set_ptt(self, on: bool) -> None:
        self._ptt = bool(on)

    async def set_rfpower(self, norm: float) -> None:
        self._rfpower_norm = max(0.0, min(1.0, float(norm)))

    async def snapshot(self) -> RigSnapshot:
        # Plausible RX-Werte; im (simulierten) TX flacht SWR leicht ab.
        return RigSnapshot(
            freq_hz=self._freq_hz,
            mode=self._mode,
            bandwidth_hz=self._bandwidth_hz,
            ptt=self._ptt,
            swr=1.1,
            rfpower_norm=self._rfpower_norm,
            rfpower_meter=(self._rfpower_norm if self._ptt else 0.0),
            s_meter_db=(None if self._ptt else -73),
            alc=0.0,
            af_gain=0.4,
            rf_gain=1.0,
            nr_level=0.0,
            preamp_on=True,
            att_on=False,
            nb_on=False,
            agc_mode="FAST",
            vfo="VFOA",
            split_on=False,
            battery_v=None,
            internal_temp_c=42.0,
        )
