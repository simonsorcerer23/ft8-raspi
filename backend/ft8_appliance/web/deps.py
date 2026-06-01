"""FastAPI dependency wiring — gives routes access to the running orchestrator.

The orchestrator is attached to ``app.state.orchestrator`` by the
factory; routes pull it out through this dependency so test fixtures
can substitute their own instance.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request

from .. import i18n as _i18n
from ..runtime import Orchestrator


def get_orchestrator(request: Request) -> Orchestrator:
    orch: Orchestrator | None = getattr(request.app.state, "orchestrator", None)
    if orch is None:
        raise HTTPException(status_code=503, detail="orchestrator not attached")
    return orch


def ui_lang(request: Request) -> str:
    """Dependency: the language for *this* response.

    The frontend appends ``?lang=de|en`` to every request; we fall back to
    the configured default. Lives here (not in i18n.py) so the i18n module
    stays framework-free.
    """
    q = request.query_params.get("lang")
    if q in ("de", "en"):
        return q
    return _i18n.default_lang()


# Convenience alias for type hints
OrchestratorDep = Depends(get_orchestrator)
