from __future__ import annotations

import logging
import sys
import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta

from fedifetcher import VERSION
from fedifetcher.api.mastodon import HomeServer
from fedifetcher.config import Config, ConfigError
from fedifetcher.http import HttpClient, build_callback_url
from fedifetcher.store import ROBOTS_MAX_AGE, LockedError, StateStore, lock
from fedifetcher.tasks import Context, run_enabled_tasks

logger = logging.getLogger("FediFetcher")


def setup_logging(config: Config) -> None:
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.basicConfig(
        format=f"{config.log_format}",
        datefmt="%Y-%m-%d %H:%M:%S %Z",
        level=config.log_level.upper(),
    )
    logger.setLevel(config.log_level.upper())


class Notifier:
    """Pings the configured callback urls, for dead man's switch monitoring"""

    def __init__(self, config: Config, http: HttpClient, run_id: uuid.UUID) -> None:
        self._config = config
        self._http = http
        self._run_id = run_id
        self._started = datetime.now()

    @property
    def elapsed(self) -> timedelta:
        return datetime.now() - self._started

    def _ping(self, url: str | None, **params: object) -> None:
        if not url:
            return
        try:
            self._http.get(
                build_callback_url(url, {"rid": self._run_id, **params}),
                ignore_robots_txt=True,
            )
        except Exception as ex:
            logger.error(f"Error getting callback url: {ex}")

    def starting(self) -> None:
        self._ping(self._config.on_start)

    def done(self, message: str) -> None:
        self._ping(self._config.on_done, ping=self._elapsed_ms(), msg=message)

    def failed(self, message: str) -> None:
        self._ping(self._config.on_fail, ping=self._elapsed_ms(), msg=message)

    def _elapsed_ms(self) -> int:
        return int(self.elapsed.total_seconds() * 1000)


def main(argv: Sequence[str] | None = None) -> int:
    try:
        config = Config.load(argv)
    except ConfigError as ex:
        logging.basicConfig()
        logger.critical(str(ex))
        return 1

    setup_logging(config)
    logger.info(f"Starting FediFetcher v{VERSION}")

    http = HttpClient(config)
    notifier = Notifier(config, http, uuid.uuid4())
    store = StateStore(config)

    notifier.starting()

    try:
        with lock(config), store.session() as state:
            # Delete any old robots.txt files so we can re-download them
            http.robots.discard_stale_files(ROBOTS_MAX_AGE)

            for token in config.access_tokens:
                run_enabled_tasks(
                    Context(
                        config=config,
                        state=state,
                        http=http,
                        home=HomeServer(config.server, token, http),
                    )
                )

    except LockedError as ex:
        logger.critical(str(ex))
        notifier.failed(str(ex))
        return 1

    except Exception as ex:
        logger.error(f"Job failed after {notifier.elapsed}.")
        notifier.failed(str(ex))
        raise

    message = f"Processing finished in {notifier.elapsed}."
    notifier.done(message)
    logger.info(message)
    return 0


if __name__ == "__main__":
    sys.exit(main())
