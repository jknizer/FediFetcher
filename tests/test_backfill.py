import logging
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock, patch

import pytest

from fedifetcher import backfill
from fedifetcher.backfill import (
    add_user_posts,
    filter_known_users,
    user_has_opted_out,
)
from fedifetcher.config import Config
from fedifetcher.state import TimestampedSet
from fedifetcher.users import User
from tests.conftest import make_post, make_user


def config_with(**settings: Any) -> Config:
    """A Config stand-in carrying only the settings a test cares about"""
    return cast("Config", SimpleNamespace(**settings))


@patch("fedifetcher.backfill.get_user_posts")
@patch("fedifetcher.backfill.add_post_with_context")
@patch("fedifetcher.backfill.logger")
def test_add_user_posts(mock_logger, mock_add_post, mock_get_posts, state, home):
    config = Mock()
    http = Mock()
    followings = [
        make_user(acct="user1", url="https://user1.com"),
        make_user(acct="user2", url="https://test_server/user2"),
    ]
    known_followings = TimestampedSet()

    mock_get_posts.return_value = [
        make_post(url="https://user1.com/post1"),
        make_post(url="https://user1.com/post2"),
    ]
    mock_add_post.return_value = True

    add_user_posts(
        home, followings, known_followings, http=http, config=config, state=state
    )

    mock_get_posts.assert_called_once_with(
        followings[0], known_followings, home.server, http=http, config=config,
        state=state
    )
    assert mock_add_post.call_count == 2
    assert len(state.seen_urls) == 2
    assert "user1" in known_followings
    assert "user1" in state.all_known_users
    mock_logger.info.assert_called_with("Added 2 posts for user user1 with 0 errors")


@patch("fedifetcher.backfill.get_user_posts")
@patch("fedifetcher.backfill.add_post_with_context")
@patch("fedifetcher.backfill.logger")
def test_add_user_posts_with_no_new_posts(mock_logger, mock_add_post, mock_get_posts, state, home):
    config = Mock()
    http = Mock()
    followings = [make_user(acct="user1", url="https://user1.com")]
    known_followings = TimestampedSet()
    state.seen_urls.update(["https://user1.com/post1", "https://user1.com/post2"])

    mock_get_posts.return_value = [
        make_post(url="https://user1.com/post1"),
        make_post(url="https://user1.com/post2"),
    ]
    mock_add_post.return_value = True

    add_user_posts(
        home, followings, known_followings, http=http, config=config, state=state
    )

    mock_get_posts.assert_called_once_with(
        followings[0], known_followings, home.server, http=http, config=config,
        state=state
    )
    mock_add_post.assert_not_called()
    assert len(state.seen_urls) == 2
    assert "user1" in known_followings
    assert "user1" in state.all_known_users


def test_add_post_with_context_post_not_added(state, home, http):
    home.resolve.return_value = False
    config = Mock()

    post = make_post(url="http://example.com")
    result = backfill.add_post_with_context(
        post, home, http=http, config=config, state=state
    )

    home.resolve.assert_called_once_with(post.url)
    assert result is False


def test_user_has_opted_out():
    assert not user_has_opted_out(make_user())
    assert not user_has_opted_out(make_user(note="I love robots"))
    assert user_has_opted_out(make_user(note="I love robots, nobot"))
    assert user_has_opted_out(make_user(note="/tags/nobot"))
    assert user_has_opted_out(make_user(indexable=False))
    assert user_has_opted_out(make_user(discoverable=False))


def test_filter_known_users():
    users = [make_user(acct=f"user{n}") for n in (1, 2, 3)]
    known_users = ["user1", "user3"]

    filtered_users = filter_known_users(users, known_users)

    assert filtered_users == [make_user(acct="user2")]


def test_filter_known_users_no_known_users():
    users = [make_user(acct=f"user{n}") for n in (1, 2, 3)]
    known_users: list[str] = []

    filtered_users = filter_known_users(users, known_users)

    assert filtered_users == users


