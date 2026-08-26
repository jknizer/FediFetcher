from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class UserList:
    """One of the API key owner's lists of accounts.

    A list is a name and the accounts in it. We never read the accounts from
    the list itself: they are fetched by id, as are the posts of everyone in
    it, so all we keep is what those two requests need.
    """

    id: str

    title: str
    """What the owner called this list, which only ever reaches a log line."""
