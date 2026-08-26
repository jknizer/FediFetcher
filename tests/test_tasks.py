from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from fedifetcher import tasks
from fedifetcher.lists import UserList
from fedifetcher.store import State
from tests.conftest import make_post, make_user


@patch("fedifetcher.tasks.get_all_known_context_urls")
@patch("fedifetcher.tasks.add_context_urls")
@patch("fedifetcher.tasks.add_user_posts")
@patch("fedifetcher.tasks.filter_known_users")
def test_fetch_timeline_context_with_empty_posts(
    mock_filter_known_users,
    mock_add_user_posts,
    mock_add_context_urls,
    mock_get_all_known_context_urls,
    state,
    http,
    home,
):
    config = config_with(server="server_test")

    tasks.fetch_timeline_context([], home, http=http, config=config, state=state)

    mock_get_all_known_context_urls.assert_called_once_with(
        config.server, [], http=http, state=state
    )
    mock_add_context_urls.assert_called_once_with(
        home, mock_get_all_known_context_urls.return_value, state=state
    )
    assert not mock_filter_known_users.called
    assert not mock_add_user_posts.called


def config_with(**settings):
    defaults = dict(
        server="example.social",
        user="",
        backfill_mentioned_users=False,
        max_list_length=0,
        max_list_accounts=0,
        from_lists=False, reply_interval_in_hours=0, home_timeline_length=0,
        max_followings=0, max_followers=0, max_follow_requests=0,
        from_notifications=0, max_bookmarks=0, max_favourites=0,
    )
    defaults.update(settings)
    return SimpleNamespace(**defaults)


def context_for(**settings):
    return tasks.Context(
        config=config_with(**settings), state=Mock(), http=Mock(), home=Mock()
    )


def test_nothing_runs_when_nothing_is_enabled():
    ran = []
    patched = [
        (is_enabled, (lambda name: lambda ctx: ran.append(name))(task.__name__))
        for is_enabled, task in tasks.ENABLED_BY
    ]
    with patch.object(tasks, "ENABLED_BY", patched):
        tasks.run_enabled_tasks(context_for())
    assert ran == []


@pytest.mark.parametrize(
    "setting,expected",
    [
        ({"from_lists": True}, "fetch_from_lists"),
        ({"reply_interval_in_hours": 1}, "fetch_reply_context"),
        ({"home_timeline_length": 10}, "fetch_home_timeline"),
        ({"max_followings": 10}, "backfill_followings"),
        ({"max_followers": 10}, "backfill_followers"),
        ({"max_follow_requests": 10}, "backfill_follow_requests"),
        ({"from_notifications": 10}, "backfill_from_notifications"),
        ({"max_bookmarks": 10}, "fetch_bookmark_context"),
        ({"max_favourites": 10}, "fetch_favourite_context"),
    ],
)
def test_each_setting_enables_its_own_task(setting, expected):
    ran = []
    patched = [
        (is_enabled, (lambda name: lambda ctx: ran.append(name))(task.__name__))
        for is_enabled, task in tasks.ENABLED_BY
    ]
    with patch.object(tasks, "ENABLED_BY", patched):
        tasks.run_enabled_tasks(context_for(**setting))
    assert ran == [expected]


def test_tasks_run_in_the_documented_order():
    ran = []
    patched = [
        (lambda c: True, (lambda name: lambda ctx: ran.append(name))(task.__name__))
        for _, task in tasks.ENABLED_BY
    ]
    with patch.object(tasks, "ENABLED_BY", patched):
        tasks.run_enabled_tasks(context_for())

    assert ran == [
        "fetch_from_lists", "fetch_reply_context", "fetch_home_timeline",
        "backfill_followings", "backfill_followers", "backfill_follow_requests",
        "backfill_from_notifications", "fetch_bookmark_context",
        "fetch_favourite_context",
    ]


