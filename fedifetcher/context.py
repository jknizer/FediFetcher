from __future__ import annotations

import itertools
import logging
from typing import TYPE_CHECKING, cast

from fedifetcher.api import client_for, find_post
from fedifetcher.posts import Post
from fedifetcher.servers import get_server_info
from fedifetcher.urls import PostRef

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from fedifetcher.api.mastodon import HomeServer
    from fedifetcher.http import HttpClient
    from fedifetcher.store import State

logger = logging.getLogger("FediFetcher")

RepliedToot = tuple[str, PostRef]
"""A post that was replied to: the URL we followed, and where it landed."""


def toot_context_can_be_fetched(toot: Post) -> bool:
    if not toot.is_public:
        logger.debug(f"Cannot fetch context of private toot {toot.uri}")
    return toot.is_public


def get_all_known_context_urls(
    server: str, reply_toots: Iterable[Post], *, http: HttpClient, state: State
) -> set[str]:
    """get the context toots of the given toots from their original server"""
    known_context_urls: set[str] = set()

    for toot in reply_toots:
        # a boost is only ever a pointer: the replies are on the original
        url = toot.original.url
        parsed_url = find_post(url, state.parsed_urls, state.seen_hosts, http)
        if parsed_url is None:
            continue
        if toot_context_can_be_fetched(toot) and state.recently_checked_context.should_fetch(toot.uri, toot.created_at):
            state.recently_checked_context.mark_fetched(toot.uri, toot.created_at)
            context = get_toot_context(parsed_url[0], parsed_url[1], url, http=http, state=state)
            if context is not None:
                for item in context:
                    known_context_urls.add(item)
            else:
                logger.error(f"Error getting context for toot {url}")

    known_context_urls = set(filter(lambda url: not url.startswith(f"https://{server}/"), known_context_urls))
    logger.info(f"Found {len(known_context_urls)} known context toots")

    return known_context_urls


def get_all_replied_toot_server_ids(
    server: str, reply_toots: Iterable[Post], *, http: HttpClient, state: State
) -> Iterator[RepliedToot]:
    """get the server and ID of the toots the given toots replied to"""
    return (
        replied
        for toot in reply_toots
        if (replied := get_replied_toot_server_id(server, toot, http=http, state=state))
        is not None
    )


def get_replied_toot_server_id(
    server: str, toot: Post, *, http: HttpClient, state: State
) -> RepliedToot | None:
    """get the server and ID of the toot the given toot replied to"""
    mentions = [
        mention
        for mention in toot.mentions
        if mention.id == toot.in_reply_to_account_id
    ]
    if len(mentions) == 0:
        return None

    mention = mentions[0]

    o_url = f"https://{server}/@{mention.acct}/{toot.in_reply_to_id}"
    if o_url in state.replied_toot_server_ids:
        # a cache entry that survived a run is JSON, so the PostRef is a list
        return cast("RepliedToot | None", state.replied_toot_server_ids[o_url])

    url = http.get_redirect_url(o_url)

    if url is None:
        return None

    match = find_post(url, state.parsed_urls, state.seen_hosts, http)
    if match is not None:
        state.replied_toot_server_ids[o_url] = (url, match)
        return (url, match)

    logger.error(f"Error parsing toot URL {url}")
    state.replied_toot_server_ids[o_url] = None
    return None

def get_all_context_urls(
    server: str,
    replied_toot_ids: Iterable[RepliedToot],
    *,
    http: HttpClient,
    state: State,
) -> Iterator[str]:
    """get the URLs of the context toots of the given toots"""
    return (
        context_url
        for context_url in itertools.chain.from_iterable(
            get_toot_context(server, toot_id, url, http=http, state=state)
            for (url, (server, toot_id)) in replied_toot_ids
        )
        if not context_url.startswith(f"https://{server}/")
    )


def get_toot_context(
    server: str, toot_id: str, toot_url: str, *, http: HttpClient, state: State
) -> list[str]:
    """get the URLs of the context toots of the given toot"""

    post_server = get_server_info(server, state.seen_hosts, http=http)
    if post_server is None:
        logger.error(f'server {server} not found for post')
        return []

    client = client_for(post_server, http)
    if client is None:
        return []

    return client.fetch_context_urls(toot_id, toot_url)

def add_context_urls(
    home: HomeServer, context_urls: Iterable[str], *, state: State
) -> None:
    """add the given toot URLs to the server"""
    count = 0
    failed = 0
    for url in context_urls:
        if url not in state.seen_urls:
            added = home.resolve(url)
            if added is True:
                state.seen_urls.add(url)
                count += 1
            else:
                failed += 1

    logger.info(f"Added {count} new context toots (with {failed} failures)")
