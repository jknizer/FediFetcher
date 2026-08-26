from datetime import UTC, datetime, timedelta

import pytest

from fedifetcher.api.mastodon import HomeServer, get_paginated, report_mastodon_error
from tests.conftest import make_user


@pytest.fixture
def home(http):
    return HomeServer("example.social", "secret-token-value", http)


def page(json_data, next_url=None):
    from unittest.mock import Mock
    resp = Mock()
    resp.status_code = 200
    resp.json.return_value = json_data
    resp.links = {"next": {"url": next_url}} if next_url else {}
    return resp


def status(n):
    """A post in the shape our own server sends it"""
    return {
        "url": f"https://example.social/@a/{n}",
        "created_at": "2026-01-01T00:00:00.000Z",
        "visibility": "public",
    }


def urls(posts):
    return [post.url for post in posts]


def test_requests_carry_the_token(home, http, reply):
    http.get.return_value = page([])
    home.bookmarks(5)
    headers = http.get.call_args[0][1]
    assert headers["Authorization"] == "Bearer secret-token-value"


def test_a_limit_becomes_a_query_parameter(home, http):
    http.get.return_value = page([])
    home.bookmarks(5)
    assert http.get.call_args[0][0] == "https://example.social/api/v1/bookmarks?limit=5"


def test_a_large_limit_is_capped_to_what_a_server_will_give(home, http):
    """Sharkey refuses an oversized limit outright, where Mastodon just trims it"""
    http.get.return_value = page([])
    home.timeline(200)
    assert http.get.call_args[0][0] == (
        "https://example.social/api/v1/timelines/home?limit=40"
    )


def test_a_capped_first_page_still_pages_on_to_the_limit(home, http):
    http.get.side_effect = [
        page([status(n) for n in range(40)], next_url="https://example.social/next"),
        page([status(n) for n in range(40, 80)]),
    ]
    assert len(home.timeline(80)) == 80
    assert http.get.call_args_list[1][0][0] == "https://example.social/next"


def test_lists_are_read_without_a_limit(home, http):
    http.get.return_value = page([])
    home.lists()
    assert http.get.call_args[0][0] == "https://example.social/api/v1/lists"


def test_pages_are_followed_until_the_limit_is_reached(home, http):
    http.get.side_effect = [
        page([status(1), status(2)], next_url="https://example.social/next"),
        page([status(3), status(4)]),
    ]
    assert urls(home.favourites(4)) == [f"https://example.social/@a/{n}" for n in (1, 2, 3, 4)]


def test_pagination_stops_when_a_page_is_not_a_list(home, http):
    http.get.side_effect = [
        page([status(1)], next_url="https://example.social/next"),
        page({"error": "nope"}),
    ]
    assert urls(home.favourites(5)) == ["https://example.social/@a/1"]


def test_pagination_by_date_stops_at_the_cutoff(home, http):
    recent = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    old = (datetime.now(UTC) - timedelta(days=5)).isoformat()
    http.get.side_effect = [
        page([{"created_at": recent}], next_url="https://example.social/next"),
        page([{"created_at": old}]),
    ]

    result = get_paginated(
        "https://example.social/api/v1/notifications",
        datetime.now(UTC) - timedelta(hours=1),
        http=http,
    )

    assert len(result) == 2


def test_an_error_status_is_reported_with_the_token_masked(home, http, reply):
    http.get.return_value = reply(401)
    with pytest.raises(Exception) as caught:
        home.bookmarks(5)
    assert "secret-token-value" not in str(caught.value)
    assert "access token is incorrect" in str(caught.value)


def test_a_missing_scope_is_named_when_known():
    with pytest.raises(Exception, match="read:statuses"):
        report_mastodon_error("boom", 403, "0123456789abcde", "read:statuses")


def test_notifications_are_reduced_to_distinct_accounts(home, http):
    now = datetime.now(UTC)
    account = {"acct": "someone@example.social", "url": "https://example.social/@someone"}
    http.get.return_value = page([
        {"created_at": now.isoformat(), "account": account},
        {"created_at": now.isoformat(), "account": account},
    ])

    assert home.notification_accounts(now - timedelta(hours=1)) == [
        make_user(acct="someone@example.social", url="https://example.social/@someone")
    ]


def test_notifications_older_than_the_cutoff_are_ignored(home, http):
    now = datetime.now(UTC)
    http.get.return_value = page([
        {"created_at": (now - timedelta(days=2)).isoformat(),
         "account": {"acct": "old", "url": "https://example.social/@old"}},
    ])

    assert home.notification_accounts(now - timedelta(hours=1)) == []


def test_the_timeline_is_paginated_to_the_requested_length(home, http):
    http.get.side_effect = [
        page([status(1), status(2)], next_url="https://example.social/next"),
        page([status(3), status(4)]),
    ]
    assert urls(home.timeline(4)) == [f"https://example.social/@a/{n}" for n in (1, 2, 3, 4)]


def test_the_timeline_names_the_scope_it_needs(home, http, reply):
    http.get.return_value = reply(403)
    with pytest.raises(Exception, match="read:statuses"):
        home.timeline(4)


def test_active_user_ids_are_those_who_posted_recently(home, http):
    recent = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    old = (datetime.now() - timedelta(days=400)).strftime("%Y-%m-%d")
    http.get.return_value = page([
        {"id": "1", "username": "active", "account": {"last_status_at": recent}},
        {"id": "2", "username": "dormant", "account": {"last_status_at": old}},
        {"id": "3", "username": "silent", "account": {"last_status_at": None}},
    ])

    assert list(home.active_user_ids(24)) == ["1"]


def test_resolving_a_url_asks_our_server_to_search_for_it(home, http, reply):
    http.get.return_value = reply(200)

    assert home.resolve("https://elsewhere/@a/1") is True

    assert http.get.call_args[0][0] == (
        "https://example.social/api/v2/search?q=https://elsewhere/@a/1&resolve=true&limit=1"
    )


def test_resolving_reports_a_missing_search_scope(home, http, reply, caplog):
    http.get.return_value = reply(403)
    assert home.resolve("https://elsewhere/@a/1") is False
    assert "read:search" in caplog.text


def test_resolving_survives_a_failed_request(home, http):
    http.get.side_effect = Exception("no route to host")
    assert home.resolve("https://elsewhere/@a/1") is False


def test_lists_and_their_contents_are_addressed_by_id(home, http):
    http.get.return_value = page([])

    home.list_timeline("42", 100)
    assert "/api/v1/timelines/list/42" in http.get.call_args[0][0]

    home.list_accounts("42", 10)
    assert "/api/v1/lists/42/accounts" in http.get.call_args[0][0]


def test_followers_and_following_are_addressed_by_user_id(home, http):
    http.get.return_value = page([])

    home.followers("7", 5)
    assert "/api/v1/accounts/7/followers" in http.get.call_args[0][0]

    home.following("7", 5)
    assert "/api/v1/accounts/7/following" in http.get.call_args[0][0]