def test_bookmarks_pull_context_for_what_they_find():
    ctx = context_for(max_bookmarks=5)
    bookmark = raw_status()
    ctx.home.bookmarks.return_value = [bookmark]

    with patch.object(tasks, "get_all_known_context_urls", return_value=["url"]) as gather, \
         patch.object(tasks, "add_context_urls") as add:
        tasks.fetch_bookmark_context(ctx)

    ctx.home.bookmarks.assert_called_once_with(5)
    gather.assert_called_once_with(
        ctx.config.server, [bookmark], http=ctx.http, state=ctx.state
    )
    add.assert_called_once_with(ctx.home, ["url"], state=ctx.state)


def raw_status(url="https://our.example/@a/1"):
    """A post as our own server hands it over, already translated"""
    return make_post(url=url, uri=url)


def real_context(**settings):
    """A Context whose state is real, so tests can assert on what got remembered"""
    return tasks.Context(
        config=config_with(**settings), state=State(), http=Mock(), home=Mock()
    )


def reply(url, created_at, in_reply_to="7"):
    return make_post(url=url, in_reply_to_id=in_reply_to, created_at=created_at)


RECENT = datetime.now(UTC) - timedelta(minutes=5)
OLD = datetime.now(UTC) - timedelta(days=5)
A_DAY_AGO = datetime.now(UTC) - timedelta(days=1)


def test_only_replies_count_as_reply_toots():
    ctx = real_context()
    ctx.home.account_statuses.return_value = [
        reply("https://our.example/@a/1", RECENT),
        make_post(url="https://our.example/@a/2", created_at=RECENT),
    ]

    toots = tasks.get_reply_toots("1", ctx.home, A_DAY_AGO, state=ctx.state)

    assert [toot.url for toot in toots] == ["https://our.example/@a/1"]


def test_replies_older_than_the_window_are_ignored():
    ctx = real_context()
    ctx.home.account_statuses.return_value = [reply("https://our.example/@a/1", OLD)]

    toots = tasks.get_reply_toots("1", ctx.home, A_DAY_AGO, state=ctx.state)

    assert toots == []


def test_replies_we_have_already_seen_are_ignored():
    ctx = real_context()
    ctx.state.seen_urls.add("https://our.example/@a/1")
    ctx.home.account_statuses.return_value = [reply("https://our.example/@a/1", RECENT)]

    toots = tasks.get_reply_toots("1", ctx.home, A_DAY_AGO, state=ctx.state)

    assert toots == []


def test_the_reply_window_is_measured_in_utc():
    """A naive local cutoff silently shortened the interval east of UTC"""
    ctx = real_context()

    with patch.object(tasks, "get_reply_toots", return_value=[]) as fetch:
        tasks.get_all_reply_toots(ctx.home, ["1"], 24, state=ctx.state)

    since = fetch.call_args[0][2]
    assert since.tzinfo is not None
    assert abs(datetime.now(UTC) - timedelta(hours=24) - since) < timedelta(seconds=5)


def test_one_users_failure_does_not_stop_the_others(caplog):
    ctx = real_context()
    ctx.home.account_statuses.side_effect = [
        Exception("boom"),
        [reply("https://our.example/@b/1", RECENT)],
    ]

    toots = tasks.get_all_reply_toots(ctx.home, ["1", "2"], 24, state=ctx.state)

    assert [toot.url for toot in toots] == ["https://our.example/@b/1"]
    assert "Error getting replies for user 1" in caplog.text


def account(acct):
    """An account as a post names it"""
    return make_user(acct=acct, url=f"https://remote.example/@{acct}")


def timeline_post(acct="author@remote.example", mentions=(), reblog=None, created_at=None):
    return make_post(
        url=f"https://remote.example/@{acct}/1",
        created_at=created_at or datetime.now(UTC),
        account=account(acct),
        mentions=tuple(account(m) for m in mentions),
        reblog=reblog,
    )


