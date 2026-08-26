from __future__ import annotations

import argparse
import json
import os
import re
import types
import typing
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import MISSING, dataclass, field, fields
from pathlib import Path
from typing import Any

TRUTHY = frozenset({"1", "true", "yes", "on"})
FALSEY = frozenset({"0", "false", "no", "off", ""})

ENV_PREFIX = "ff_"
ACCESS_TOKEN_ENV_PREFIX = "ff_access_token"

# where a checkout keeps its state, and has since long before there was a choice
LEGACY_STATE_DIR = Path("artifacts")


class ConfigError(Exception):
    """Raised when the supplied configuration cannot be used"""


def opt(default: Any = MISSING, *, help: str, default_factory: Any = MISSING, **extra: Any) -> Any:
    """Declare a configuration option.

    Everything the command line parser needs is derived from the field itself,
    so an option is described in exactly one place. `extra` carries the few
    settings that cannot be inferred, such as a flag that differs from the field
    name, or argparse actions.
    """
    metadata = {"help": help, **extra}
    if default_factory is not MISSING:
        return field(default_factory=default_factory, metadata=metadata)
    if default is MISSING:
        return field(metadata=metadata)
    return field(default=default, metadata=metadata)


def _default_state_dir() -> Path:
    """Where state goes when nobody says otherwise.

    Every checkout has an artifacts/ directory, because artifacts/blank is
    committed, so this keeps the clone-and-cron installs that FediFetcher is
    usually deployed as writing exactly where they always have. An installed
    FediFetcher, run from wherever the user happens to be, gets a home of its
    own instead of scattering state across working directories.

    The build_parser docstring explains why this is never evaluated for --help:
    the parser defaults every option to None, so this runs only when a Config is
    actually constructed, by which point the working directory is the real one.
    """
    if LEGACY_STATE_DIR.is_dir():
        return LEGACY_STATE_DIR
    base = os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state"
    return Path(base) / "fedifetcher"


@dataclass(frozen=True, slots=True)
class Config:
    server: str = opt(
        help="Required: The name of your server (e.g. `mstdn.thms.uk`)")
    access_tokens: tuple[str, ...] = opt(
        flag="--access-token", action="append", metavar="ACCESS_TOKEN",
        help="Required: The access token can be generated at https://<server>/settings/applications, and must have read:search, read:statuses and admin:read:accounts scopes. You can supply this multiple times, if you want tun run it for multiple users.")

    reply_interval_in_hours: int = opt(0,
        help="Fetch remote replies to posts that have received replies from users on your own instance in this period")
    home_timeline_length: int = opt(0,
        help="Look for replies to posts in the API-Key owner's home timeline, up to this many posts")
    user: str = opt("",
        help="Optional. Use together with --max-followings or --max-followers to backfill a specific user's followings/followers. If omitted, we use the account that owns the access token.")
    max_followings: int = opt(0,
        help="Backfill posts for new accounts followed by --user. We'll backfill at most this many followings' posts")
    max_followers: int = opt(0,
        help="Backfill posts for new accounts following --user. We'll backfill at most this many followers' posts")
    max_follow_requests: int = opt(0,
        help="Backfill posts of the API key owners pending follow requests. We'll backfill at most this many requester's posts")
    max_bookmarks: int = opt(0,
        help="Fetch remote replies to the API key owners Bookmarks. We'll fetch replies to at most this many bookmarks")
    max_favourites: int = opt(0,
        help="Fetch remote replies to the API key owners Favourites. We'll fetch replies to at most this many favourites")
    from_notifications: int = opt(0,
        help="Backfill accounts of anyone appearing in your notifications, during the last hours")
    from_lists: bool = opt(False,
        help="Set to `1` to fetch missing replies and/or backfill account from your lists. This is disabled by default.")
    max_list_length: int = opt(100,
        help="Determines how many posts we'll fetch replies for in each list. This will be ignored, unless you also provide `from-lists = 1`. Set to `0` if you only want to backfill profiles in lists.")
    max_list_accounts: int = opt(10,
        help="Determines how many accounts we'll backfill for in each list. This will be ignored, unless you also provide `from-lists = 1`. Set to `0` if you only want to fetch replies in lists.")

    max_posts_per_account: int = opt(40,
        help="How many posts to fetch from each account we backfill. Anything above the 40 we fetch by default means more requests to the remote server, and more requests to your own server to pull each post in, so raising this will make FediFetcher considerably slower.")
    backfill_with_context: bool = opt(True,
        help="If enabled, we'll fetch remote replies when backfilling profiles. Set to `0` to disable.")
    backfill_mentioned_users: bool = opt(True,
        help="If enabled, we'll backfill any mentioned users when fetching remote replies to timeline posts. Set to `0` to disable.")
    remember_users_for_hours: int = opt(24 * 7,
        help="How long to remember users that you aren't following for, before trying to backfill them again.")
    remember_hosts_for_days: int = opt(30,
        help="How long to remember host info for, before checking again.")
    http_timeout: int = opt(5,
        help="The timeout for any HTTP requests to your own, or other instances.")
    instance_blocklist: tuple[str, ...] = opt((),
        help="A comma-separated array of instances that FediFetcher should never try to connect to")

    state_dir: Path = opt(default_factory=_default_state_dir,
        help="Directory to store persistent files and possibly lock file. Defaults to ./artifacts if that directory exists, and to $XDG_STATE_HOME/fedifetcher (usually ~/.local/state/fedifetcher) otherwise.")
    lock_file: Path | None = opt(None,
        help="Location of the lock file")
    lock_hours: int = opt(24,
        help="The lock timeout in hours.")
    on_start: str | None = opt(None,
        help="Provide a url that will be pinged when processing is starting. You can use this for 'dead man switch' monitoring of your task")
    on_done: str | None = opt(None,
        help="Provide a url that will be pinged when processing has completed. You can use this for 'dead man switch' monitoring of your task")
    on_fail: str | None = opt(None,
        help="Provide a url that will be pinged when processing has failed. You can use this for 'dead man switch' monitoring of your task")
    log_level: str = opt("DEBUG",
        help="Severity of events to log (DEBUG|INFO|WARNING|ERROR|CRITICAL)")
    log_format: str = opt("%(asctime)s: %(message)s",
        help="Specify the log format")

    def __post_init__(self) -> None:
        # in case someone provided the server name as url instead,
        object.__setattr__(
            self, "server", re.sub(r"^(https://)?([^/]*)/?$", "\\2", self.server)
        )

    @property
    def lock_path(self) -> Path:
        return self.lock_file or self.state_dir / "lock.lock"

    @property
    def seen_urls_file(self) -> Path:
        return self.state_dir / "seen_urls"

    @property
    def replied_toot_server_ids_file(self) -> Path:
        return self.state_dir / "replied_toot_server_ids"

    @property
    def known_followings_file(self) -> Path:
        return self.state_dir / "known_followings"

    @property
    def recently_checked_users_file(self) -> Path:
        return self.state_dir / "recently_checked_users"

    @property
    def seen_hosts_file(self) -> Path:
        return self.state_dir / "seen_hosts"

    @property
    def recently_checked_contexts_file(self) -> Path:
        return self.state_dir / "recent_context"

    @classmethod
    def load(
        cls,
        argv: Sequence[str] | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> Config:
        """Build a Config from the command line, environment and config file.

        Later sources win: defaults, then the config file, then the environment,
        then whatever was given explicitly on the command line.
        """
        environ = os.environ if environ is None else environ
        args = build_parser().parse_args(argv)

        values: dict[str, object] = {}
        if args.config is not None:
            values.update(_from_config_file(args.config))
        values.update(_from_environ(environ))
        # argparse defaults every option to None, so anything set here was asked for
        values.update({k: v for k, v in vars(args).items() if v is not None})
        values.pop("config", None)

        if values.get("server") is None or not values.get("access_tokens"):
            raise ConfigError("You must supply at least a server name and an access token")

        return cls(**_coerce_all(values))  # type: ignore[arg-type]


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser from the Config fields.

    Every option defaults to None rather than to its real default, so that the
    loader can tell an option that was actually given from one that was omitted.
    Values stay as text here and are converted by _coerce, so the command line,
    the environment and the config file are all read the same way.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '-c', '--config', required=False, default=None,
        help='Optionally provide a path to a JSON file containing configuration options. If not provided, options must be supplied using command line flags.',
    )

    for f in fields(Config):
        extra = dict(f.metadata)
        flag = extra.pop("flag", f"--{f.name.replace('_', '-')}")
        parser.add_argument(flag, dest=f.name, required=False, default=None, **extra)

    return parser


