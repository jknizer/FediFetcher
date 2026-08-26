import logging
from datetime import datetime
from unittest.mock import MagicMock, Mock, patch

import pytest

from fedifetcher import context
from fedifetcher.context import add_context_urls, get_toot_context
from fedifetcher.urls import PostRef
from tests.conftest import make_post, make_user


@pytest.fixture
def webserver():
    return "server.com"


@pytest.fixture
def userName():
    return "test_user"


@patch("fedifetcher.context.logger")
def test_toot_context_can_be_fetched_public(mock_logger):
    assert context.toot_context_can_be_fetched(make_post()) is True
    mock_logger.debug.assert_not_called()


@patch("fedifetcher.context.logger")
def test_toot_context_can_be_fetched_private(mock_logger):
    toot = make_post(uri="sample_uri", is_public=False)
    assert context.toot_context_can_be_fetched(toot) is False
    mock_logger.debug.assert_called_once_with(
        "Cannot fetch context of private toot sample_uri"
    )


toot_with_existing_uri = {
    "uri": "existing_uri",
    "lastSeen": datetime.now(),
    "created_at": datetime.now(),
}


toot_with_new_uri = {
    "uri": "new_uri",
    "lastSeen": datetime.now(),
    "created_at": datetime.now(),
}


recently_checked_context = {"existing_uri": toot_with_existing_uri}


@patch("fedifetcher.context.get_toot_context")
@patch("fedifetcher.context.find_post")
def test_get_all_known_context_urls(find_post, get_toot_context, state, http):
    reply_toots = [
        make_post(url="test_url_1", uri="test_uri_1"),
        make_post(url="test_url_2", uri="test_uri_2"),
    ]
    find_post.return_value = ("parsed_host", "parsed_id")
    get_toot_context.return_value = ["context_item_1", "context_item_2"]

    urls = context.get_all_known_context_urls(
        "test_server", reply_toots, http=http, state=state
    )

    assert urls == {"context_item_1", "context_item_2"}
    assert get_toot_context.call_count == 2
    # both posts are now remembered, so a second pass would not refetch them
    assert "test_uri_1" in state.recently_checked_context
    assert not context.get_all_known_context_urls(
        "test_server", reply_toots, http=http, state=state
    )


def test_get_replied_toot_server_id_no_mentions(state):
    http = Mock()
    toot = make_post(in_reply_to_id="1", in_reply_to_account_id="1")
    assert context.get_replied_toot_server_id("server", toot, http=http, state=state) is None


def test_get_replied_toot_server_id_no_url_redirect(state):
    http = Mock()
    http.get_redirect_url.return_value = None
    toot = make_post(
        in_reply_to_id="1",
        in_reply_to_account_id="1",
        mentions=(make_user(id="1", acct="account"),),
    )
    assert context.get_replied_toot_server_id("server", toot, http=http, state=state) is None


def test_get_replied_toot_server_id_with_url_redirect(state):
    http = Mock()
    http.get_redirect_url.return_value = "redirect_url"
    toot = make_post(
        in_reply_to_id="1",
        in_reply_to_account_id="1",
        mentions=(make_user(id="1", acct="account"),),
    )
    match = PostRef("server", "1")
    with patch("fedifetcher.context.find_post", return_value=match) as mock_parse:
        assert context.get_replied_toot_server_id(
            "server", toot, http=http, state=state
        ) == ("redirect_url", match)
        mock_parse.assert_called_once_with(
            "redirect_url", state.parsed_urls, state.seen_hosts, http
        )


def test_get_replied_toot_server_id_with_existing_replied_toot_server_ids(state):
    http = Mock()
    toot = make_post(
        in_reply_to_id="1",
        in_reply_to_account_id="1",
        mentions=(make_user(id="1", acct="account"),),
    )
    cached = ("url", PostRef("server", "1"))
    state.replied_toot_server_ids = {"https://server/@account/1": cached}

    assert context.get_replied_toot_server_id(
        "server", toot, http=http, state=state
    ) == cached


@patch("fedifetcher.context.get_server_info")
@patch("fedifetcher.context.logger")
def test_get_toot_context_no_server_info(mock_logger, mock_server_info, state, http):
    mock_server_info.return_value = None
    assert get_toot_context("server1", "toot1", "url1", http=http, state=state) == []
    mock_logger.error.assert_called_once_with("server server1 not found for post")


@pytest.fixture
def mock_response_success():
    return_value = MagicMock()
    return_value.status_code = 200
    return_value.json.return_value = {
        "ancestors": [{"url": "https://abc.com/statuses/123456"}],
        "descendants": [{"url": "https://abc.com/statuses/789012"}],
    }
    return return_value


@pytest.fixture
def mock_response_fail():
    return_value = MagicMock()
    return_value.status_code = 404
    return return_value


class MockResponse:
    def __init__(self, status_code, links=None, json_data=None):
        self.status_code = status_code
        self.links = links
        self.json_data = json_data

    def json(self):
        return self.json_data


def test_add_context_urls_records_what_the_server_accepted(state, home, caplog):
    caplog.set_level(logging.INFO)
    home.resolve.return_value = True

    add_context_urls(home, ["url1", "url2", "url3", "url4"], state=state)

    assert home.resolve.call_count == 4
    assert len(state.seen_urls) == 4
    assert "url1" in state.seen_urls
    assert "Added 4 new context toots (with 0 failures)" in caplog.text


