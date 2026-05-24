"""ntfy.sh push notifications.

Publish via JSON-Body am Root-Endpoint statt Header-only Format.
HTTP-Header dürfen nur Latin-1 — sobald wir Emojis im Titel hatten
(z.B. "🆕 New DXCC!") kippte httpx mit UnicodeEncodeError und die
Notification wurde NIE gesendet. JSON-Body trägt UTF-8 nativ.

ntfy.sh docs: https://docs.ntfy.sh/publish/#publish-as-json
"""

from __future__ import annotations

import json

from .base import Integration


# Priority-Mapping ntfy-Strings → JSON-Integer (1=min … 5=urgent).
_PRIO_MAP = {"min": 1, "low": 2, "default": 3, "high": 4, "urgent": 5}


def _parse_action(s: str) -> dict | None:
    """Parse einen ntfy-Action-String in das JSON-Action-Object.

    Eingangsformat (so wie wir's gebaut haben):
      "http, <label>, <url>, method=POST, headers.content-type=…, body=…"
    Ausgang:
      {"action": "http", "label": "...", "url": "...", "method": "POST",
       "headers": {...}, "body": "..."}
    """
    parts = [p.strip() for p in s.split(",")]
    if len(parts) < 3:
        return None
    action, label, url, *kw = parts
    obj: dict = {"action": action, "label": label, "url": url}
    headers: dict[str, str] = {}
    for token in kw:
        if "=" not in token:
            continue
        k, _, v = token.partition("=")
        k = k.strip()
        v = v.strip()
        if k.startswith("headers."):
            headers[k[len("headers."):]] = v
        else:
            obj[k] = v
    if headers:
        obj["headers"] = headers
    return obj


class NtfyClient(Integration):
    name = "ntfy"

    def __init__(
        self,
        topic: str | None,
        *,
        server: str = "https://ntfy.sh",
        enabled: bool = True,
        timeout: float = 3.0,
    ) -> None:
        super().__init__(
            enabled=enabled and bool(topic),
            base_url=server,
            timeout=timeout,
            cache_ttl_s=0.0,
        )
        self.topic = topic

    async def notify(
        self,
        message: str,
        *,
        title: str | None = None,
        priority: str | None = None,  # "min" | "low" | "default" | "high" | "urgent"
        tags: list[str] | None = None,
        actions: list[str] | None = None,
        flag: str = "",
    ) -> bool:
        """Push an ntfy notification.

        ``flag``: optionales Flag-Emoji (z.B. "🇩🇪") das dem Titel
        vorangestellt wird. Sebastian-Request v0.3.0 — Pushes die ein
        fremdes Callsign enthalten kriegen die Landesflagge davor. Der
        Caller berechnet das Flag via ``integrations.flags.flag_for_call``;
        wir hier nur das Rendering. Leerer String = kein Prepend.
        """
        if not self.enabled or not self.topic:
            return False
        body: dict = {"topic": self.topic, "message": message}
        if title:
            if flag:
                body["title"] = f"{flag} {title}"
            else:
                body["title"] = title
        elif flag:
            # Kein title → flag dem message-body voranstellen damit's
            # ueberhaupt sichtbar wird.
            body["message"] = f"{flag} {message}"
        if priority:
            body["priority"] = _PRIO_MAP.get(priority, 3)
        if tags:
            body["tags"] = list(tags)
        if actions:
            parsed = [a for a in (_parse_action(s) for s in actions[:3]) if a]
            if parsed:
                body["actions"] = parsed
        try:
            await self._post(
                "/",
                content=json.dumps(body).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
        except Exception:
            return False
        return True
