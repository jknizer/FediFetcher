from __future__ import annotations

import itertools
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from fedifetcher.api.mastodon import HomeServer
from fedifetcher.backfill import add_user_posts, filter_known_users
from fedifetcher.config import Config
from fedifetcher.context import (
    add_context_urls,
    get_all_context_urls,
    get_all_known_context_urls,
    get_all_replied_toot_server_ids,
)
from fedifetcher.http import HttpClient
from fedifetcher.posts import Post
from fedifetcher.store import State
from fedifetcher.translate import usable
from fedifetcher.users import User

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable


logger = logging.getLogger("FediFetcher")


@dataclass(frozen=True, slots=True)
class Context:
    """Everything a task needs: the settings, what we remember, and who to ask"""

    config: Config
    state: State
    http: HttpClient
    home: HomeServer


def get_all_reply_toots(
    home: HomeServer,
    user_ids: Iterable[str],
    reply_interval_hours: float,
    *,
    state: State,
) -> list[Post]:
    """get all replies to other users by the given users in the last day"""
    # a post's date is UTC, so the window has to be measured in UTC too: local
    # time here quietly shortened the interval east of UTC and lengthened it west
    replies_since = datetime.now(UTC) - timedelta(hours=reply_interval_hours)
    reply_toots = list(
        itertools.chain.from_iterable(
            get_reply_toots(user_id, home, replies_since, state=state)
            for user_id in user_ids
        )
    )
    logger.info(f"Found {len(reply_toots)} reply toots")
    return reply_toots


def get_reply_toots(
    user_id: str, home: HomeServer, reply_since: datetime, *, state: State
) -> list[Post]:
    """get replies by the user to other users since the given date"""
    try:
        statuses = home.account_statuses(user_id)
    except Exception as ex:
        logger.error(
            f"Error getting replies for user {user_id} on server {home.server}: {ex}"
        )
        return []

    toots = [
        toot
        for toot in statuses
        if toot.in_reply_to_id is not None
        and toot.url not in state.seen_urls
        and toot.created_at > reply_since
    ]
    for toot in toots:
        logger.debug(f"Found reply toot: {toot.url}")
    return toots


def fetch_timeline_context(
    timeline_posts: list[Post],
    home: HomeServer,
    *,
    http: HttpClient,
    config: Config,
    state: State,
) -> None:
    known_context_urls = get_all_known_context_urls(
        config.server, timeline_posts, http=http, state=state
    )
    add_context_urls(home, known_context_urls, state=state)

    # Backfill any post authors, and any mentioned users
    if config.backfill_mentioned_users:
        mentioned_users: list[User] = []
        cut_off = datetime.now(datetime.now().astimezone().tzinfo) - timedelta(minutes=60)
        for toot in timeline_posts:
            these_users: list[User] = []
            if len(mentioned_users) < 10 or (toot.created_at > cut_off and len(mentioned_users) < 30):
                these_users += _accounts_named_by(toot)
                if toot.reblog is not None:
                    these_users += _accounts_named_by(toot.reblog)
            for user in these_users:
                if user not in mentioned_users and user.acct not in state.all_known_users:
                    mentioned_users.append(user)

        add_user_posts(home, filter_known_users(mentioned_users, state.all_known_users), state.recently_checked_users, http=http, config=config, state=state)



def _accounts_named_by(toot: Post) -> list[User]:
    """Who wrote a post, and everyone it mentions"""
    return usable([toot.account, *toot.mentions])


def fetch_from_lists(ctx: Context) -> None:
    """Pull replies to posts in the API key owner's lists, and backfill their members"""
    lists = ctx.home.lists()
    logger.info(f"Getting context for {len(lists)} lists")
    for user_list in lists:
        if ctx.config.max_list_length > 0:
            timeline_toots = ctx.home.list_timeline(user_list.id, ctx.config.max_list_length)
            logger.info(f"Found {len(timeline_toots)} toots in list {user_list.title}")
            fetch_timeline_context(timeline_toots, ctx.home, http=ctx.http, config=ctx.config, state=ctx.state)

        if ctx.config.max_list_accounts:
            accounts = ctx.home.list_accounts(user_list.id, ctx.config.max_list_accounts)
            logger.info(f"Found {len(accounts)} accounts in list {user_list.title}")
            add_user_posts(ctx.home, accounts, ctx.state.recently_checked_users, http=ctx.http, config=ctx.config, state=ctx.state)


def fetch_reply_context(ctx: Context) -> None:
    """Pull the context of toots our users replied to, from the original server"""
    user_ids = ctx.home.active_user_ids(ctx.config.reply_interval_in_hours)
    reply_toots = get_all_reply_toots(
        ctx.home, user_ids, ctx.config.reply_interval_in_hours, state=ctx.state
    )
    known_context_urls = get_all_known_context_urls(
        ctx.config.server, reply_toots, http=ctx.http, state=ctx.state
    )
    ctx.state.seen_urls.update(known_context_urls)
    replied_toot_ids = get_all_replied_toot_server_ids(
        ctx.config.server, reply_toots, http=ctx.http, state=ctx.state
    )
    context_urls = get_all_context_urls(
        ctx.config.server, replied_toot_ids, http=ctx.http, state=ctx.state
    )
    add_context_urls(ctx.home, context_urls, state=ctx.state)


