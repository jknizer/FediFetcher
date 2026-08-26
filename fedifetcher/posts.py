from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fedifetcher.users import User


@dataclass(frozen=True, slots=True)
class Post:
    """A post from any fediverse server, in the one shape FediFetcher uses.

    Servers disagree about almost everything here: what a post is called, how
    it says who may see it, and whether it admits to having replies. Each
    client translates its own server's posts into this shape as they arrive,
    so that nothing downstream has to ask who it is talking to.
    """

    url: str
    uri: str
    created_at: datetime
    is_public: bool
    reblog: Post | None = None
    in_reply_to_id: str | None = None
    in_reply_to_account_id: str | None = None
    reply_count: int | None = None
    account: User | None = None
    mentions: tuple[User, ...] = ()

    @property
    def original(self) -> Post:
        """The post itself, or the one it boosts"""
        return self.reblog or self

    @property
    def is_boost(self) -> bool:
        return self.reblog is not None

    @property
    def may_have_context(self) -> bool:
        """Whether the server said anything suggesting this post has replies"""
        return self.in_reply_to_id is not None or self.reply_count is not None
