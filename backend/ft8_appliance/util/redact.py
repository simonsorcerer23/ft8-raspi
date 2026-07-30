"""Secrets aus Freitext entfernen, bevor er geloggt oder gepusht wird.

Hintergrund (2026-07-30): QRZ und ClubLog erwarten ihre Credentials als
GET-Query-Parameter — bei ClubLog ``?api=<key>``, bei QRZ
``?username=..&password=..``. httpx schreibt bei ``raise_for_status()``
die VOLLSTAENDIGE URL inklusive Query-String in die Exception-Message.
Diese Message landete als Preflight-``detail`` in einer ntfy-Push, und
ntfy-Topics sind oeffentlich und unauthentifiziert. Ein falscher oder
abgelaufener Key (ClubLog antwortet dann mit HTTP 403) hat den Key also
genau im Fehlerfall veroeffentlicht.

Die eigentliche Abhilfe ist, solche Exceptions gar nicht erst
weiterzureichen. Diese Funktion ist die zweite Verteidigungslinie fuer
alles, was doch als Fremdtext (Server-Body, Exception-repr) durchkommt.
"""

from __future__ import annotations

import re

# Query-Parameter, deren Wert ein Geheimnis ist. Bewusst als Ganzwort
# verankert (\b...=), damit "api" nicht in "rapid=" trifft.
_SECRET_PARAMS = (
    "api",
    "api_key",
    "apikey",
    "key",
    "password",
    "passwd",
    "pw",
    "token",
    "secret",
)

_SECRET_RE = re.compile(
    r"\b(" + "|".join(_SECRET_PARAMS) + r")=([^&\s'\"<>]+)",
    re.IGNORECASE,
)


def redact_secrets(text: str) -> str:
    """Werte bekannter Secret-Query-Parameter durch ``***`` ersetzen.

    Der Parametername bleibt stehen — fuer die Fehlersuche ist relevant,
    *dass* ein Key mitgeschickt wurde, nicht welcher.
    """
    return _SECRET_RE.sub(r"\1=***", text)
