"""FastAPI dependency wiring — gives routes access to the running orchestrator.

The orchestrator is attached to ``app.state.orchestrator`` by the
factory; routes pull it out through this dependency so test fixtures
can substitute their own instance.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request

from ..runtime import Orchestrator


def get_orchestrator(request: Request) -> Orchestrator:
    orch: Orchestrator | None = getattr(request.app.state, "orchestrator", None)
    if orch is None:
        raise HTTPException(status_code=503, detail="orchestrator not attached")
    return orch


# Convenience alias for type hints
OrchestratorDep = Depends(get_orchestrator)
