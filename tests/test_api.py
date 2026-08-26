from datetime import datetime
from unittest.mock import patch

import pytest

from fedifetcher.api import (
    CLIENTS,
    FediverseApi,
    LemmyApi,
    MastodonApi,
    client_for,
    find_post,
)
from fedifetcher.servers import ApiFlavour, ServerInfo
from fedifetcher.state import ServerCache
from fedifetcher.urls import PostRef


def server(software):
    return ServerInfo.from_nodeinfo(
        "example.social",
        {"protocols": ["activitypub"], "software": {"name": software, "version": "1"}},
    )


@pytest.mark.parametrize(
    "software,expected",
    [
        ("mastodon", MastodonApi),
        ("lemmy", LemmyApi),
    ],
)
def test_a_client_is_chosen_by_what_the_server_speaks(software, expected, http):
    assert isinstance(client_for(server(software), http), expected)


def test_a_server_we_cannot_talk_to_gets_no_client(http):
    assert client_for(server("something-new"), http) is None


def test_the_chosen_client_is_bound_to_the_web_domain(http):
    client = client_for(server("mastodon"), http)
    assert isinstance(client, MastodonApi)
    assert client.webserver == "example.social"


def test_mastodon_wins_when_a_server_claims_more_than_one_api(http):
    info = ServerInfo(
        webserver="example.social",
        software="hybrid",
        version="1",
        apis=frozenset({ApiFlavour.MISSKEY, ApiFlavour.MASTODON}),
        last_checked=datetime.now(),
    )
    assert isinstance(client_for(info, http), MastodonApi)


def test_every_client_satisfies_the_protocol(http):
    for client in CLIENTS:
        assert isinstance(client("example.social", http), FediverseApi)  # type: ignore[call-arg]


def test_every_flavour_has_a_client():
    assert {client.flavour for client in CLIENTS} == set(ApiFlavour)


@pytest.fixture
def looked_up(http):
    """find_post against a server that says what it runs, with nothing cached"""
    def look_up(url, software="mastodon"):
        with patch("fedifetcher.api.get_server_info", return_value=server(software)):
            return find_post(url, {}, ServerCache(), http)
    return look_up


def test_a_post_url_is_read_by_the_software_that_serves_it(looked_up):
    assert looked_up("https://example.social/@someone/12345") == (
        "example.social", "12345"
    )


def test_the_same_url_means_different_things_on_different_software(looked_up):
    """/notes/ is a Misskey post; on a Mastodon server it is nothing"""
    url = "https://example.social/notes/abc123"

    assert looked_up(url, "misskey") == ("example.social", "abc123")
    assert looked_up(url, "mastodon") is None


def test_a_url_with_no_host_is_not_looked_up(http):
    assert find_post("not a url", {}, ServerCache(), http) is None


def test_a_server_that_cannot_be_reached_leaves_the_post_unread(http):
    with patch("fedifetcher.api.get_server_info", return_value=None):
        assert find_post("https://example.social/@a/1", {}, ServerCache(), http) is None


def test_a_server_we_cannot_talk_to_leaves_the_post_unread(http):
    with patch("fedifetcher.api.get_server_info", return_value=server("something-new")):
        assert find_post("https://example.social/@a/1", {}, ServerCache(), http) is None


def test_an_answer_is_remembered_rather_than_asked_for_twice(http):
    parsed: dict[str, PostRef | None] = {}
    with patch("fedifetcher.api.get_server_info", return_value=server("mastodon")) as ask:
        find_post("https://example.social/@a/1", parsed, ServerCache(), http)
        find_post("https://example.social/@a/1", parsed, ServerCache(), http)

    assert ask.call_count == 1
    assert parsed == {"https://example.social/@a/1": ("example.social", "1")}


def test_a_url_we_could_not_read_is_remembered_as_such(http, caplog):
    parsed: dict[str, PostRef | None] = {}
    with patch("fedifetcher.api.get_server_info", return_value=None):
        find_post("https://example.social/nothing", parsed, ServerCache(), http)

    assert parsed == {"https://example.social/nothing": None}
    assert "Error parsing toot URL" in caplog.text