def test_timeline_context_backfills_authors_and_mentions():
    ctx = real_context(backfill_mentioned_users=True)
    post = timeline_post(mentions=["mentioned@remote.example"])

    with patch.object(tasks, "get_all_known_context_urls", return_value=[]), \
         patch.object(tasks, "add_context_urls"), \
         patch.object(tasks, "add_user_posts") as backfill:
        tasks.fetch_timeline_context([post], ctx.home, http=ctx.http, config=ctx.config, state=ctx.state)

    backfilled = [user.acct for user in backfill.call_args[0][1]]
    assert backfilled == ["author@remote.example", "mentioned@remote.example"]


def test_a_boosted_post_backfills_the_original_author():
    ctx = real_context(backfill_mentioned_users=True)
    post = timeline_post(reblog=timeline_post(acct="original@remote.example"))

    with patch.object(tasks, "get_all_known_context_urls", return_value=[]), \
         patch.object(tasks, "add_context_urls"), \
         patch.object(tasks, "add_user_posts") as backfill:
        tasks.fetch_timeline_context([post], ctx.home, http=ctx.http, config=ctx.config, state=ctx.state)

    backfilled = [user.acct for user in backfill.call_args[0][1]]
    assert "original@remote.example" in backfilled


def test_a_boost_of_a_post_that_mentions_people_backfills_them_too():
    ctx = real_context(backfill_mentioned_users=True)
    post = timeline_post(reblog=timeline_post(
        acct="original@remote.example", mentions=["mentioned@remote.example"],
    ))

    with patch.object(tasks, "get_all_known_context_urls", return_value=[]), \
         patch.object(tasks, "add_context_urls"), \
         patch.object(tasks, "add_user_posts") as backfill:
        tasks.fetch_timeline_context([post], ctx.home, http=ctx.http, config=ctx.config, state=ctx.state)

    backfilled = [user.acct for user in backfill.call_args[0][1]]
    assert "mentioned@remote.example" in backfilled


def test_users_we_already_know_are_not_backfilled_again():
    ctx = real_context(backfill_mentioned_users=True)
    ctx.state.all_known_users.add("author@remote.example")

    with patch.object(tasks, "get_all_known_context_urls", return_value=[]), \
         patch.object(tasks, "add_context_urls"), \
         patch.object(tasks, "add_user_posts") as backfill:
        tasks.fetch_timeline_context([timeline_post()], ctx.home, http=ctx.http, config=ctx.config, state=ctx.state)

    assert backfill.call_args[0][1] == []


def test_only_a_bounded_number_of_users_is_backfilled():
    """Old posts stop contributing users once ten are queued"""
    ctx = real_context(backfill_mentioned_users=True)
    old = datetime.now(UTC) - timedelta(days=2)
    posts = [
        timeline_post(acct=f"user{i}@remote.example", created_at=old)
        for i in range(40)
    ]

    with patch.object(tasks, "get_all_known_context_urls", return_value=[]), \
         patch.object(tasks, "add_context_urls"), \
         patch.object(tasks, "add_user_posts") as backfill:
        tasks.fetch_timeline_context(posts, ctx.home, http=ctx.http, config=ctx.config, state=ctx.state)

    assert len(backfill.call_args[0][1]) == 10


def test_lists_pull_both_replies_and_members():
    ctx = real_context(from_lists=True, max_list_length=50, max_list_accounts=5)
    ctx.home.lists.return_value = [UserList(id="42", title="Friends")]
    ctx.home.list_timeline.return_value = ["a post"]
    ctx.home.list_accounts.return_value = ["an account"]

    with patch.object(tasks, "fetch_timeline_context") as timeline, \
         patch.object(tasks, "add_user_posts") as backfill:
        tasks.fetch_from_lists(ctx)

    ctx.home.list_timeline.assert_called_once_with("42", 50)
    ctx.home.list_accounts.assert_called_once_with("42", 5)
    assert timeline.call_args[0][0] == ["a post"]
    assert backfill.call_args[0][1] == ["an account"]


