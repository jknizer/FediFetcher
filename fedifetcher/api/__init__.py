from __future__ import annotations

import logging
from typing import TYPE_CHECKING, ClassVar, Protocol, runtime_checkable

from fedifetcher.api.lemmy import LemmyApi
from fedifetcher.api.mastodon import MastodonApi
from fedifetcher.api.misskey import MisskeyApi
from fedifetcher.api.peertube import PeerTubeApi
from fedifetcher.posts import Post
from fedifetcher.servers import ApiFlavour, ServerInfo, get_server_info
from fedifetcher.state import ServerCache
from fedifetcher.urls import PostRef, host_of

if TYPE_CHECKING:
    from fedifetcher.http import HttpClient

logger = logging.getLogger("FediFetcher")


@runtime_checkable
class FediverseApi(Protocol):
    """What FediFetcher needs from a server, however that server speaks"""

    flavour: ClassVar[ApiFlavour]

    def post_id_from(self, post_url: str) -> str | None:
        """The post's id in one of this server's post URLs, or None if it is not one"""
        ...

    def username_from(self, profile_url: str) -> str | None:
        """The name in one of this server's profile URLs, or None if it is not one.

        Only ever asked of a server we have already identified, so each client
        answers for its own URL shapes and no others.
        """
        ...

    def fetch_user_posts(
        self, username: str, profile_url: str, limit: int
    ) -> list[Post] | None:
        """At most `limit` recent posts by an account, or None if unreadable.

        Servers hand these out a page at a time, so anything beyond the first
        page costs another request to a server that is not ours.
        """
        ...

    def fetch_context_urls(self, post_id: str, post_url: str) -> list[str]:
        """URLs of the posts around a post: its ancestors and replies"""
        ...


# tried in order, so a server claiming several APIs gets the best supported one
CLIENTS: tuple[type[FediverseApi], ...] = (
    MastodonApi,
    LemmyApi,
    MisskeyApi,
    PeerTubeApi,
)


def client_for(server: ServerInfo, http: HttpClient) -> FediverseApi | None:
    """Pick a client that can talk to this server, if we know how"""
    for client in CLIENTS:
        if server.supports(client.flavour):
            return client(server.webserver, http)  # type: ignore[call-arg]

    logger.error(f'server api unknown for {server.webserver}')
    return None


def find_post(
    url: str,
    parsed_urls: dict[str, PostRef | None],
    seen_hosts: ServerCache,
    http: HttpClient,
) -> PostRef | None:
    """Which server a post URL is on and what it calls the post, remembering it.

    The host is in the URL; how to read the rest of it is not, so the host is
    asked what software it runs and its client reads the remainder. Answers are
    kept because the same URL comes round again, and because a Pleroma object
    URL costs a redirect to resolve.
    """
    if url in parsed_urls:
        return parsed_urls[url]

    parsed_urls[url] = match = _look_up_post(url, seen_hosts, http)
    if match is None:
        logger.error(f"Error parsing toot URL {url}")
    return match


def _look_up_post(
    url: str, seen_hosts: ServerCache, http: HttpClient
) -> PostRef | None:
    host = host_of(url)
    if host is None:
        return None

    server = get_server_info(host, seen_hosts, http=http)
    if server is None:
        return None

    client = client_for(server, http)
    if client is None:
        return None

    post_id = client.post_id_from(url)
    return None if post_id is None else PostRef(host, post_id)


__all__ = [
    "CLIENTS",
    "FediverseApi",
    "LemmyApi",
    "MastodonApi",
    "MisskeyApi",
    "PeerTubeApi",
    "client_for",
    "find_post",
]
