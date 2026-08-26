import os
import time
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock, patch

import pytest
import requests

from fedifetcher import VERSION
from fedifetcher.config import Config
from fedifetcher.http import (
    BlockedByRobotsError,
    HttpClient,
    RateLimitError,
    RobotsCache,
)

ALLOW_ALL = "User-agent: *\nAllow: /\n"
DENY_ALL = "User-agent: *\nDisallow: /\n"


def make_config(tmp_path, **overrides):
    values = {
        "server": "example.social",
        "access_tokens": ("token",),
        "state_dir": tmp_path,
    }
    values.update(overrides)
    return Config(**values)


def make_client(tmp_path, session=None, robots_text=ALLOW_ALL, **config_overrides):
    config = make_config(tmp_path, **config_overrides)
    robots = RobotsCache(config.state_dir, config.instance_blocklist)
    if robots_text is not None:
        robots._cache["https://example.social/robots.txt"] = robots_text
    return HttpClient(config, session=session or Mock(), robots=robots)


def response(status_code=200, headers=None, text=""):
    resp = Mock()
    resp.status_code = status_code
    resp.headers = headers or {}
    resp.text = text
    return resp


def test_user_agent_names_the_configured_server(tmp_path):
    client = make_client(tmp_path)
    assert client.user_agent == f"FediFetcher/{VERSION}; +example.social (https://go.thms.uk/ff)"


def test_get_sends_our_user_agent(tmp_path):
    session = Mock()
    session.request.return_value = response()
    client = make_client(tmp_path, session=session)

    client.get("https://example.social/api")

    _, kwargs = session.request.call_args
    assert kwargs["headers"]["User-Agent"] == client.user_agent


def test_supplied_headers_win_over_the_default_user_agent(tmp_path):
    session = Mock()
    session.request.return_value = response()
    client = make_client(tmp_path, session=session)

    client.get("https://example.social/api", headers={"User-Agent": "something else"})

    _, kwargs = session.request.call_args
    assert kwargs["headers"]["User-Agent"] == "something else"


def test_get_falls_back_to_the_configured_timeout(tmp_path):
    session = Mock()
    session.request.return_value = response()
    client = make_client(tmp_path, session=session, http_timeout=42)

    client.get("https://example.social/api")

    assert session.request.call_args.kwargs["timeout"] == 42


def test_an_explicit_timeout_wins(tmp_path):
    session = Mock()
    session.request.return_value = response()
    client = make_client(tmp_path, session=session, http_timeout=42)

    client.get("https://example.social/api", timeout=2)

    assert session.request.call_args.kwargs["timeout"] == 2


def test_post_sends_the_body_as_json(tmp_path):
    session = Mock()
    session.request.return_value = response()
    client = make_client(tmp_path, session=session)

    client.post("https://example.social/api", {"key": "value"})

    args, kwargs = session.request.call_args
    assert args[0] == "POST"
    assert kwargs["json"] == {"key": "value"}


def test_requests_the_robots_file_forbids_are_refused(tmp_path):
    session = Mock()
    client = make_client(tmp_path, session=session, robots_text=DENY_ALL)

    with pytest.raises(BlockedByRobotsError):
        client.get("https://example.social/api")

    session.request.assert_not_called()


def test_robots_is_not_consulted_when_ignored(tmp_path):
    session = Mock()
    session.request.return_value = response()
    client = make_client(tmp_path, session=session, robots_text=DENY_ALL)

    client.get("https://example.social/api", ignore_robots_txt=True)

    session.request.assert_called_once()


def test_blocklisted_hosts_are_never_contacted(tmp_path):
    session = Mock()
    client = make_client(
        tmp_path, session=session, instance_blocklist=("example.social",)
    )

    with pytest.raises(BlockedByRobotsError, match="blocklist"):
        client.get("https://example.social/api")

    session.request.assert_not_called()


def test_rate_limited_requests_are_retried_after_the_reset(tmp_path):
    session = Mock()
    reset = (datetime.now(UTC) + timedelta(seconds=1)).isoformat()
    session.request.side_effect = [
        response(429, {"x-ratelimit-reset": reset}),
        response(200),
    ]
    client = make_client(tmp_path, session=session)

    with patch("fedifetcher.http.time.sleep") as sleep:
        result = client.get("https://example.social/api")

    assert result.status_code == 200
    assert session.request.call_count == 2
    assert sleep.call_args[0][0] == pytest.approx(2, abs=1)


def test_rate_limited_requests_back_off_without_a_reset_header(tmp_path):
    session = Mock()
    session.request.side_effect = [response(429), response(429), response(200)]
    client = make_client(tmp_path, session=session)

    with patch("fedifetcher.http.time.sleep") as sleep:
        client.get("https://example.social/api", backoff=0.5)

    assert [call[0][0] for call in sleep.call_args_list] == [0.5, 2.0]