def test_filter_known_users_all_users_known():
    users = [make_user(acct=f"user{n}") for n in (1, 2, 3)]
    known_users = ["user1", "user2", "user3"]

    filtered_users = filter_known_users(users, known_users)

    assert filtered_users == []


def test_filter_known_users_no_users():
    users: list[User] = []
    known_users = ["user1", "user2", "user3"]

    filtered_users = filter_known_users(users, known_users)

    assert filtered_users == []


@pytest.fixture
def config():
    """The settings backfilling reads while gathering a user's posts"""
    return config_with(max_posts_per_account=40)


def account(acct="someone@remote.example", url="https://remote.example/@someone", **extra):
    return make_user(acct=acct, url=url, **extra)


def test_opted_out_users_are_left_alone(state, http, caplog, config):
    caplog.set_level(logging.DEBUG)
    target = TimestampedSet()

    posts = backfill.get_user_posts(
        account(note="please no bots, nobot"), target, "our.example", http=http, config=config, state=state
    )

    assert posts is None
    assert "opted out" in caplog.text
    # not recorded as known, so a later change of heart is picked up
    assert "someone@remote.example" not in target


@pytest.mark.parametrize(
    "flag", [{"indexable": False}, {"discoverable": False}]
)
def test_users_who_hid_themselves_are_left_alone(flag, state, http, config):
    assert backfill.get_user_posts(
        account(**flag), TimestampedSet(), "our.example", http=http, config=config, state=state
    ) is None


def test_an_unparseable_profile_url_is_remembered_so_we_stop_retrying(state, http, config):
    target = TimestampedSet()

    posts = backfill.get_user_posts(
        account(url="not a url"), target, "our.example", http=http, config=config, state=state
    )

    assert posts is None
    assert "someone@remote.example" in target


def test_our_own_users_are_skipped(state, http, caplog, config):
    caplog.set_level(logging.DEBUG)
    target = TimestampedSet()

    posts = backfill.get_user_posts(
        account(url="https://our.example/@someone"), target, "our.example",
        http=http, config=config, state=state,
    )

    assert posts is None
    assert "is a local user" in caplog.text
    assert "someone@remote.example" in target


def test_a_server_we_cannot_reach_is_not_remembered(state, http, caplog, config):
    target = TimestampedSet()

    with patch.object(backfill, "get_server_info", return_value=None):
        posts = backfill.get_user_posts(
            account(), target, "our.example", http=http, config=config, state=state
        )

    assert posts is None
    assert "not found for post" in caplog.text
    # left unknown deliberately: the server may come back
    assert "someone@remote.example" not in target


def test_a_server_we_cannot_talk_to_yields_nothing(state, http, config):
    with patch.object(backfill, "get_server_info", return_value=Mock()), \
         patch.object(backfill, "client_for", return_value=None):
        assert backfill.get_user_posts(
            account(), TimestampedSet(), "our.example", http=http, config=config, state=state
        ) is None


def test_posts_come_from_the_client_for_that_server(state, http, config):
    client = Mock()
    client.username_from.return_value = "someone"
    client.fetch_user_posts.return_value = [make_post()]

    with patch.object(backfill, "get_server_info", return_value=Mock()), \
         patch.object(backfill, "client_for", return_value=client):
        posts = backfill.get_user_posts(
            account(), TimestampedSet(), "our.example", http=http, config=config, state=state
        )

    assert posts == [make_post()]
    client.fetch_user_posts.assert_called_once_with(
        "someone", "https://remote.example/@someone", 40
    )


def test_a_post_the_server_refuses_is_reported_as_not_added(state, home, http):
    home.resolve.return_value = False
    config = config_with(backfill_with_context=True)

    added = backfill.add_post_with_context(
        make_post(url="https://remote.example/@a/1"), home, http=http, config=config, state=state
    )

    assert added is False
    assert "https://remote.example/@a/1" not in state.seen_urls