def test_lists_can_fetch_replies_without_backfilling_members():
    ctx = real_context(from_lists=True, max_list_length=50, max_list_accounts=0)
    ctx.home.lists.return_value = [UserList(id="42", title="Friends")]
    ctx.home.list_timeline.return_value = ["a post"]

    with patch.object(tasks, "fetch_timeline_context"), \
         patch.object(tasks, "add_user_posts") as backfill:
        tasks.fetch_from_lists(ctx)

    backfill.assert_not_called()
    ctx.home.list_accounts.assert_not_called()


def test_reply_context_walks_from_our_replies_to_their_originals():
    ctx = real_context(reply_interval_in_hours=24)
    ctx.home.active_user_ids.return_value = ["1"]

    with patch.object(tasks, "get_all_reply_toots", return_value=[raw_status()]), \
         patch.object(tasks, "get_all_known_context_urls", return_value=["known"]), \
         patch.object(tasks, "get_all_replied_toot_server_ids", return_value=["ref"]), \
         patch.object(tasks, "get_all_context_urls", return_value=["ctx"]) as context_urls, \
         patch.object(tasks, "add_context_urls") as add:
        tasks.fetch_reply_context(ctx)

    assert "known" in ctx.state.seen_urls
    context_urls.assert_called_once_with(ctx.config.server, ["ref"], http=ctx.http, state=ctx.state)
    add.assert_called_once_with(ctx.home, ["ctx"], state=ctx.state)


def test_the_home_timeline_is_fetched_to_the_configured_length():
    ctx = real_context(home_timeline_length=200)
    ctx.home.timeline.return_value = ["a post"]

    with patch.object(tasks, "fetch_timeline_context") as timeline:
        tasks.fetch_home_timeline(ctx)

    ctx.home.timeline.assert_called_once_with(200)
    assert timeline.call_args[0][0] == ["a post"]


@pytest.mark.parametrize(
    "task,setting,fetcher,collection",
    [
        ("backfill_followings", {"max_followings": 5}, "following", "known_followings"),
        ("backfill_followers", {"max_followers": 5}, "followers", "recently_checked_users"),
        ("backfill_follow_requests", {"max_follow_requests": 5}, "follow_requests", "recently_checked_users"),
        ("backfill_from_notifications", {"from_notifications": 5}, "notification_accounts", "recently_checked_users"),
    ],
)
def test_each_backfill_task_uses_its_own_source_and_collection(task, setting, fetcher, collection):
    ctx = real_context(user="", **setting)
    getattr(ctx.home, fetcher).return_value = [make_user(acct="new@remote.example")]

    with patch.object(tasks, "add_user_posts") as backfill:
        getattr(tasks, task)(ctx)

    getattr(ctx.home, fetcher).assert_called_once()
    assert backfill.call_args[0][1] == [make_user(acct="new@remote.example")]
    assert backfill.call_args[0][2] is getattr(ctx.state, collection)


def test_accounts_we_already_know_are_filtered_out_before_backfilling():
    ctx = real_context(user="", max_followers=5)
    ctx.state.all_known_users.add("known@remote.example")
    ctx.home.followers.return_value = [
        make_user(acct="known@remote.example"), make_user(acct="new@remote.example")
    ]

    with patch.object(tasks, "add_user_posts") as backfill:
        tasks.backfill_followers(ctx)

    assert backfill.call_args[0][1] == [make_user(acct="new@remote.example")]


def test_favourites_pull_context_for_what_they_find():
    ctx = real_context(max_favourites=5)
    ctx.home.favourites.return_value = [raw_status()]

    with patch.object(tasks, "get_all_known_context_urls", return_value=["url"]), \
         patch.object(tasks, "add_context_urls") as add:
        tasks.fetch_favourite_context(ctx)

    ctx.home.favourites.assert_called_once_with(5)
    add.assert_called_once_with(ctx.home, ["url"], state=ctx.state)
