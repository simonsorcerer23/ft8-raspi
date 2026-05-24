"""Runtime orchestrator — glues hardware clients, state machine, and decode pipeline."""

from __future__ import annotations

from .orchestrator import DecodeSource, Orchestrator, OrchestratorStatus
from .production import build_production_orchestrator
from .slot_clock import FT4_SLOT_SECONDS, SLOT_SECONDS, FakeSlotClock, SlotClock, SlotTick

__all__ = [
    "FT4_SLOT_SECONDS",
    "SLOT_SECONDS",
    "DecodeSource",
    "FakeSlotClock",
    "Orchestrator",
    "OrchestratorStatus",
    "SlotClock",
    "SlotTick",
    "build_production_orchestrator",
]