def test_an_added_post_is_remembered(state, home, http):
    home.resolve.return_value = True
    config = config_with(backfill_with_context=False)

    added = backfill.add_post_with_context(
        make_post(url="https://remote.example/@a/1"), home, http=http, config=config, state=state
    )

    assert added is True
    assert "https://remote.example/@a/1" in state.seen_urls


def test_replies_are_pulled_in_when_context_is_wanted(state, home, http):
    home.resolve.return_value = True
    config = config_with(backfill_with_context=True)
    post = make_post(url="https://remote.example/@a/1", reply_count=2)

    with patch.object(backfill, "find_post", return_value=("remote.example", "1")), \
         patch.object(backfill, "get_all_known_context_urls", return_value=["u"]) as gather, \
         patch.object(backfill, "add_context_urls") as add:
        backfill.add_post_with_context(post, home, http=http, config=config, state=state)

    gather.assert_called_once_with(home.server, [post], http=http, state=state)
    add.assert_called_once_with(home, ["u"], state=state)


def test_replies_are_left_alone_when_context_is_not_wanted(state, home, http):
    home.resolve.return_value = True
    config = config_with(backfill_with_context=False)
    post = make_post(url="https://remote.example/@a/1", reply_count=2)

    with patch.object(backfill, "get_all_known_context_urls") as gather:
        backfill.add_post_with_context(post, home, http=http, config=config, state=state)

    gather.assert_not_called()


def test_a_post_whose_url_we_cannot_parse_is_still_added(state, home, http):
    home.resolve.return_value = True
    config = config_with(backfill_with_context=True)
    post = make_post(url="https://remote.example/@a/1", reply_count=2)

    with patch.object(backfill, "find_post", return_value=None), \
         patch.object(backfill, "get_all_known_context_urls") as gather:
        added = backfill.add_post_with_context(
            post, home, http=http, config=config, state=state
        )

    assert added is True
    gather.assert_not_called()


def test_a_user_is_only_marked_known_when_every_post_succeeded(state, home, http, caplog):
    caplog.set_level(logging.INFO)
    config = Mock()
    target = TimestampedSet()
    posts = [
        make_post(url="https://remote.example/@a/1"),
        make_post(url="https://remote.example/@a/2"),
    ]

    with patch.object(backfill, "get_user_posts", return_value=posts), \
         patch.object(backfill, "add_post_with_context", side_effect=[True, False]):
        backfill.add_user_posts(
            home, [account()], target, http=http, config=config, state=state
        )

    assert "Added 1 posts for user someone@remote.example with 1 errors" in caplog.text
    assert "someone@remote.example" not in target
    assert "someone@remote.example" not in state.all_known_users


def test_reblogs_and_renotes_are_not_added(state, home, http):
    # a Mastodon boost and a Misskey renote arrive as the same thing now
    posts = [
        make_post(url="https://remote.example/@a/1", reblog=make_post(url="x")),
        make_post(url="https://remote.example/@a/2", reblog=make_post(url="y")),
    ]

    with patch.object(backfill, "get_user_posts", return_value=posts), \
         patch.object(backfill, "add_post_with_context") as add:
        backfill.add_user_posts(
            home, [account()], TimestampedSet(), http=http, config=Mock(), state=state
        )

    add.assert_not_called()


def test_users_we_already_know_are_skipped(state, home, http):
    state.all_known_users.add("someone@remote.example")

    with patch.object(backfill, "get_user_posts") as fetch:
        backfill.add_user_posts(
            home, [account()], TimestampedSet(), http=http, config=Mock(), state=state
        )

    fetch.assert_not_called()


def test_users_on_our_own_server_are_skipped(state, home, http):
    with patch.object(backfill, "get_user_posts") as fetch:
        backfill.add_user_posts(
            home, [account(url=f"https://{home.server}/@someone")], TimestampedSet(),
            http=http, config=Mock(), state=state,
        )

    fetch.assert_not_called()
