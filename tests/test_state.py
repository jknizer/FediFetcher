import json
from datetime import UTC, datetime, timedelta

from fedifetcher.state import ContextCache, ServerCache, TimestampedSet


def ago(**kwargs):
    return datetime.now(UTC) - timedelta(**kwargs)


def test_timestamped_set_from_plain_iterable():
    seen = TimestampedSet(["a", "b"])
    assert "a" in seen
    assert len(seen) == 2
    assert list(seen) == ["a", "b"]


def test_timestamped_set_preserves_insertion_order():
    seen = TimestampedSet(["c", "a", "b"])
    seen.add("d")
    assert list(seen) == ["c", "a", "b", "d"]


def test_timestamped_set_keeps_first_timestamp():
    original = ago(hours=5)
    seen = TimestampedSet([])
    seen.add("a", original)
    seen.add("a")
    assert seen.get("a") == original


def test_timestamped_set_parses_stored_timestamps():
    when = ago(hours=2)
    seen = TimestampedSet({"a": when.isoformat()})
    assert seen.get("a") == when


def test_timestamped_set_update_adds_many():
    seen = TimestampedSet([])
    seen.update(["a", "b"])
    assert len(seen) == 2


def test_timestamped_set_expiry_drops_only_the_old():
    seen = TimestampedSet([])
    seen.add("old", ago(hours=48))
    seen.add("fresh", ago(hours=1))

    assert seen.expire_older_than(timedelta(hours=24)) == 1

    assert "old" not in seen
    assert "fresh" in seen


def test_timestamped_set_roundtrips_through_json():
    when = ago(hours=3)
    seen = TimestampedSet([])
    seen.add("a", when)

    restored = TimestampedSet(json.loads(seen.toJSON()))

    assert restored.get("a") == when


def server(**overrides):
    entry = {"peertubeApiSupport": False, "last_checked": ago(days=1)}
    entry.update(overrides)
    return entry


def test_server_cache_add_and_get():
    hosts = ServerCache({})
    hosts.add("example.org", server())
    assert "example.org" in hosts
    assert hosts.get("example.org")["peertubeApiSupport"] is False
    assert len(hosts) == 1


def test_server_cache_parses_stored_last_checked():
    when = ago(days=2)
    hosts = ServerCache({"example.org": {"last_checked": when.isoformat()}})
    assert hosts.get("example.org")["last_checked"] == when


def test_server_cache_expires_stale_hosts():
    hosts = ServerCache({})
    hosts.add("stale.org", server(last_checked=ago(days=40)))
    hosts.add("fresh.org", server(last_checked=ago(days=1)))

    assert hosts.expire(timedelta(days=30), timedelta(hours=1)) == 1

    assert "stale.org" not in hosts
    assert "fresh.org" in hosts


def test_server_cache_expires_failures_sooner_than_successes():
    hosts = ServerCache({})
    hosts.add("failed.org", server(info=None, last_checked=ago(hours=2)))
    hosts.add("worked.org", server(last_checked=ago(hours=2)))

    assert hosts.expire(timedelta(days=30), timedelta(hours=1)) == 1

    assert "failed.org" not in hosts
    assert "worked.org" in hosts


def test_server_cache_drops_entries_predating_peertube_support():
    hosts = ServerCache({})
    hosts.add("old-schema.org", {"last_checked": ago(hours=1)})

    assert hosts.expire(timedelta(days=30), timedelta(hours=1)) == 1

    assert "old-schema.org" not in hosts


def test_server_cache_keeps_entries_without_last_checked():
    hosts = ServerCache({})
    hosts.add("unknown-age.org", {"peertubeApiSupport": True})

    assert hosts.expire(timedelta(days=30), timedelta(hours=1)) == 0

    assert "unknown-age.org" in hosts


def test_server_cache_is_iterable():
    hosts = ServerCache({})
    hosts.add("a.org", server())
    hosts.add("b.org", server())
    assert list(hosts) == ["a.org", "b.org"]


def test_server_cache_roundtrips_through_json():
    when = ago(days=1)
    hosts = ServerCache({})
    hosts.add("example.org", server(last_checked=when))

    restored = ServerCache(json.loads(hosts.toJSON()))

    assert restored.get("example.org")["last_checked"] == when


def test_context_is_fetched_the_first_time():
    cache = ContextCache()
    assert cache.should_fetch("uri", ago(hours=1))


def test_context_is_not_refetched_immediately():
    cache = ContextCache()
    created = ago(hours=1)
    cache.mark_fetched("uri", created)
    assert not cache.should_fetch("uri", created)


def test_a_brand_new_post_is_rechecked_after_a_minute():
    cache = ContextCache({"uri": {"created_at": ago(minutes=30), "lastSeen": ago(minutes=2)}})
    assert cache.should_fetch("uri", ago(minutes=30))


def test_a_post_from_today_waits_ten_minutes():
    cache = ContextCache({"uri": {"created_at": ago(hours=5), "lastSeen": ago(minutes=2)}})
    assert not cache.should_fetch("uri", ago(hours=5))

    cache = ContextCache({"uri": {"created_at": ago(hours=5), "lastSeen": ago(minutes=20)}})
    assert cache.should_fetch("uri", ago(hours=5))


def test_an_old_post_waits_an_hour():
    cache = ContextCache({"uri": {"created_at": ago(days=5), "lastSeen": ago(minutes=30)}})
    assert not cache.should_fetch("uri", ago(days=5))

    cache = ContextCache({"uri": {"created_at": ago(days=5), "lastSeen": ago(hours=2)}})
    assert cache.should_fetch("uri", ago(days=5))


def test_should_fetch_does_not_record_anything():
    """Asking the question must not change the answer next time"""
    cache = ContextCache()
    assert cache.should_fetch("uri", ago(days=1))
    assert "uri" not in cache
    assert cache.should_fetch("uri", ago(days=1))


def test_context_entries_expire():
    cache = ContextCache({
        "old": {"created_at": ago(days=30), "lastSeen": ago(days=30)},
        "fresh": {"created_at": ago(days=30), "lastSeen": ago(hours=1)},
    })

    assert cache.expire_older_than(timedelta(days=7)) == 1

    assert "old" not in cache
    assert "fresh" in cache


def test_context_entries_survive_a_round_trip():
    cache = ContextCache()
    cache.mark_fetched("uri", ago(days=1))

    restored = ContextCache(json.loads(cache.toJSON()))

    assert not restored.should_fetch("uri", ago(days=1))


def test_context_entries_from_older_versions_are_read():
    """Older releases stored the whole post; only two of its fields matter"""
    cache = ContextCache({
        "uri": {
            "created_at": ago(days=1).isoformat(),
            "lastSeen": ago(minutes=1).isoformat(),
            "content": "<p>a whole toot</p>",
            "account": {"acct": "someone"},
        }
    })
    assert not cache.should_fetch("uri", ago(days=1))


def test_context_entries_that_were_never_completed_are_ignored():
    assert len(ContextCache({"uri": {"created_at": ago(days=1)}})) == 0