def test_add_context_urls_counts_what_the_server_refused(state, home, caplog):
    caplog.set_level(logging.INFO)
    home.resolve.return_value = False

    add_context_urls(home, ["url1", "url2", "url3", "url4"], state=state)

    assert home.resolve.call_count == 4
    assert len(state.seen_urls) == 0
    assert "Added 0 new context toots (with 4 failures)" in caplog.text


def test_add_context_urls_skips_urls_we_already_have(state, home):
    state.seen_urls.add("url1")
    home.resolve.return_value = True

    add_context_urls(home, ["url1", "url2"], state=state)

    home.resolve.assert_called_once_with("url2")


def public_toot(uri="https://remote.example/@a/1", **extra):
    return {
        "url": "https://remote.example/@a/1",
        "uri": uri,
        "reblog": None,
        "visibility": "public",
        "created_at": "2026-01-01T00:00:00+00:00",
        **extra,
    }


def test_a_failed_context_lookup_is_reported(state, http, caplog):
    with patch.object(context, "find_post", return_value=("remote.example", "1")), \
         patch.object(context, "get_toot_context", return_value=None):
        urls = context.get_all_known_context_urls(
            "our.example", [make_post()], http=http, state=state
        )

    assert urls == set()
    assert "Error getting context for toot" in caplog.text


def test_posts_already_on_our_own_server_are_not_returned(state, http):
    ours = "https://our.example/@a/2"
    theirs = "https://remote.example/@b/3"

    with patch.object(context, "find_post", return_value=("remote.example", "1")), \
         patch.object(context, "get_toot_context", return_value=[ours, theirs]):
        urls = context.get_all_known_context_urls(
            "our.example", [make_post()], http=http, state=state
        )

    assert urls == {theirs}


def test_private_posts_are_not_asked_about(state, http):
    private = make_post(is_public=False)

    with patch.object(context, "find_post", return_value=("remote.example", "1")), \
         patch.object(context, "get_toot_context") as fetch:
        context.get_all_known_context_urls(
            "our.example", [private], http=http, state=state
        )

    fetch.assert_not_called()


def test_posts_we_cannot_parse_are_skipped(state, http):
    with patch.object(context, "find_post", return_value=None), \
         patch.object(context, "get_toot_context") as fetch:
        context.get_all_known_context_urls(
            "our.example", [make_post()], http=http, state=state
        )

    fetch.assert_not_called()


def test_a_reblog_is_followed_to_the_post_it_boosts(state, http):
    boost = make_post(reblog=make_post(url="https://remote.example/@original/9"))

    with patch.object(context, "find_post", return_value=("remote.example", "9")) as parse, \
         patch.object(context, "get_toot_context", return_value=[]):
        context.get_all_known_context_urls(
            "our.example", [boost], http=http, state=state
        )

    assert parse.call_args[0][0] == "https://remote.example/@original/9"


def test_replied_toot_ids_drop_the_ones_that_could_not_be_worked_out(state, http):
    with patch.object(context, "get_replied_toot_server_id", side_effect=[None, ("url", ("s", "1"))]):
        result = list(context.get_all_replied_toot_server_ids(
            "our.example", [public_toot(), public_toot()], http=http, state=state
        ))

    assert result == [("url", ("s", "1"))]


def test_an_unparseable_redirect_is_remembered_as_a_dead_end(state, http, caplog):
    toot = make_post(
        in_reply_to_id="1",
        in_reply_to_account_id="1",
        mentions=(make_user(id="1", acct="account"),),
    )
    http.get_redirect_url.return_value = "https://elsewhere/nonsense"

    with patch.object(context, "find_post", return_value=None):
        result = context.get_replied_toot_server_id(
            "our.example", toot, http=http, state=state
        )

    assert result is None
    assert "Error parsing toot URL" in caplog.text
    # remembered so the same redirect is not chased again
    assert state.replied_toot_server_ids["https://our.example/@account/1"] is None


def test_context_urls_skip_posts_we_already_host(state, http):
    replied = [("https://remote.example/@a/1", PostRef("remote.example", "1"))]

    with patch.object(context, "get_toot_context",
                      return_value=["https://our.example/@x/1", "https://remote.example/@y/2"]):
        urls = list(context.get_all_context_urls(
            "our.example", replied, http=http, state=state
        ))

    assert urls == ["https://remote.example/@y/2"]


def test_toot_context_comes_from_the_client_for_that_server(state, http):
    client = Mock()
    client.fetch_context_urls.return_value = ["https://remote.example/@a/2"]

    with patch.object(context, "get_server_info", return_value=Mock()), \
         patch.object(context, "client_for", return_value=client):
        urls = context.get_toot_context(
            "remote.example", "1", "https://remote.example/@a/1", http=http, state=state
        )

    assert urls == ["https://remote.example/@a/2"]
    client.fetch_context_urls.assert_called_once_with("1", "https://remote.example/@a/1")


def test_a_server_we_cannot_talk_to_yields_no_context(state, http):
    with patch.object(context, "get_server_info", return_value=Mock()), \
         patch.object(context, "client_for", return_value=None):
        assert context.get_toot_context(
            "remote.example", "1", "url", http=http, state=state
        ) == []
