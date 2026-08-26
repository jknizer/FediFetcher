from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from dateutil import parser

from fedifetcher.state import ContextCache, ServerCache, TimestampedSet

if TYPE_CHECKING:
    from fedifetcher.config import Config
    from fedifetcher.urls import PostRef

logger = logging.getLogger("FediFetcher")

# the format the state files are written in; a directory without a version file
# predates versioning and is read as version 1
STATE_VERSION = 1

# how many entries of the append-only collections to carry over between runs
MAX_ENTRIES = 100_000

CONTEXT_MAX_AGE = timedelta(days=7)
ROBOTS_MAX_AGE = timedelta(days=1)
SERVER_FAILURE_MAX_AGE = timedelta(hours=1)


class LockedError(Exception):
    """Raised when another run holds the lock, or its age cannot be read"""


@dataclass(slots=True)
class State:
    """Everything FediFetcher remembers between runs, plus this run's scratch space"""

    seen_urls: TimestampedSet = field(default_factory=TimestampedSet)
    known_followings: TimestampedSet = field(default_factory=TimestampedSet)
    recently_checked_users: TimestampedSet = field(default_factory=TimestampedSet)
    seen_hosts: ServerCache = field(default_factory=ServerCache)
    recently_checked_context: ContextCache = field(default_factory=ContextCache)
    replied_toot_server_ids: dict[str, Any] = field(default_factory=dict)

    # rebuilt on load, and not written back out
    all_known_users: TimestampedSet = field(default_factory=TimestampedSet)
    parsed_urls: dict[str, PostRef | None] = field(default_factory=dict)


class StateStore:
    """Reads and writes the state directory"""

    def __init__(self, config: Config) -> None:
        self._config = config

    @property
    def version_file(self) -> Path:
        return self._config.state_dir / "state_version"

    def stored_version(self) -> int:
        try:
            return int(self.version_file.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return 1

    def load(self) -> State:
        config = self._config
        version = self.stored_version()
        if version > STATE_VERSION:
            logger.warning(
                f"State directory is version {version}, but this FediFetcher "
                f"understands version {STATE_VERSION}. Reading it anyway."
            )

        state = State(
            seen_urls=TimestampedSet(_read_lines(config.seen_urls_file)),
            known_followings=TimestampedSet(_read_lines(config.known_followings_file)),
            recently_checked_users=TimestampedSet(
                _read_json(config.recently_checked_users_file) or {}
            ),
            seen_hosts=ServerCache(_read_json(config.seen_hosts_file) or {}),
            recently_checked_context=ContextCache(
                _read_json(config.recently_checked_contexts_file) or {}
            ),
            replied_toot_server_ids=_read_json(config.replied_toot_server_ids_file) or {},
        )

        state.recently_checked_users.expire_older_than(
            timedelta(hours=config.remember_users_for_hours)
        )
        state.seen_hosts.expire(
            max_age=timedelta(days=config.remember_hosts_for_days),
            failure_max_age=SERVER_FAILURE_MAX_AGE,
        )
        state.recently_checked_context.expire_older_than(CONTEXT_MAX_AGE)

        state.all_known_users = TimestampedSet(
            list(state.known_followings) + list(state.recently_checked_users)
        )
        return state

    def save(self, state: State) -> None:
        config = self._config
        config.state_dir.mkdir(parents=True, exist_ok=True)

        _write(config.known_followings_file, "\n".join(list(state.known_followings)[-MAX_ENTRIES:]))
        _write(config.seen_urls_file, "\n".join(list(state.seen_urls)[-MAX_ENTRIES:]))
        _write(
            config.replied_toot_server_ids_file,
            json.dumps(dict(list(state.replied_toot_server_ids.items())[-MAX_ENTRIES:])),
        )
        _write(config.recently_checked_users_file, state.recently_checked_users.toJSON())
        _write(config.seen_hosts_file, state.seen_hosts.toJSON())
        _write(config.recently_checked_contexts_file, state.recently_checked_context.toJSON())
        _write(self.version_file, str(STATE_VERSION))

    @contextmanager
    def session(self) -> Iterator[State]:
        """Load state, and write it back even if the run fails part way through"""
        state = self.load()
        try:
            yield state
        finally:
            self.save(state)


@contextmanager
def lock(config: Config) -> Iterator[None]:
    """Hold the lock file for the duration, so two runs cannot overlap"""
    path = config.lock_path

    if path.exists():
        logger.debug(f"Lock file exists at {path}")
        try:
            lock_time = parser.parse(path.read_text(encoding="utf-8"))
        except Exception as ex:
            raise LockedError("Cannot read logfile age - aborting.") from ex

        age = datetime.now() - lock_time
        if age.total_seconds() < config.lock_hours * 60 * 60:
            raise LockedError(
                f"Lock file age is {age} - below --lock-hours={config.lock_hours} provided."
            )

        path.unlink()
        logger.debug("Lock file has expired. Removed lock file.")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{datetime.now()}", encoding="utf-8")
    try:
        yield
    finally:
        path.unlink(missing_ok=True)


def _read_lines(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
