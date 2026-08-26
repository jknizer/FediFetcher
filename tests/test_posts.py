from datetime import UTC, datetime

import pytest

from fedifetcher.posts import Post

WHEN = datetime(2026, 1, 1, tzinfo=UTC)


def post(**overrides):
    fields = {"url": "https://remote.example/@a/1", "uri": "https://remote.example/@a/1",
              "created_at": WHEN, "is_public": True}
    return Post(**{**fields, **overrides})


def test_a_post_that_is_not_a_boost_is_its_own_original():
    plain = post()

    assert not plain.is_boost
    assert plain.original is plain


def test_a_boost_points_at_the_post_it_boosts():
    original = post(url="https://remote.example/@b/9")
    boost = post(reblog=original)

    assert boost.is_boost
    assert boost.original is original


def test_a_post_the_server_said_nothing_about_may_have_no_context():
    assert not post().may_have_context


@pytest.mark.parametrize(
    "said", [{"in_reply_to_id": "7"}, {"reply_count": 2}, {"reply_count": 0}]
)
def test_a_post_the_server_said_anything_about_may_have_context(said):
    assert post(**said).may_have_context


def test_a_post_cannot_be_changed_once_it_has_been_read():
    with pytest.raises(AttributeError):
        post().url = "https://somewhere.else/1"