def test_rate_limiting_gives_up_eventually(tmp_path):
    session = Mock()
    session.request.return_value = response(429)
    client = make_client(tmp_path, session=session)

    with patch("fedifetcher.http.time.sleep"), pytest.raises(RateLimitError):
        client.get("https://example.social/api", max_tries=2)

    assert session.request.call_count == 3


def test_robots_is_read_from_the_network_once_then_remembered(tmp_path):
    config = make_config(tmp_path)
    robots = RobotsCache(config.state_dir, config.instance_blocklist)
    fetcher = Mock()
    fetcher.get.return_value = response(200, text=ALLOW_ALL)

    assert robots.fetch("https://example.social/robots.txt", fetcher) == ALLOW_ALL
    assert robots.fetch("https://example.social/robots.txt", fetcher) == ALLOW_ALL

    fetcher.get.assert_called_once()


def test_robots_is_cached_on_disk_between_runs(tmp_path):
    config = make_config(tmp_path)
    fetcher = Mock()
    fetcher.get.return_value = response(200, text=ALLOW_ALL)

    first = RobotsCache(config.state_dir, config.instance_blocklist)
    first.fetch("https://example.social/robots.txt", fetcher)

    second = RobotsCache(config.state_dir, config.instance_blocklist)
    assert second.fetch("https://example.social/robots.txt", fetcher) == ALLOW_ALL
    fetcher.get.assert_called_once()


def test_a_refused_robots_file_denies_everything(tmp_path):
    config = make_config(tmp_path)
    robots = RobotsCache(config.state_dir, config.instance_blocklist)
    fetcher = Mock()
    fetcher.get.return_value = response(403)

    assert robots.fetch("https://example.social/robots.txt", fetcher) is False


def test_an_unreachable_robots_file_allows_everything(tmp_path):
    config = make_config(tmp_path)
    robots = RobotsCache(config.state_dir, config.instance_blocklist)
    fetcher = Mock()
    fetcher.get.side_effect = Exception("no route to host")

    assert robots.fetch("https://example.social/robots.txt", fetcher) is True


def test_stale_robots_files_are_discarded(tmp_path):
    config = make_config(tmp_path)
    robots = RobotsCache(config.state_dir, config.instance_blocklist)
    stale = robots.cache_path("https://old.example/robots.txt")
    fresh = robots.cache_path("https://new.example/robots.txt")
    stale.write_text(ALLOW_ALL, encoding="utf-8")
    fresh.write_text(ALLOW_ALL, encoding="utf-8")
    old = time.time() - timedelta(days=2).total_seconds()
    os.utime(stale, (old, old))
    unrelated = tmp_path / "seen_urls"
    unrelated.write_text("", encoding="utf-8")
    os.utime(unrelated, (old, old))

    assert robots.discard_stale_files(timedelta(days=1)) == 1

    assert not stale.exists()
    assert fresh.exists()
    assert unrelated.exists()


def test_get_redirect_url_returns_the_url_when_it_does_not_redirect(tmp_path):
    session = Mock()
    session.request.return_value = response(200)
    client = make_client(tmp_path, session=session)

    assert client.get_redirect_url("https://example.social/objects/1") == (
        "https://example.social/objects/1"
    )
    assert session.request.call_args.kwargs["allow_redirects"] is False


def test_get_redirect_url_follows_a_302(tmp_path):
    session = Mock()
    session.request.return_value = response(302, {"Location": "/notice/123"})
    client = make_client(tmp_path, session=session)

    assert client.get_redirect_url("https://example.social/objects/1") == "/notice/123"


def test_get_redirect_url_gives_up_on_other_statuses(tmp_path):
    session = Mock()
    session.request.return_value = response(500)
    client = make_client(tmp_path, session=session)

    assert client.get_redirect_url("https://example.social/objects/1") is None


def test_get_redirect_url_gives_up_when_the_request_fails(tmp_path):
    session = Mock()
    session.request.side_effect = requests.exceptions.RequestException
    client = make_client(tmp_path, session=session)

    assert client.get_redirect_url("https://example.social/objects/1") is None


def test_get_redirect_url_sends_our_user_agent(tmp_path):
    session = Mock()
    session.request.return_value = response(200)
    client = make_client(tmp_path, session=session)

    client.get_redirect_url("https://example.social/objects/1")

    assert session.request.call_args.kwargs["headers"]["User-Agent"] == client.user_agent


def test_get_redirect_url_honours_the_blocklist(tmp_path):
    session = Mock()
    client = make_client(
        tmp_path, session=session, instance_blocklist=("example.social",)
    )

    assert client.get_redirect_url("https://example.social/objects/1") is None

    session.request.assert_not_called()


def test_get_redirect_url_honours_robots(tmp_path):
    session = Mock()
    client = make_client(tmp_path, session=session, robots_text=DENY_ALL)

    assert client.get_redirect_url("https://example.social/objects/1") is None

    session.request.assert_not_called()
