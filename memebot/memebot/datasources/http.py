"""Small shared HTTP layer: token-bucket rate limiting and bounded retries.

Public crypto APIs answer 429 aggressively. A bot that ignores that gets banned
mid-position, which is a risk event, not an inconvenience.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

import httpx

log = logging.getLogger(__name__)


class RateLimiter:
    """Token bucket. Thread-safe so several clients can share one limiter.

    The clock and sleeper are injected together at construction: reading the clock
    through one source while seeding `_updated` from another produces a negative
    elapsed time, an unsatisfiable token deficit, and a loop that never terminates.
    """

    def __init__(
        self,
        requests_per_minute: int,
        clock=time.monotonic,
        sleeper=time.sleep,
    ) -> None:
        if requests_per_minute <= 0:
            raise ValueError("requests_per_minute must be positive")
        self.capacity = float(requests_per_minute)
        self.refill_per_second = requests_per_minute / 60.0
        self._tokens = float(requests_per_minute)
        self._clock = clock
        self._sleep = sleeper
        self._updated = clock()
        self._lock = threading.Lock()

    def acquire(self) -> float:
        """Block until a token is available. Returns the seconds spent waiting."""
        waited = 0.0
        while True:
            with self._lock:
                now = self._clock()
                elapsed = max(0.0, now - self._updated)
                self._tokens = min(self.capacity, self._tokens + elapsed * self.refill_per_second)
                self._updated = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return waited
                delay = (1.0 - self._tokens) / self.refill_per_second
            self._sleep(delay)
            waited += delay


class HttpClient:
    """Thin wrapper over httpx with a rate limiter and retry/backoff on 429 and 5xx."""

    RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})

    def __init__(
        self,
        base_url: str,
        requests_per_minute: int = 60,
        timeout: float = 15.0,
        headers: dict[str, str] | None = None,
        max_retries: int = 3,
        client: httpx.Client | None = None,
        sleeper=time.sleep,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.limiter = RateLimiter(requests_per_minute, sleeper=sleeper)
        self.max_retries = max_retries
        self._sleep = sleeper
        self._client = client or httpx.Client(
            timeout=timeout,
            headers={"User-Agent": "memebot/1.0", **(headers or {})},
            follow_redirects=True,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "HttpClient":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def request_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any | None = None,
    ) -> Any | None:
        """Return parsed JSON, or None if the call ultimately failed.

        Callers must treat None as "unknown", never as "safe".
        """
        url = path if path.startswith("http") else f"{self.base_url}/{path.lstrip('/')}"
        backoff = 1.0
        for attempt in range(1, self.max_retries + 1):
            self.limiter.acquire()
            try:
                response = self._client.request(method, url, params=params, json=json_body)
            except httpx.HTTPError as exc:
                log.warning("%s %s failed (attempt %d/%d): %s", method, url, attempt, self.max_retries, exc)
            else:
                if response.status_code in self.RETRY_STATUSES:
                    retry_after = _retry_after_seconds(response) or backoff
                    log.warning(
                        "%s %s -> HTTP %d (attempt %d/%d), retrying in %.1fs",
                        method, url, response.status_code, attempt, self.max_retries, retry_after,
                    )
                    if attempt < self.max_retries:
                        self._sleep(retry_after)
                        backoff = min(backoff * 2, 30.0)
                    continue
                if response.status_code >= 400:
                    log.warning("%s %s -> HTTP %d (not retryable)", method, url, response.status_code)
                    return None
                try:
                    return response.json()
                except ValueError:
                    log.warning("%s %s returned non-JSON body", method, url)
                    return None
            if attempt < self.max_retries:
                self._sleep(backoff)
                backoff = min(backoff * 2, 30.0)
        return None

    def get_json(self, path: str, params: dict[str, Any] | None = None) -> Any | None:
        return self.request_json("GET", path, params=params)

    def post_json(self, path: str, json_body: Any) -> Any | None:
        return self.request_json("POST", path, json_body=json_body)


def _retry_after_seconds(response: httpx.Response) -> float | None:
    raw = response.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None
