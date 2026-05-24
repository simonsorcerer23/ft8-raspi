"""Captive-Portal probe handlers (architecture.md §3.2).

Android and other OSes ping well-known URLs after joining a WiFi to
check whether they actually have internet. If the response is wrong
they flag "no internet" and may auto-disconnect when LTE recovers.

In the Pi's AP-fallback mode we have to:
  * answer the known probes with HTTP 204 No Content
  * redirect any *other* HTTP request to our UI (``/``)

Reference URLs we recognise:
  * connectivitycheck.gstatic.com/generate_204    (Android)
  * www.google.com/generate_204                   (Android variant)
  * clients3.google.com/generate_204              (older Android)
  * connectivity-check.ubuntu.com                 (Ubuntu)
  * captive.apple.com/*                           (iOS — handled for completeness)
  * www.msftconnecttest.com/connecttest.txt       (Windows)

These checks rely on the *Host* header rather than the path, so we
register both flavours: a generic ``/generate_204`` path and an explicit
catch-all that inspects ``Host``.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import Response

log = logging.getLogger(__name__)

_CAPTIVE_HOSTS = {
    "connectivitycheck.gstatic.com",
    "www.google.com",
    "clients3.google.com",
    "clients1.google.com",
    "connectivity-check.ubuntu.com",
    "captive.apple.com",
    "www.msftconnecttest.com",
    "detectportal.firefox.com",
}

_GENERATE_204_PATHS = {
    "/generate_204",
    "/gen_204",
}


def is_captive_probe(host: str | None, path: str) -> bool:
    """Return True if the request looks like an OS connectivity check."""
    if not host:
        return False
    host = host.split(":")[0].lower()
    if host in _CAPTIVE_HOSTS:
        return True
    if path in _GENERATE_204_PATHS:
        return True
    if host == "captive.apple.com":
        return True
    if path == "/connecttest.txt":
        return True
    if path == "/ncsi.txt":
        return True
    return False


def register(app: FastAPI) -> None:
    """Hook the captive routes into *app*."""

    @app.get("/generate_204", include_in_schema=False)
    async def _generate_204_path() -> Response:
        return Response(status_code=204)

    @app.get("/gen_204", include_in_schema=False)
    async def _generate_204_short() -> Response:
        return Response(status_code=204)

    @app.get("/hotspot-detect.html", include_in_schema=False)
    async def _apple_probe() -> Response:
        # iOS specifically expects this exact body
        return Response(
            content="<HTML><HEAD><TITLE>Success</TITLE></HEAD>"
            "<BODY>Success</BODY></HTML>",
            media_type="text/html",
        )

    @app.get("/ncsi.txt", include_in_schema=False)
    async def _windows_ncsi() -> Response:
        return Response(content="Microsoft NCSI", media_type="text/plain")

    @app.get("/connecttest.txt", include_in_schema=False)
    async def _windows_connecttest() -> Response:
        return Response(content="Microsoft Connect Test", media_type="text/plain")

    @app.middleware("http")
    async def _captive_redirect_unknown(request: Request, call_next):  # type: ignore[no-untyped-def]
        """If a probe came in with an unknown path but a captive Host,
        redirect to our UI so the device renders the appliance page."""
        host = request.headers.get("host", "")
        path = request.url.path
        if is_captive_probe(host, path):
            # Known probe — let the specific handlers above answer.
            response = await call_next(request)
            if response.status_code == 404:
                # Generic fallback: 204 keeps the device happy
                return Response(status_code=204)
            return response
        return await call_next(request)