def _from_config_file(path: str) -> dict[str, object]:
    if not os.path.exists(path):
        raise ConfigError(f"Config file {path} doesn't exist")

    with open(path, encoding="utf-8") as f:
        raw = json.load(f)

    values = {key.lower().replace("-", "_"): value for key, value in raw.items()}
    # the file has always spelled this option in the singular
    if "access_token" in values:
        values["access_tokens"] = values.pop("access_token")
    return values


def _from_environ(environ: Mapping[str, str]) -> dict[str, object]:
    values: dict[str, object] = {}
    known = {f.name for f in fields(Config)}

    for name, value in environ.items():
        name = name.lower()
        if name.startswith(ENV_PREFIX) and not name.startswith(ACCESS_TOKEN_ENV_PREFIX):
            option = name[len(ENV_PREFIX):]
            if option in known:
                values[option] = value

    # remains special-cased for specifying multiple tokens
    tokens = [
        token
        for name, token in environ.items()
        if name.lower().startswith(ACCESS_TOKEN_ENV_PREFIX)
    ]
    if tokens:
        values["access_tokens"] = tokens

    return values


def _coerce_all(values: Mapping[str, object]) -> dict[str, object]:
    hints = typing.get_type_hints(Config)
    coerced = {}
    for name, value in values.items():
        if name not in hints:
            continue
        try:
            coerced[name] = _coerce(hints[name], value)
        except (TypeError, ValueError) as ex:
            raise ConfigError(f"Cannot read option {name}: {ex}") from ex
    return coerced


def _coerce(annotation: object, value: object) -> object:
    """Convert a value from a config file, the environment or the command line.

    The target type is taken from the Config field itself, so adding an option
    never means remembering to register how it should be parsed.
    """
    if typing.get_origin(annotation) in (types.UnionType, typing.Union):
        allowed = [a for a in typing.get_args(annotation) if a is not type(None)]
        if value is None:
            return None
        annotation = allowed[0]

    if typing.get_origin(annotation) is tuple:
        if isinstance(value, str):
            return tuple(part.strip() for part in value.split(",") if part.strip())
        if isinstance(value, Iterable):
            return tuple(value)
        raise TypeError(f"{value!r} is not a list of values")

    if annotation is bool:
        return _coerce_bool(value)
    if annotation is int:
        return int(value)  # type: ignore[call-overload]
    if annotation is Path:
        return Path(value)  # type: ignore[arg-type]
    return str(value)


def _coerce_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    text = str(value).strip().lower()
    if text in TRUTHY:
        return True
    if text in FALSEY:
        return False
    raise ValueError(f"{value!r} is not a yes/no value")
