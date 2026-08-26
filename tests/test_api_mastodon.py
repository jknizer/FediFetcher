from datetime import UTC, datetime
from typing import TypeVar

import pytest

from fedifetcher.api.mastodon import MastodonApi, to_list, to_post, to_user
from fedifetcher.servers import ApiFlavour


@pytest.fixture
def api(http):
    return MastodonApi("example.social", http)

WHEN = "2026-01-01T00:00:00.000Z"


T = TypeVar("T")


def built(thing: T | None) -> T:
    """The builders drop what they cannot use; these tests are about the rest"""
    assert thing is not None
    return thing


def test_it_claims_the_mastodon_flavour():
    assert MastodonApi.flavour is ApiFlavour.MASTODON


def test_user_id_is_looked_up_by_name(api, http, reply):
    http.get.return_value = reply(200, {"id": "1234"})

    assert api.user_id("someone") == "1234"

    assert http.get.call_args[0][0] == (
        "https://example.social/api/v1/accounts/lookup?acct=someone"
    )


def test_user_id_falls_back_to_the_token_owner(api, http, reply):
    http.get.return_value = reply(200, {"id": "1234"})

    assert api.user_id(access_token="token") == "1234"

    assert http.get.call_args[0][0] == (
        "https://example.social/api/v1/accounts/verify_credentials"
    )
    assert http.get.call_args.kwargs["headers"]["Authorization"] == "Bearer token"


def test_user_id_needs_something_to_go_on(api):
    with pytest.raises(Exception, match="user name or an access token"):
        api.user_id()


def test_user_id_reports_an_unknown_user(api, http, reply):
    http.get.return_value = reply(404)
    with pytest.raises(Exception, match="was not found"):
        api.user_id("nobody")


def test_user_id_reports_other_failures(api, http, reply):
    http.get.return_value = reply(500)
    with pytest.raises(Exception, match="Status code: 500"):
        api.user_id("someone")


def test_user_posts_are_fetched_for_the_resolved_id(api, http, reply):
    http.get.side_effect = [
        reply(200, {"id": "1234"}),
        reply(200, [{"url": "https://example.social/@someone/1",
                    "created_at": "2026-01-01T00:00:00.000Z"}]),
    ]

    posts = api.fetch_user_posts("someone", "https://example.social/@someone", 40)

    assert [post.url for post in posts] == ["https://example.social/@someone/1"]
    assert http.get.call_args[0][0] == (
        "https://example.social/api/v1/accounts/1234/statuses?exclude_replies=false&limit=40"
    )


def test_more_posts_than_a_page_are_asked_for_from_the_last_one(api, http, reply):
    def status(n):
        return {"id": str(n), "url": f"https://example.social/@someone/{n}",
                "created_at": WHEN}

    http.get.side_effect = [
        reply(200, {"id": "1234"}),
        reply(200, [status(n) for n in range(40)]),
        reply(200, [status(40)]),
    ]

    posts = api.fetch_user_posts("someone", "https://example.social/@someone", 100)

    assert len(posts) == 41
    assert http.get.call_args_list[2][0][0] == (
        "https://example.social/api/v1/accounts/1234/statuses?exclude_replies=false&limit=40&max_id=39"
    )


def test_replies_are_requested_when_fetching_user_posts(api, http, reply):
    """Mitra excludes replies unless asked; Mastodon and its kin include them anyway"""
    http.get.side_effect = [
        reply(200, {"id": "1234"}),
        reply(200, []),
    ]

    api.fetch_user_posts("someone", "https://example.social/@someone", 40)

    assert "exclude_replies=false" in http.get.call_args[0][0]


def test_posts_from_a_page_we_cannot_read_are_kept(api, http, reply):
    http.get.side_effect = [
        reply(200, {"id": "1234"}),
        reply(200, [{"id": str(n), "url": f"https://example.social/@someone/{n}",
                     "created_at": WHEN} for n in range(40)]),
        reply(500),
    ]

    posts = api.fetch_user_posts("someone", "https://example.social/@someone", 100)

    assert len(posts) == 40


def test_user_posts_give_up_when_the_id_cannot_be_found(api, http, reply):
    http.get.return_value = reply(404)
    assert api.fetch_user_posts("nobody", "https://example.social/@nobody", 40) is None


def test_user_posts_give_up_on_an_error_status(api, http, reply):
    http.get.side_effect = [reply(200, {"id": "1234"}), reply(500)]
    assert api.fetch_user_posts("someone", "https://example.social/@someone", 40) is None


