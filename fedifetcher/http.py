from __future__ import annotations

import logging
import time
import urllib.robotparser
from collections.abc import Mapping
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import requests
import xxhash
from dateutil import parser

from fedifetcher import VERSION
from fedifetcher.config import Config

logger = logging.getLogger("FediFetcher")

ROBOTS_CACHE_PREFIX = "robots-"


class BlockedByRobotsError(Exception):
    """Raised when robots.txt or the configured blocklist forbids a request"""


class RateLimitError(Exception):
    """Raised when a rate limited request ran out of retries"""


class RobotsCache:
    """Remembers each host's robots.txt, in memory and on disk.

    An entry is either the text of a robots.txt, or a verdict reached without
    one: False when the host refused to serve it, True when it could not be
    fetched at all and we go ahead regardless.
    """

    def __init__(self, state_dir: Path, blocklist: tuple[str, ...]) -> None:
        self._state_dir = state_dir
        self._blocklist = blocklist
        self._cache: dict[str, str | bool] = {}

    def cache_path(self, robots_url: str) -> Path:
        digest = xxhash.xxh128(robots_url.encode('utf-8')).hexdigest()
        return self._state_dir / f'{ROBOTS_CACHE_PREFIX}{digest}.txt'

    def cached(self, robots_url: str) -> str | bool | None:
        if robots_url in self._cache:
            return self._cache[robots_url]

        path = self.cache_path(robots_url)
        if path.exists():
            logger.debug(f"Getting robots.txt file from cache for {robots_url}.")
            text = path.read_text(encoding="utf-8")
            self._cache[robots_url] = text
            return text

        return None

    def fetch(self, robots_url: str, fetcher: HttpClient) -> str | bool:
        cached = self.cached(robots_url)
        if cached is not None:
            return cached

        robots: str | bool
        try:
            # We are getting the robots.txt manually from here, because otherwise we can't change the User Agent
            response = fetcher.get(robots_url, timeout=2, ignore_robots_txt=True)
            if response.status_code in (401, 403):
                robots = False
            else:
                robots = response.text
                self.cache_path(robots_url).write_text(robots, encoding="utf-8")
        except Exception:
            robots = True

        self._cache[robots_url] = robots
        return robots

    def can_fetch(self, user_agent: str, url: str, fetcher: HttpClient) -> bool:
        parsed_uri = urlparse(url)
        robots_url = f'{parsed_uri.scheme}://{parsed_uri.netloc}/robots.txt'

        if parsed_uri.netloc in self._blocklist:
            # Never connect to these locations
            raise BlockedByRobotsError(
                f"Connecting to {parsed_uri.netloc} is prohibited by the configured blocklist"
            )

        robots = self.fetch(robots_url, fetcher)
        if isinstance(robots, bool):
            return robots

        robot_parser = urllib.robotparser.RobotFileParser()
        robot_parser.parse(robots.splitlines())
        return robot_parser.can_fetch(user_agent, url)

    def discard_stale_files(self, max_age: timedelta) -> int:
        """Delete cached robots.txt files old enough to be worth re-fetching"""
        if not self._state_dir.is_dir():
            return 0

        discarded = 0
        cutoff = time.time() - max_age.total_seconds()
        for path in self._state_dir.iterdir():
            if path.name.startswith(ROBOTS_CACHE_PREFIX) and path.is_file():
                if path.stat().st_mtime < cutoff:
                    logger.debug(f"Removing cached robots.txt file {path.name}")
                    path.unlink()
                    discarded += 1
        return discarded


