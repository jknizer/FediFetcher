from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from datetime import datetime, timedelta
from typing import Any

from dateutil import parser


def _now() -> datetime:
    return datetime.now(datetime.now().astimezone().tzinfo)


class TimestampedSet:
    """An insertion-ordered set that remembers when each item was first added.

    Backed by a dict, whose keys are the members and whose values are the times
    they were added. Only the first add of an item counts: re-adding something
    keeps the original timestamp, so ages reflect first sight rather than last.
    """

    def __init__(self, iterable: Iterable[str] | dict[str, Any] = ()) -> None:
        self._dict: dict[str, datetime] = {}
        if isinstance(iterable, dict):
            for item, when in iterable.items():
                self.add(item, parser.parse(when) if isinstance(when, str) else when)
        else:
            for item in iterable:
                self.add(item)

    def add(self, item: str, when: datetime | None = None) -> None:
        if item not in self._dict:
            self._dict[item] = _now() if when is None else when

    def get(self, item: str) -> datetime:
        return self._dict[item]

    def update(self, iterable: Iterable[str]) -> None:
        for item in iterable:
            self.add(item)

    def expire_older_than(self, age: timedelta) -> int:
        """Drop everything added longer ago than `age`, and report how many went"""
        expired = [
            item
            for item, when in self._dict.items()
            if datetime.now(when.tzinfo) - when > age
        ]
        for item in expired:
            del self._dict[item]
        return len(expired)

    def __contains__(self, item: object) -> bool:
        return item in self._dict

    def __iter__(self) -> Iterator[str]:
        return iter(self._dict)

    def __len__(self) -> int:
        return len(self._dict)

    def toJSON(self) -> str:
        return json.dumps(self._dict, default=str)


class ServerCache:
    """What we know about each host we have looked up, keyed by hostname"""

    def __init__(self, iterable: dict[str, dict[str, Any]] | None = None) -> None:
        self._dict: dict[str, dict[str, Any]] = {}
        for key, item in (iterable or {}).items():
            if 'last_checked' in item and isinstance(item['last_checked'], str):
                item['last_checked'] = parser.parse(item['last_checked'])
            self.add(key, item)

    def add(self, key: str, item: dict[str, Any]) -> None:
        self._dict[key] = item

    def get(self, key: str) -> dict[str, Any]:
        return self._dict[key]

    def expire(self, max_age: timedelta, failure_max_age: timedelta) -> int:
        """Drop stale entries, and report how many went.

        Entries recorded before PeerTube support existed are dropped whatever
        their age, because they cannot say whether the host speaks that API.
        """
        dropped = []
        for host, info in self._dict.items():
            if 'peertubeApiSupport' not in info:
                dropped.append(host)
            elif 'last_checked' in info:
                last_checked = info['last_checked']
                age = datetime.now(last_checked.tzinfo) - last_checked
                if age > max_age:
                    dropped.append(host)
                elif 'info' in info and info['info'] is None and age > failure_max_age:
                    dropped.append(host)

        for host in dropped:
            del self._dict[host]
        return len(dropped)

    def __contains__(self, item: object) -> bool:
        return item in self._dict

    def __iter__(self) -> Iterator[str]:
        return iter(self._dict)

    def __len__(self) -> int:
        return len(self._dict)

    def toJSON(self) -> str:
        return json.dumps(self._dict, default=str)


class ContextCache:
    """Remembers when we last fetched the replies to a post.

    Fresh posts gain replies quickly and old ones rarely, so how long we wait
    before looking again depends on how old the post is.
    """

    def __init__(self, entries: dict[str, dict[str, Any]] | None = None) -> None:
        self._entries: dict[str, dict[str, datetime]] = {}
        for uri, entry in (entries or {}).items():
            if 'lastSeen' not in entry or 'created_at' not in entry:
                continue
            self._entries[uri] = {
                'lastSeen': _as_datetime(entry['lastSeen']),
                'created_at': _as_datetime(entry['created_at']),
            }

    def should_fetch(self, uri: str, created_at: datetime | str) -> bool:
        entry = self._entries.get(uri)
        if entry is None:
            return True

        last_seen = entry['lastSeen']
        created = entry['created_at']
        since_last_seen = (datetime.now(last_seen.tzinfo) - last_seen).total_seconds()
        age = (datetime.now(created.tzinfo) - created).total_seconds()

        if age <= 60 * 60 and since_last_seen >= 60:
            # For the first hour: allow refetching once per minute
            return True
        if age <= 24 * 60 * 60 and since_last_seen >= 10 * 60:
            # For the rest of the first day: once every 10 minutes
            return True
        if since_last_seen >= 60 * 60:
            # After that: hourly
            return True
        return False

    def mark_fetched(self, uri: str, created_at: datetime | str) -> None:
        self._entries[uri] = {
            'lastSeen': _now(),
            'created_at': _as_datetime(created_at),
        }

    def expire_older_than(self, age: timedelta) -> int:
        """Forget posts we have not seen in a while, so this cannot grow forever"""
        expired = [
            uri
            for uri, entry in self._entries.items()
            if datetime.now(entry['lastSeen'].tzinfo) - entry['lastSeen'] > age
        ]
        for uri in expired:
            del self._entries[uri]
        return len(expired)

    def __contains__(self, uri: object) -> bool:
        return uri in self._entries

    def __len__(self) -> int:
        return len(self._entries)

    def toJSON(self) -> str:
        return json.dumps(self._entries, default=str)


def _as_datetime(value: datetime | str) -> datetime:
    return parser.parse(value) if isinstance(value, str) else value