def test_context_gathers_ancestors_and_descendants(api, http, reply):
    http.get.return_value = reply(200, {
        "ancestors": [{"url": "https://example.social/@a/1"}],
        "descendants": [{"url": "https://example.social/@b/2"}],
    })

    urls = api.fetch_context_urls("9", "https://example.social/@a/9")

    assert urls == ["https://example.social/@a/1", "https://example.social/@b/2"]
    assert http.get.call_args[0][0] == (
        "https://example.social/api/v1/statuses/9/context"
    )


def test_context_is_empty_on_an_error_status(api, http, reply):
    http.get.return_value = reply(500)
    assert api.fetch_context_urls("9", "https://example.social/@a/9") == []


def test_context_is_empty_when_the_request_fails(api, http):
    http.get.side_effect = Exception("no route to host")
    assert api.fetch_context_urls("9", "https://example.social/@a/9") == []


def test_context_is_empty_when_the_body_makes_no_sense(api, http, reply):
    http.get.return_value = reply(200, {"unexpected": True})
    assert api.fetch_context_urls("9", "https://example.social/@a/9") == []


def test_a_mastodon_status_keeps_what_it_was_given():
    post = built(to_post({
        "url": "https://remote.example/@a/1",
        "uri": "https://remote.example/users/a/statuses/1",
        "created_at": WHEN,
        "visibility": "public",
        "in_reply_to_id": "7",
        "replies_count": 2,
    }))

    assert post.url == "https://remote.example/@a/1"
    assert post.uri == "https://remote.example/users/a/statuses/1"
    assert post.created_at == datetime(2026, 1, 1, tzinfo=UTC)
    assert post.is_public
    assert post.in_reply_to_id == "7"
    assert post.reply_count == 2


@pytest.mark.parametrize(
    "visibility,expected",
    [("public", True), ("unlisted", True), ("private", False), ("direct", False)],
)
def test_only_posts_anyone_may_read_are_public(visibility, expected):
    post = built(to_post({"url": "u", "created_at": WHEN, "visibility": visibility}))
    assert post.is_public is expected


def test_a_status_that_says_nothing_about_who_may_see_it_is_not_public():
    assert built(to_post({"url": "u", "created_at": WHEN})).is_public is False


def test_a_boost_carries_the_post_it_boosts():
    post = built(to_post({
        "url": "https://our.example/@a/1", "created_at": WHEN, "visibility": "public",
        "reblog": {"url": "https://remote.example/@b/9", "created_at": WHEN,
                   "visibility": "public"},
    }))

    assert post.is_boost
    assert post.original.url == "https://remote.example/@b/9"


def test_a_boost_with_no_url_of_its_own_borrows_the_one_it_boosts():
    post = built(to_post({
        "uri": "https://our.example/users/a/statuses/1/activity", "url": None,
        "created_at": WHEN, "visibility": "public",
        "reblog": {"url": "https://remote.example/@b/9", "created_at": WHEN,
                   "visibility": "public", "replies_count": 3},
    }))

    assert post.is_boost
    assert post.url == "https://remote.example/@b/9"
    assert post.uri == "https://our.example/users/a/statuses/1/activity"
    assert post.original.reply_count == 3


@pytest.mark.parametrize(
    "raw",
    [
        {"created_at": WHEN},                    # nothing to address it by
        {"url": "u"},                            # nothing to date it by
        {"url": "u", "created_at": "not a date"},
        {"url": "u", "created_at": None},
    ],
)
def test_a_post_we_could_not_use_is_dropped(raw):
    assert to_post(raw) is None


def test_dropping_a_post_is_worth_saying_out_loud(caplog):
    with caplog.at_level("DEBUG"):
        to_post({"uri": "https://remote.example/1"})

    assert "https://remote.example/1" in caplog.text


def test_a_date_we_already_understand_is_left_alone():
    when = datetime(2026, 6, 1, tzinfo=UTC)
    assert built(to_post({"url": "u", "created_at": when})).created_at == when


def account(**overrides):
    raw = {"acct": "someone@remote.example", "url": "https://remote.example/@someone"}
    return {**raw, **overrides}


def test_an_account_keeps_what_it_was_given():
    user = built(to_user(account(note="hello", indexable=False, discoverable=False)))

    assert user.acct == "someone@remote.example"
    assert user.url == "https://remote.example/@someone"
    assert user.note == "hello"
    assert not user.indexable
    assert not user.discoverable


def test_an_account_that_said_nothing_is_taken_to_have_agreed():
    user = built(to_user(account()))

    assert user.note == ""
    assert user.indexable
    assert user.discoverable


def test_a_mention_is_an_account_too():
    mention = built(to_user(
        {"id": "1", "username": "someone", "acct": "someone@remote.example",
         "url": "https://remote.example/@someone"}
    ))

    assert mention.acct == "someone@remote.example"