class HttpClient:
    """Makes requests on FediFetcher's behalf, respecting robots.txt and rate limits"""

    def __init__(
        self,
        config: Config,
        session: requests.Session | None = None,
        robots: RobotsCache | None = None,
    ) -> None:
        self._config = config
        # a session keeps connections alive between the many requests we make
        # to the same handful of hosts
        self._session = session if session is not None else requests.Session()
        self.robots = (
            robots
            if robots is not None
            else RobotsCache(config.state_dir, config.instance_blocklist)
        )

    @property
    def user_agent(self) -> str:
        return f"FediFetcher/{VERSION}; +{self._config.server} (https://go.thms.uk/ff)"

    def get(
        self,
        url: str,
        headers: Mapping[str, str] | None = None,
        timeout: int | None = None,
        max_tries: int = 5,
        backoff: float = 0.5,
        ignore_robots_txt: bool = False,
    ) -> requests.Response:
        """Make a get request while providing our user agent, and respecting rate limits"""
        return self._request(
            "GET", url, headers=headers, timeout=timeout, max_tries=max_tries,
            backoff=backoff, ignore_robots_txt=ignore_robots_txt,
        )

    def post(
        self,
        url: str,
        json: Any,
        headers: Mapping[str, str] | None = None,
        timeout: int | None = None,
        max_tries: int = 5,
        backoff: float = 0.5,
    ) -> requests.Response:
        """Make a post request while providing our user agent, and respecting rate limits"""
        return self._request(
            "POST", url, json=json, headers=headers, timeout=timeout,
            max_tries=max_tries, backoff=backoff,
        )

    def get_redirect_url(self, url: str) -> str | None:
        """get the URL given URL redirects to"""
        try:
            resp = self._request("HEAD", url, allow_redirects=False)
        except Exception as ex:
            logger.error(f"Error getting redirect URL for URL {url}. Exception: {ex}")
            return None

        if resp.status_code == 200:
            return url
        elif resp.status_code == 302:
            redirect_url = resp.headers["Location"]
            logger.debug(f"Discovered redirect for URL {url}")
            return redirect_url
        else:
            logger.error(
                f"Error getting redirect URL for URL {url}. Status code: {resp.status_code}"
            )
            return None

    def _request(
        self,
        method: str,
        url: str,
        json: Any = None,
        headers: Mapping[str, str] | None = None,
        timeout: int | None = None,
        max_tries: int = 5,
        backoff: float = 0.5,
        ignore_robots_txt: bool = False,
        allow_redirects: bool = True,
    ) -> requests.Response:
        h = dict(headers or {})
        h.setdefault('User-Agent', self.user_agent)

        if not ignore_robots_txt and not self.robots.can_fetch(h['User-Agent'], url, self):
            raise BlockedByRobotsError(f"Querying {url} prohibited by robots.txt")

        if timeout is None:
            timeout = self._config.http_timeout

        while True:
            response = self._session.request(
                method, url, json=json, headers=h, timeout=timeout,
                allow_redirects=allow_redirects,
            )
            if response.status_code != 429:
                return response

            if max_tries <= 0:
                raise RateLimitError(
                    f"Maximum number of retries exceeded for rate limited request {url}"
                )

            time.sleep(self._retry_delay(response, url, backoff))
            max_tries -= 1
            backoff *= 4

    def _retry_delay(self, response: requests.Response, url: str, backoff: float) -> float:
        now = datetime.now(datetime.now().astimezone().tzinfo)
        if 'x-ratelimit-reset' in response.headers:
            reset = parser.parse(response.headers['x-ratelimit-reset'])
            wait = (reset - now).total_seconds() + 1
        else:
            wait = backoff
            reset = now + timedelta(seconds=wait)
        logger.warning(f"Rate Limit hit requesting {url}. Waiting {wait} sec to retry at {reset}")
        return wait


def build_callback_url(url: str, params: Mapping[str, object]) -> str:
    """Add query parameters to a callback URL, replacing any that already exist."""
    parsed = urlparse(url)
    query = parse_qs(parsed.query, keep_blank_values=True)
    for key, value in params.items():
        query[key] = [str(value)]
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))


__all__ = [
    "BlockedByRobotsError",
    "HttpClient",
    "RateLimitError",
    "RobotsCache",
    "build_callback_url",
]
