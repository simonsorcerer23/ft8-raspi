"""API token authentication (v0.37.0, Audit 2026-05-30 SEC-C1).

The HTTP API controls a real transmitter and exposes credentials, so it
must not be open to anyone who can reach the port. We enforce a token via
an ASGI middleware:

  * Requests from **localhost** (127.0.0.1 / ::1) bypass auth entirely —
    being on the Pi already means full control (SSH), and the self-update
    script talks to the API over localhost.
  * Non-``/api`` paths (the SPA, static assets, map tiles, captive-portal
    probes) are public so the login page can load and the AP-fallback
    captive portal works.
  * Everything under ``/api`` requires the **master token** (``api_token``)
    via ``Authorization: Bearer <t>`` or ``?token=<t>`` (the query form is
    needed for ``EventSource``/SSE which cannot set headers).
  * A narrow **action token** (``ntfy_action_token``) is additionally
    accepted, but ONLY for the operational control toggles in
    :data:`ACTION_PATHS` — these are embedded in the ntfy lockscreen
    buttons. It never grants config/secret/shutdown access.
  * If no ``api_token`` is configured at all, auth fails OPEN (the appliance
    stays reachable) — startup always generates one, so this is only a
    brief first-boot / misconfig safety valve, logged loudly.
"""

from __future__ import annotations

import logging
import secrets

from starlette.requests import Request
from starlette.responses import JSONResponse

log = logging.getLogger(__name__)

# Control endpoints the narrow ntfy action token may invoke. Deliberately
# excludes shutdown/reboot and everything outside /api/control.
ACTION_PATHS: frozenset[str] = frozenset({
    "/api/control/stop",
    "/api/control/cq",
    "/api/control/auto-answer",
    "/api/control/reset-lock",
    "/api/control/set-mode",
    "/api/control/tx-power",
    "/api/control/set-freq",
    "/api/control/panic",
})

_LOCALHOSTS: frozenset[str] = frozenset({"127.0.0.1", "::1", "localhost"})


def generate_token() -> str:
    return secrets.token_urlsafe(24)


def _presented_token(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    qt = request.query_params.get("token")
    return qt.strip() if qt else None


def _is_localhost(request: Request) -> bool:
    client = request.client
    return bool(client and client.host in _LOCALHOSTS)


def _eq(a: str | None, b: str | None) -> bool:
    """Constant-time compare, tolerant of None."""
    if not a or not b:
        return False
    return secrets.compare_digest(a, b)


async def auth_middleware(request: Request, call_next):
    path = request.url.path

    # Public: everything that is not the JSON API or the SSE streams (SPA,
    # /assets, /tiles, captive-portal probes, "/"). SSE (/sse/*) IS gated —
    # EventSource kann keine Header → Token kommt dort als ?token=.
    if not (path.startswith("/api/") or path.startswith("/sse/")):
        return await call_next(request)

    # OpenAPI docs/schema stay reachable (no secrets); avoids confusing 401s.
    if path in ("/api/docs", "/api/openapi.json"):
        return await call_next(request)

    # Localhost is trusted (on-Pi == full access; self-update lives here).
    if _is_localhost(request):
        return await call_next(request)

    from ..config import get_config
    try:
        cfg = get_config()
    except Exception:
        cfg = None

    master = getattr(cfg, "api_token", None) if cfg else None
    action = getattr(cfg, "ntfy_action_token", None) if cfg else None

    # Fail-open if unconfigured (startup always sets one — brief safety valve).
    if not master:
        log.warning("auth: kein api_token gesetzt — Request OHNE Schutz "
                    "durchgelassen (%s)", path)
        return await call_next(request)

    presented = _presented_token(request)
    if _eq(presented, master):
        return await call_next(request)
    if _eq(presented, action) and path in ACTION_PATHS:
        return await call_next(request)

    return JSONResponse(status_code=401, content={"detail": "unauthorized"})