def test_a_note_that_is_not_text_is_treated_as_no_note():
    assert built(to_user(account(note=None))).note == ""


@pytest.mark.parametrize("missing", ["acct", "url"])
def test_an_account_we_cannot_name_or_reach_is_dropped(missing):
    raw = account()
    del raw[missing]

    assert to_user(raw) is None


def test_a_status_carries_who_wrote_it_and_who_it_names():
    post = built(to_post({
        "url": "u", "created_at": WHEN,
        "in_reply_to_id": "7", "in_reply_to_account_id": "99",
        "account": account(acct="author@remote.example"),
        "mentions": [account(acct="mentioned@remote.example", id="99")],
    }))

    assert built(post.account).acct == "author@remote.example"
    assert [m.acct for m in post.mentions] == ["mentioned@remote.example"]
    # how a reply is matched to the account it answers
    assert post.mentions[0].id == post.in_reply_to_account_id


def test_a_status_that_names_nobody_carries_nobody():
    post = built(to_post({"url": "u", "created_at": WHEN}))

    assert post.account is None
    assert post.mentions == ()


def test_a_mention_we_cannot_use_is_dropped_without_losing_the_post():
    post = built(to_post({
        "url": "u", "created_at": WHEN,
        "mentions": [{"id": "1"}, account(acct="fine@remote.example")],
    }))

    assert [m.acct for m in post.mentions] == ["fine@remote.example"]


def test_a_list_is_its_id_and_what_the_owner_called_it():
    user_list = built(to_list({"id": "42", "title": "Friends"}))

    assert user_list.id == "42"
    assert user_list.title == "Friends"


def test_a_list_without_a_name_is_still_a_list_we_can_read():
    assert built(to_list({"id": "42"})).title == ""


def test_a_list_we_cannot_address_is_dropped():
    assert to_list({"title": "Friends"}) is None


@pytest.mark.parametrize(
    "profile_url,username",
    [
        ("https://mstdn.example/@someone", "someone"),          # mastodon and kin
        ("https://pleroma.example/users/someone", "someone"),   # pleroma, akkoma
        ("https://pixelfed.example/someone", "someone"),        # no prefix at all
        ("https://pixelfed.example/someone/", "someone"),
        # a post URL shares the prefix, and its author is the right answer
        ("https://mstdn.example/@someone/12345", "someone"),
    ],
)
def test_the_names_this_api_uses_for_an_account(api, profile_url, username):
    assert api.username_from(profile_url) == username


@pytest.mark.parametrize(
    "profile_url",
    [
        "http://mstdn.example/@someone",
        "not a url",
    ],
)
def test_a_profile_url_this_api_does_not_use(api, profile_url):
    assert api.username_from(profile_url) is None


@pytest.mark.parametrize(
    "post_url,post_id",
    [
        ("https://mstdn.example/@someone/12345", "12345"),
        ("https://mstdn.example/users/someone/statuses/12345", "12345"),
        ("https://pleroma.example/notice/12345", "12345"),          # pleroma, akkoma
        ("https://pixelfed.example/p/someone/12345", "12345"),      # speaks this API
    ],
)
def test_the_ways_this_api_addresses_a_post(api, post_url, post_id):
    assert api.post_id_from(post_url) == post_id


def test_a_pleroma_object_is_resolved_by_following_its_redirect(api, http):
    http.get_redirect_url.return_value = "/notice/12345"

    assert api.post_id_from("https://pleroma.example/objects/abc-def") == "12345"
    http.get_redirect_url.assert_called_once_with("https://pleroma.example/objects/abc-def")


def test_an_object_whose_redirect_goes_nowhere_cannot_be_read(api, http):
    http.get_redirect_url.return_value = None
    assert api.post_id_from("https://pleroma.example/objects/abc-def") is None


def test_an_object_that_redirects_somewhere_unexpected_cannot_be_read(api, http):
    http.get_redirect_url.return_value = "/somewhere/else"
    assert api.post_id_from("https://pleroma.example/objects/abc-def") is None


def test_a_post_url_this_api_does_not_use(api):
    assert api.post_id_from("https://mstdn.example/notes/12345") is None


def test_a_mitra_object_is_resolved_by_following_its_redirect(api, http):
    http.get_redirect_url.return_value = "https://mitra.example/post/019ff65d-fa3d-76e2"

    assert api.post_id_from("https://mitra.example/objects/abc-def") == "019ff65d-fa3d-76e2"
    http.get_redirect_url.assert_called_once_with("https://mitra.example/objects/abc-def")
