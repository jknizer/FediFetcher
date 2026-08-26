from __future__ import annotations

from typing import NamedTuple
from urllib.parse import urlparse


class PostRef(NamedTuple):
    """A post identified by the server hosting it and its ID on that server"""

    server: str
    post_id: str


def host_of(url: str) -> str | None:
    """The server a URL points at, whatever the rest of it looks like.

    Which server it is decides how to read the rest, so this is the one thing
    that can be worked out without knowing what software is answering.
    """
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        return None
    return parsed.netloc
