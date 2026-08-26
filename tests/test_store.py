from datetime import UTC, datetime, timedelta

import pytest

from fedifetcher.config import Config
from fedifetcher.store import STATE_VERSION, LockedError, State, StateStore, lock


def make_config(tmp_path, **overrides):
    values = {
        "server": "example.social",
        "access_tokens": ("token",),
        "state_dir": tmp_path,
    }
    values.update(overrides)
    return Config(**values)


@pytest.fixture
def store(tmp_path):
    return StateStore(make_config(tmp_path))


def test_an_empty_state_directory_loads_as_empty(store):
    state = store.load()
    assert len(state.seen_urls) == 0
    assert len(state.known_followings) == 0
    assert state.replied_toot_server_ids == {}


def test_what_is_saved_is_loaded_again(store):
    state = store.load()
    state.seen_urls.add("https://example.social/@a/1")
    state.known_followings.add("someone@example.social")
    state.replied_toot_server_ids["url"] = ["a", "b"]

    store.save(state)
    restored = store.load()

    assert "https://example.social/@a/1" in restored.seen_urls
    assert "someone@example.social" in restored.known_followings
    assert restored.replied_toot_server_ids == {"url": ["a", "b"]}


def test_all_known_users_combines_both_sets(store):
    state = store.load()
    state.known_followings.add("a@example.social")
    state.recently_checked_users.add("b@example.social")
    store.save(state)

    restored = store.load()

    assert "a@example.social" in restored.all_known_users
    assert "b@example.social" in restored.all_known_users


def test_stale_users_are_dropped_on_load(tmp_path):
    store = StateStore(make_config(tmp_path, remember_users_for_hours=24))
    state = store.load()
    state.recently_checked_users.add("old@example.social", datetime.now(UTC) - timedelta(days=3))
    state.recently_checked_users.add("new@example.social", datetime.now(UTC))
    store.save(state)

    restored = store.load()

    assert "old@example.social" not in restored.recently_checked_users
    assert "new@example.social" in restored.recently_checked_users


def test_only_the_most_recent_entries_are_carried_over(tmp_path, monkeypatch):
    monkeypatch.setattr("fedifetcher.store.MAX_ENTRIES", 3)
    store = StateStore(make_config(tmp_path))
    state = store.load()
    for i in range(10):
        state.seen_urls.add(f"https://example.social/@a/{i}")

    store.save(state)

    assert len(store.load().seen_urls) == 3


def test_a_state_directory_without_a_version_is_read_as_version_one(store):
    assert store.stored_version() == 1


def test_saving_records_the_version(store):
    store.save(store.load())
    assert store.version_file.read_text(encoding="utf-8") == str(STATE_VERSION)
    assert store.stored_version() == STATE_VERSION


def test_a_newer_state_directory_is_read_anyway(store, caplog):
    store.version_file.parent.mkdir(parents=True, exist_ok=True)
    store.version_file.write_text("99", encoding="utf-8")

    store.load()

    assert "version 99" in caplog.text


def test_unreadable_state_files_are_treated_as_empty(tmp_path):
    config = make_config(tmp_path)
    config.seen_hosts_file.write_text("{not json", encoding="utf-8")
    assert len(StateStore(config).load().seen_hosts) == 0


def test_a_session_saves_on_the_way_out(store):
    with store.session() as state:
        state.seen_urls.add("https://example.social/@a/1")

    assert "https://example.social/@a/1" in store.load().seen_urls


def test_a_session_saves_even_when_the_run_fails(store):
    """A long run that dies part way through must not throw away its progress"""
    with pytest.raises(RuntimeError), store.session() as state:
        state.seen_urls.add("https://example.social/@a/1")
        raise RuntimeError("network went away")

    assert "https://example.social/@a/1" in store.load().seen_urls


def test_the_lock_is_held_for_the_duration_and_released_after(tmp_path):
    config = make_config(tmp_path)
    with lock(config):
        assert config.lock_path.exists()
    assert not config.lock_path.exists()


def test_the_lock_is_released_when_the_run_fails(tmp_path):
    config = make_config(tmp_path)
    with pytest.raises(RuntimeError), lock(config):
        raise RuntimeError("network went away")
    assert not config.lock_path.exists()


def test_a_second_run_is_refused_while_the_lock_is_held(tmp_path):
    config = make_config(tmp_path)
    with lock(config), pytest.raises(LockedError, match="Lock file age"):
        with lock(config):
            pass


def test_an_expired_lock_is_taken_over(tmp_path):
    config = make_config(tmp_path, lock_hours=1)
    config.lock_path.write_text(str(datetime.now() - timedelta(hours=2)), encoding="utf-8")

    with lock(config):
        pass

    assert not config.lock_path.exists()


def test_an_unreadable_lock_stops_the_run(tmp_path):
    config = make_config(tmp_path)
    config.lock_path.write_text("not a date", encoding="utf-8")

    with pytest.raises(LockedError, match="Cannot read logfile age"):
        with lock(config):
            pass


def test_the_state_directory_is_created_if_missing(tmp_path):
    store = StateStore(make_config(tmp_path / "fresh"))
    store.save(State())
    assert (tmp_path / "fresh" / "seen_urls").exists()
