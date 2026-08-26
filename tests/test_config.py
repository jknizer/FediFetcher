import json
from pathlib import Path

import pytest

from fedifetcher.config import Config, ConfigError

REQUIRED = ["--server=example.social", "--access-token=token"]


def load(*argv, env=None):
    return Config.load([*REQUIRED, *argv], environ=env or {})


def test_defaults_apply_when_nothing_is_given():
    config = load()
    assert config.server == "example.social"
    assert config.access_tokens == ("token",)
    assert config.home_timeline_length == 0
    assert config.remember_users_for_hours == 24 * 7
    # one page of posts per account, as it was before it could be asked for more
    assert config.max_posts_per_account == 40


def test_server_given_as_url_is_reduced_to_a_hostname():
    assert Config.load(["--server=https://example.social/", "--access-token=t"], environ={}).server == "example.social"


def test_access_token_can_be_given_more_than_once():
    config = Config.load(
        ["--server=example.social", "--access-token=one", "--access-token=two"],
        environ={},
    )
    assert config.access_tokens == ("one", "two")


def test_missing_server_is_rejected():
    with pytest.raises(ConfigError):
        Config.load(["--access-token=token"], environ={})


def test_missing_access_token_is_rejected():
    with pytest.raises(ConfigError):
        Config.load(["--server=example.social"], environ={})


def test_config_is_frozen():
    config = load()
    with pytest.raises(Exception):
        config.server = "somewhere.else"


def test_environment_is_read_with_its_prefix_stripped():
    config = load(env={"FF_HOME_TIMELINE_LENGTH": "200"})
    assert config.home_timeline_length == 200


def test_environment_ignores_unknown_names():
    config = load(env={"FF_NOT_AN_OPTION": "1", "HOME_TIMELINE_LENGTH": "5"})
    assert config.home_timeline_length == 0


def test_access_tokens_come_from_numbered_environment_variables():
    config = Config.load(
        ["--server=example.social"],
        environ={"FF_ACCESS_TOKEN_1": "one", "FF_ACCESS_TOKEN_2": "two"},
    )
    assert sorted(config.access_tokens) == ["one", "two"]


def write_config(tmp_path, **values):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(values), encoding="utf-8")
    return str(path)


def test_config_file_is_read(tmp_path):
    path = write_config(tmp_path, server="from.file", **{"access-token": ["t"]})
    config = Config.load([f"--config={path}"], environ={})
    assert config.server == "from.file"


def test_config_file_keys_may_use_dashes(tmp_path):
    path = write_config(tmp_path, **{"home-timeline-length": 50})
    config = Config.load([f"--config={path}", *REQUIRED], environ={})
    assert config.home_timeline_length == 50


def test_missing_config_file_is_rejected():
    with pytest.raises(ConfigError):
        Config.load(["--config=/nonexistent/config.json"], environ={})


def test_environment_beats_the_config_file(tmp_path):
    path = write_config(tmp_path, **{"home-timeline-length": 50})
    config = Config.load(
        [f"--config={path}", *REQUIRED],
        environ={"FF_HOME_TIMELINE_LENGTH": "200"},
    )
    assert config.home_timeline_length == 200


def test_command_line_beats_the_environment():
    config = load("--home-timeline-length=300", env={"FF_HOME_TIMELINE_LENGTH": "200"})
    assert config.home_timeline_length == 300


def test_command_line_beats_the_config_file(tmp_path):
    path = write_config(tmp_path, **{"home-timeline-length": 50})
    config = Config.load(
        [f"--config={path}", *REQUIRED, "--home-timeline-length=300"], environ={}
    )
    assert config.home_timeline_length == 300


@pytest.mark.parametrize("given", ["0", "false", "no", "off"])
def test_flags_can_be_switched_off(given):
    assert load(f"--from-lists={given}").from_lists is False


@pytest.mark.parametrize("given", ["1", "true", "yes", "on"])
def test_flags_can_be_switched_on(given):
    assert load(f"--from-lists={given}").from_lists is True


def test_flags_reject_nonsense():
    with pytest.raises(ConfigError, match="from_lists"):
        load("--from-lists=perhaps")


def test_numbers_reject_nonsense_from_any_source():
    with pytest.raises(ConfigError, match="home_timeline_length"):
        load("--home-timeline-length=lots")
    with pytest.raises(ConfigError, match="home_timeline_length"):
        load(env={"FF_HOME_TIMELINE_LENGTH": "lots"})


def test_flags_from_the_environment_accept_numbers():
    assert load(env={"FF_BACKFILL_WITH_CONTEXT": "0"}).backfill_with_context is False


def test_flags_from_the_config_file_accept_json_booleans(tmp_path):
    path = write_config(tmp_path, **{"backfill-with-context": False})
    config = Config.load([f"--config={path}", *REQUIRED], environ={})
    assert config.backfill_with_context is False


def test_blocklist_is_split_on_commas():
    config = load("--instance-blocklist=one.example, two.example")
    assert config.instance_blocklist == ("one.example", "two.example")


def test_empty_blocklist_is_empty():
    assert load("--instance-blocklist=").instance_blocklist == ()


def test_paths_are_paths():
    config = load("--state-dir=/var/lib/ff")
    assert config.state_dir == Path("/var/lib/ff")


def test_state_files_live_under_the_state_dir():
    config = load("--state-dir=/var/lib/ff")
    assert config.seen_urls_file == Path("/var/lib/ff/seen_urls")
    assert config.lock_path == Path("/var/lib/ff/lock.lock")


def test_state_dir_defaults_to_artifacts_in_a_checkout(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "artifacts").mkdir()
    assert load().state_dir == Path("artifacts")


def test_state_dir_defaults_below_xdg_state_home_when_installed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("XDG_STATE_HOME", "/xdg/state")
    assert load().state_dir == Path("/xdg/state/fedifetcher")


def test_state_dir_falls_back_to_a_dot_local_path_without_xdg_state_home(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert load().state_dir == tmp_path / "home/.local/state/fedifetcher"


def test_an_explicit_state_dir_beats_the_default(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "artifacts").mkdir()
    assert load("--state-dir=/var/lib/ff").state_dir == Path("/var/lib/ff")


def test_an_explicit_lock_file_wins():
    config = load("--state-dir=/var/lib/ff", "--lock-file=/tmp/other.lock")
    assert config.lock_path == Path("/tmp/other.lock")
