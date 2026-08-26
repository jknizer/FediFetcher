import uuid
from unittest.mock import Mock, patch

import pytest

from fedifetcher.config import Config
from fedifetcher.run import Notifier, main
from fedifetcher.store import LockedError


def make_config(tmp_path, **overrides):
    values = {
        "server": "example.social",
        "access_tokens": ("token",),
        "state_dir": tmp_path,
    }
    values.update(overrides)
    return Config(**values)


def test_missing_settings_exit_without_a_traceback(tmp_path):
    assert main(["--server=example.social"]) == 1


def test_a_run_with_nothing_enabled_still_succeeds(tmp_path):
    argv = ["--server=example.social", "--access-token=t", f"--state-dir={tmp_path}"]
    with patch("fedifetcher.run.HttpClient"):
        assert main(argv) == 0


def test_a_successful_run_writes_state_and_releases_the_lock(tmp_path):
    argv = ["--server=example.social", "--access-token=t", f"--state-dir={tmp_path}"]
    with patch("fedifetcher.run.HttpClient"):
        main(argv)

    assert (tmp_path / "seen_urls").exists()
    assert not (tmp_path / "lock.lock").exists()


def test_a_held_lock_stops_the_run(tmp_path):
    argv = ["--server=example.social", "--access-token=t", f"--state-dir={tmp_path}"]
    with patch("fedifetcher.run.HttpClient"), patch(
        "fedifetcher.run.lock", side_effect=LockedError("already running")
    ):
        assert main(argv) == 1


def test_each_token_gets_its_own_run(tmp_path):
    argv = [
        "--server=example.social", "--access-token=one", "--access-token=two",
        f"--state-dir={tmp_path}", "--max-bookmarks=5",
    ]
    with patch("fedifetcher.run.HttpClient"), patch(
        "fedifetcher.run.run_enabled_tasks"
    ) as run_tasks:
        main(argv)

    assert run_tasks.call_count == 2
    tokens = [call.args[0].home._token for call in run_tasks.call_args_list]
    assert tokens == ["one", "two"]


def test_a_failing_task_still_saves_what_it_managed(tmp_path):
    argv = ["--server=example.social", "--access-token=t", f"--state-dir={tmp_path}"]

    def blow_up(ctx):
        ctx.state.seen_urls.add("https://example.social/@a/1")
        raise RuntimeError("network went away")

    with patch("fedifetcher.run.HttpClient"), patch(
        "fedifetcher.run.run_enabled_tasks", side_effect=blow_up
    ), pytest.raises(RuntimeError):
        main(argv)

    assert "https://example.social/@a/1" in (tmp_path / "seen_urls").read_text()
    assert not (tmp_path / "lock.lock").exists()


@pytest.fixture
def notifier(tmp_path):
    http = Mock()
    config = make_config(
        tmp_path, on_start="https://hc.example/start",
        on_done="https://hc.example/done", on_fail="https://hc.example/fail",
    )
    return Notifier(config, http, uuid.UUID(int=1)), http


def test_callbacks_carry_the_run_id(notifier):
    notify, http = notifier
    notify.starting()
    assert "rid=00000000-0000-0000-0000-000000000001" in http.get.call_args[0][0]


def test_the_done_callback_reports_how_long_it_took(notifier):
    notify, http = notifier
    notify.done("all finished")
    url = http.get.call_args[0][0]
    assert "ping=" in url
    assert "msg=all+finished" in url


def test_unset_callbacks_are_not_called(tmp_path):
    http = Mock()
    notify = Notifier(make_config(tmp_path), http, uuid.UUID(int=1))
    notify.starting()
    notify.done("finished")
    notify.failed("broke")
    http.get.assert_not_called()


def test_a_failing_callback_does_not_stop_the_run(notifier):
    notify, http = notifier
    http.get.side_effect = Exception("monitoring is down")
    notify.done("all finished")
