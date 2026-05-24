"""Building blocks for online integrations.

Every external service (QRZ, HamQTH, PSK Reporter, hamqsl, Blitzortung,
ntfy, …) follows the same pattern — defined in ``architecture.md`` §6.6:

* Aggressive timeouts (≤ 5 s)
* TTL cache so repeat hits are cheap
* Circuit breaker so a flaky upstream doesn't degrade the controller
* Failures degrade gracefully — returning ``None`` or a stale cache,
  never raising into the state machine

This module provides :class:`AsyncTTLCache` and :class:`CircuitBreaker`,
plus an :class:`Integration` base class that bundles them with an
``httpx.AsyncClient`` and standard health reporting.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

import httpx

log = logging.getLogger(__name__)

T = TypeVar("T")


# ---------------------------------------------------------------------------
@dataclass
class AsyncTTLCache(Generic[T]):
    """Tiny coroutine-safe TTL cache keyed by string."""

    ttl_s: float
    _store: dict[str, tuple[float, T]] = field(default_factory=dict)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def get(self, key: str) -> T | None:
        """Return *fresh* value if any. Stale entries are kept on disk so
        :meth:`get_stale_ok` can still hand them back during upstream
        outages — important for graceful-degrade."""
        async with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            ts, value = entry
            if time.monotonic() - ts > self.ttl_s:
                return None
            return value

    async def set(self, key: str, value: T) -> None:
        async with self._lock:
            self._store[key] = (time.monotonic(), value)

    async def get_stale_ok(self, key: str) -> tuple[T | None, bool]:
        """Like :meth:`get` but also returns stale entries with ``stale=True``."""
        async with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None, False
            ts, value = entry
            stale = (time.monotonic() - ts) > self.ttl_s
            return value, stale


# ---------------------------------------------------------------------------
class CircuitOpenError(RuntimeError):
    """Raised by :class:`CircuitBreaker.run` when the circuit is open."""


@dataclass
class CircuitBreaker:
    """Trip after *failure_threshold* failures within *cool_off_s* seconds.

    While open, calls raise immediately. After *cool_off_s* the breaker
    moves to half-open: one trial call is allowed; success closes the
    breaker, failure re-opens it.
    """

    failure_threshold: int = 3
    cool_off_s: float = 60.0
    state: str = "closed"  # closed | open | half_open
    _failures: int = 0
    _opened_at: float = 0.0
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def __post_init__(self) -> None:
        # dataclass treats Lock as default value — re-init per instance
        self._lock = asyncio.Lock()

    async def run(self, fn: Callable[[], Awaitable[T]]) -> T:
        async with self._lock:
            now = time.monotonic()
            if self.state == "open":
                if now - self._opened_at >= self.cool_off_s:
                    self.state = "half_open"
                else:
                    raise CircuitOpenError("circuit open")

        try:
            result = await fn()
        except Exception:
            async with self._lock:
                self._failures += 1
                if self._failures >= self.failure_threshold:
                    self.state = "open"
                    self._opened_at = time.monotonic()
            raise

        async with self._lock:
            self._failures = 0
            self.state = "closed"
        return result


# ---------------------------------------------------------------------------
@dataclass
class IntegrationHealth:
    """Snapshot exposed via the /api/healthcheck endpoint."""

    name: str
    enabled: bool
    circuit_state: str
    last_ok: float | None = None
    last_error: str | None = None


class Integration:
    """Common base for HTTP-driven integrations.

    Subclasses override :attr:`name`, set ``base_url`` / ``timeout`` and
    implement domain-specific methods that call :meth:`_get` /
    :meth:`_post`.
    """

    name: str = "integration"

    def __init__(
        self,
        *,
        enabled: bool = True,
        base_url: str | None = None,
        timeout: float = 5.0,
        cache_ttl_s: float = 60.0,
    ) -> None:
        self.enabled = enabled
        self.base_url = base_url
        self.timeout = timeout
        self.cache: AsyncTTLCache[Any] = AsyncTTLCache(ttl_s=cache_ttl_s)
        self.breaker = CircuitBreaker()
        self._last_ok: float | None = None
        self._last_error: str | None = None
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> Integration:
        self._client = httpx.AsyncClient(base_url=self.base_url or "", timeout=self.timeout)
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(base_url=self.base_url or "", timeout=self.timeout)
        return self._client

    async def _get(self, url: str, **kwargs: Any) -> httpx.Response:
        async def _do() -> httpx.Response:
            r = await self.client.get(url, **kwargs)
            r.raise_for_status()
            return r

        try:
            r = await self.breaker.run(_do)
            self._last_ok = time.time()
            return r
        except Exception as exc:
            self._last_error = repr(exc)
            log.warning("%s: GET %s failed: %s", self.name, url, exc)
            raise

    async def _post(self, url: str, **kwargs: Any) -> httpx.Response:
        async def _do() -> httpx.Response:
            r = await self.client.post(url, **kwargs)
            r.raise_for_status()
            return r

        try:
            r = await self.breaker.run(_do)
            self._last_ok = time.time()
            return r
        except Exception as exc:
            self._last_error = repr(exc)
            log.warning("%s: POST %s failed: %s", self.name, url, exc)
            raise

    def health(self) -> IntegrationHealth:
        return IntegrationHealth(
            name=self.name,
            enabled=self.enabled,
            circuit_state=self.breaker.state,
            last_ok=self._last_ok,
            last_error=self._last_error,
        )