def fetch_home_timeline(ctx: Context) -> None:
    """Pull replies to posts on the API key owner's home timeline"""
    logger.info("Getting context for home timeline")
    timeline_toots = ctx.home.timeline(ctx.config.home_timeline_length)
    fetch_timeline_context(timeline_toots, ctx.home, http=ctx.http, config=ctx.config, state=ctx.state)


def backfill_followings(ctx: Context) -> None:
    """Backfill posts of accounts our user has recently followed"""
    logger.info(f"Getting posts from last {ctx.config.max_followings} followings")
    followings = ctx.home.following(ctx.home.user_id(ctx.config.user), ctx.config.max_followings)
    new_followings = filter_known_users(followings, ctx.state.all_known_users)
    logger.info(f"Got {len(followings)} followings, {len(new_followings)} of which are new")
    add_user_posts(ctx.home, new_followings, ctx.state.known_followings, http=ctx.http, config=ctx.config, state=ctx.state)


def backfill_followers(ctx: Context) -> None:
    """Backfill posts of accounts that recently followed our user"""
    logger.info(f"Getting posts from last {ctx.config.max_followers} followers")
    followers = ctx.home.followers(ctx.home.user_id(ctx.config.user), ctx.config.max_followers)
    new_followers = filter_known_users(followers, ctx.state.all_known_users)
    logger.info(f"Got {len(followers)} followers, {len(new_followers)} of which are new")
    add_user_posts(ctx.home, new_followers, ctx.state.recently_checked_users, http=ctx.http, config=ctx.config, state=ctx.state)


def backfill_follow_requests(ctx: Context) -> None:
    """Backfill posts of accounts with a pending follow request"""
    logger.info(f"Getting posts from last {ctx.config.max_follow_requests} follow requests")
    follow_requests = ctx.home.follow_requests(ctx.config.max_follow_requests)
    new_requests = filter_known_users(follow_requests, ctx.state.all_known_users)
    logger.info(f"Got {len(follow_requests)} follow_requests, {len(new_requests)} of which are new")
    add_user_posts(ctx.home, new_requests, ctx.state.recently_checked_users, http=ctx.http, config=ctx.config, state=ctx.state)


def backfill_from_notifications(ctx: Context) -> None:
    """Backfill posts of accounts appearing in recent notifications"""
    logger.info(f"Getting notifications for last {ctx.config.from_notifications} hours")
    since = datetime.now(datetime.now().astimezone().tzinfo) - timedelta(hours=ctx.config.from_notifications)
    accounts = ctx.home.notification_accounts(since)
    new_accounts = filter_known_users(accounts, ctx.state.all_known_users)
    logger.info(f"Found {len(accounts)} users in notifications, {len(new_accounts)} of which are new")
    add_user_posts(ctx.home, new_accounts, ctx.state.recently_checked_users, http=ctx.http, config=ctx.config, state=ctx.state)


def fetch_bookmark_context(ctx: Context) -> None:
    """Pull replies to the API key owner's bookmarks"""
    logger.info(f"Pulling replies to the last {ctx.config.max_bookmarks} bookmarks")
    bookmarks = ctx.home.bookmarks(ctx.config.max_bookmarks)
    _pull_context_for(ctx, bookmarks)


def fetch_favourite_context(ctx: Context) -> None:
    """Pull replies to the API key owner's favourites"""
    logger.info(f"Pulling replies to the last {ctx.config.max_favourites} favourites")
    favourites = ctx.home.favourites(ctx.config.max_favourites)
    _pull_context_for(ctx, favourites)


def _pull_context_for(ctx: Context, posts: list[Post]) -> None:
    known_context_urls = get_all_known_context_urls(
        ctx.config.server, posts, http=ctx.http, state=ctx.state
    )
    add_context_urls(ctx.home, known_context_urls, state=ctx.state)


# each task runs only when its setting asks for it
ENABLED_BY: tuple[tuple[Callable[[Config], bool], Callable[[Context], None]], ...] = (
    (lambda c: c.from_lists, fetch_from_lists),
    (lambda c: c.reply_interval_in_hours > 0, fetch_reply_context),
    (lambda c: c.home_timeline_length > 0, fetch_home_timeline),
    (lambda c: c.max_followings > 0, backfill_followings),
    (lambda c: c.max_followers > 0, backfill_followers),
    (lambda c: c.max_follow_requests > 0, backfill_follow_requests),
    (lambda c: c.from_notifications > 0, backfill_from_notifications),
    (lambda c: c.max_bookmarks > 0, fetch_bookmark_context),
    (lambda c: c.max_favourites > 0, fetch_favourite_context),
)


def run_enabled_tasks(ctx: Context) -> None:
    for is_enabled, task in ENABLED_BY:
        if is_enabled(ctx.config):
            task(ctx)
