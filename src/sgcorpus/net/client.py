"""The one HTTP client. Rate-limited, retrying, robots-aware.

Every adapter goes through this. The politeness rules in docs/legal-posture.md
are enforced here rather than trusted to each adapter, because four adapters
means four places to get it wrong.
"""

from __future__ import annotations

import logging
import time
import urllib.robotparser
from typing import Any
from urllib.parse import urlparse

import httpx

from ..config import SourcePolicy

log = logging.getLogger(__name__)


class RateLimitExceeded(RuntimeError):
    """The daily cap for a source was reached. Checkpoint and stop."""


class RobotsDisallowed(RuntimeError):
    """robots.txt disallows this path. Skipped with a logged reason."""


class Client:
    """A single-connection, rate-limited HTTP client for one source.

    There is deliberately no concurrency and no way to opt out of the delay.
    """

    def __init__(
        self,
        policy: SourcePolicy,
        *,
        timeout: float = 30.0,
        max_retries: int = 4,
    ) -> None:
        self.policy = policy
        self.max_retries = max_retries
        self._last_request: float = 0.0
        self._request_count = 0
        self._robots: urllib.robotparser.RobotFileParser | None = None
        self._http = httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": policy.user_agent, "Accept-Language": "en-SG,en;q=0.9"},
        )

    # -- lifecycle ---------------------------------------------------------

    def __enter__(self) -> Client:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    # -- politeness --------------------------------------------------------

    def _wait(self) -> None:
        elapsed = time.monotonic() - self._last_request
        if elapsed < self.policy.min_interval:
            time.sleep(self.policy.min_interval - elapsed)
        self._last_request = time.monotonic()

    def _check_cap(self) -> None:
        cap = self.policy.daily_cap
        if cap is not None and self._request_count >= cap:
            raise RateLimitExceeded(
                f"{self.policy.host}: daily cap of {cap} requests reached; "
                "checkpoint written, resume tomorrow"
            )

    def _check_robots(self, url: str) -> None:
        if not self.policy.respect_robots:
            return
        if self._robots is None:
            parsed = urlparse(url)
            self._robots = urllib.robotparser.RobotFileParser()
            robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
            try:
                self._check_cap()
                self._wait()
                self._request_count += 1
                response = self._http.get(robots_url, timeout=10.0)
                self._robots.parse(response.text.splitlines())
            except httpx.HTTPError:
                # An unreachable robots.txt is not permission. Fetch nothing
                # we would not fetch with an empty allow-list, but do not
                # treat a network blip as a site-wide disallow either.
                log.warning("could not fetch %s; proceeding at policy rate", robots_url)
                self._robots.parse([])
        delay = self._robots.crawl_delay(self.policy.user_agent)
        if delay is not None:
            self.policy = self.policy.merge(float(delay))
        if not self._robots.can_fetch(self.policy.user_agent, url):
            raise RobotsDisallowed(url)

    # -- requests ----------------------------------------------------------

    def get(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        accept_status: tuple[int, ...] = (200,),
    ) -> httpx.Response | None:
        """GET with backoff. Returns None when the source says "nothing here".

        ``accept_status`` lets an adapter declare which non-200 codes are
        meaningful rather than exceptional -- Hansard answers HTTP 500 for a
        non-sitting date, which is data, not an error.
        """
        self._check_robots(url)

        backoff = 2.0
        for attempt in range(self.max_retries):
            self._check_cap()
            self._wait()
            self._request_count += 1

            try:
                response = self._http.get(url, params=params)
            except httpx.HTTPError as exc:
                log.warning("%s: request failed (%d/%d): %s", url, attempt + 1, self.max_retries, exc)
                time.sleep(backoff * (2**attempt))
                continue

            if response.status_code in accept_status:
                return response

            if response.status_code == 429:
                wait = float(response.headers.get("Retry-After", backoff * (2**attempt)))
                log.warning("%s: rate limited, waiting %.0fs", url, wait)
                time.sleep(wait)
                continue

            if 500 <= response.status_code < 600:
                # Some sources use 5xx to mean "no record", so hand it back and
                # let the adapter decide.
                return response

            log.info("%s: HTTP %d", url, response.status_code)
            return response

        log.error("%s: giving up after %d attempts", url, self.max_retries)
        return None

    def post_json(
        self,
        url: str,
        payload: dict[str, Any],
        *,
        accept_status: tuple[int, ...] = (200,),
    ) -> httpx.Response | None:
        """POST a JSON body, under the same politeness rules as get().

        Several of these sources have migrated from query-string GETs to JSON
        POSTs as their front ends moved to SPAs; SPRS is the first.
        """
        self._check_robots(url)

        backoff = 2.0
        for attempt in range(self.max_retries):
            self._check_cap()
            self._wait()
            self._request_count += 1

            try:
                response = self._http.post(url, json=payload)
            except httpx.HTTPError as exc:
                log.warning("%s: request failed (%d/%d): %s", url, attempt + 1, self.max_retries, exc)
                time.sleep(backoff * (2**attempt))
                continue

            if response.status_code in accept_status:
                return response
            if response.status_code == 429:
                wait = float(response.headers.get("Retry-After", backoff * (2**attempt)))
                log.warning("%s: rate limited, waiting %.0fs", url, wait)
                time.sleep(wait)
                continue
            return response

        log.error("%s: giving up after %d attempts", url, self.max_retries)
        return None

    @property
    def request_count(self) -> int:
        return self._request_count
