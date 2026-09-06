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


# AP-Fallback-Subnetz + UI-Adresse (deploy/scripts/start-ap-fallback.sh,
# deploy/dnsmasq/ap-fallback.conf). Port 80 -> :8000 macht nftables.
AP_SUBNET_PREFIX = "192.168.66."
CAPTIVE_UI_URL = "http://192.168.66.1/"
# So lange nach dem ersten Kontakt eines Clients bekommen Probes den
# Redirect (Portal-Prompt), danach 204 (WLAN bleibt).
CAPTIVE_PROMPT_S = 300.0
_first_seen: dict[str, float] = {}


def _is_ap_client(client_ip: str | None) -> bool:
    return bool(client_ip) and str(client_ip).startswith(AP_SUBNET_PREFIX)


def _is_foreign_host(host: str | None) -> bool:
    h = (host or "").split(":")[0].lower()
    return h not in ("", "192.168.66.1", "ft8.local", "ft8", "localhost", "127.0.0.1")


def captive_prompt_due(client_ip: str | None, now: float | None = None) -> bool:
    """Redirect statt 204? Nur AP-Clients, nur im Fenster nach Erstkontakt."""
    import time as _time
    if not _is_ap_client(client_ip):
        return False
    now = _time.monotonic() if now is None else now
    first = _first_seen.setdefault(str(client_ip), now)
    if now - first > CAPTIVE_PROMPT_S * 4:
        # Alter Eintrag (Client kam vor Stunden) — als Neukontakt werten.
        _first_seen[str(client_ip)] = now
        first = now
    return (now - first) <= CAPTIVE_PROMPT_S


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
        """Captive-Verhalten fuer Clients im AP-Fallback-Subnetz.

        Zwei Dinge, die sich widersprechen, und der Kompromiss dazwischen
        (2026-09-06, Sebastian stand mit dem Handy vor dem Pi):

        * Antwortet der Pi auf Androids Connectivity-Probe mit 204, haelt
          Android das WLAN fuer "Internet ok" — es bleibt verbunden, aber
          es oeffnet KEIN Portal. Ohne die IP war Sebastian aufgeschmissen.
          Der Browser hilft nicht: Chrome geht HTTPS-first, das faengt
          weder DNAT noch dnsmasq.
        * Antwortet der Pi mit einem Redirect, zeigt Android "Anmelden bei
          <SSID>" und oeffnet beim Tippen die UI — markiert das WLAN aber
          als "kein Internet" und kann es fallen lassen, sobald LTE da ist
          (architecture.md §3.2, der Grund fuer das 204).

        Darum: die ersten CAPTIVE_PROMPT_S Sekunden nach dem ersten Kontakt
        eines Clients bekommen Probes den Redirect (Portal geht auf),
        danach 204 (Android bleibt). Nur fuer Clients aus dem AP-Subnetz;
        LAN, Tailscale und Tests sehen weiter nur 204.
        """
        host = request.headers.get("host", "")
        path = request.url.path
        client_ip = request.client.host if request.client else ""
        if is_captive_probe(host, path):
            if captive_prompt_due(client_ip):
                return Response(
                    status_code=302, headers={"Location": CAPTIVE_UI_URL, "Cache-Control": "no-store"},
                )
            # Known probe — let the specific handlers above answer.
            response = await call_next(request)
            if response.status_code == 404:
                # Generic fallback: 204 keeps the device happy
                return Response(status_code=204)
            return response
        if (
            request.method == "GET"
            and _is_ap_client(client_ip)
            and _is_foreign_host(host)
        ):
            # dnsmasq loest JEDEN Namen auf uns auf; ein "http://irgendwas"
            # aus dem AP-Subnetz landet hier mit fremdem Host — auf die UI.
            return Response(
                status_code=302, headers={"Location": CAPTIVE_UI_URL, "Cache-Control": "no-store"},
            )
        return await call_next(request)
