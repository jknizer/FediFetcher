import pytest

from fedifetcher.api.misskey import MisskeyApi, to_post
from fedifetcher.posts import Post
from fedifetcher.servers import ApiFlavour


@pytest.fixture
def api(http):
    return MisskeyApi("misskey.example", http)

WHEN = "2026-01-01T00:00:00.000Z"


def built(post: Post | None) -> Post:
    """The builder drops what it cannot use; these tests are about the rest"""
    assert post is not None
    return post


def test_it_claims_the_misskey_flavour():
    assert MisskeyApi.flavour is ApiFlavour.MISSKEY


def test_user_posts_are_fetched_for_the_local_account(api, http, reply):
    http.post.side_effect = [
        reply(200, [{"id": "remote", "host": "elsewhere"}, {"id": "local", "host": None}]),
        reply(200, [{"id": "note1", "createdAt": "2026-01-01T00:00:00Z"}]),
    ]

    notes = api.fetch_user_posts("someone", "https://misskey.example/@someone", 40)

    assert [note.url for note in notes] == ["https://misskey.example/notes/note1"]
    assert http.post.call_args_list[1][0][1] == {"userId": "local", "limit": 40}


def test_a_note_that_knows_its_url_keeps_it(api, http, reply):
    http.post.side_effect = [
        reply(200, [{"id": "local", "host": None}]),
        reply(200, [{"id": "note1", "url": "https://elsewhere/notes/note1",
                    "createdAt": "2026-01-01T00:00:00Z"}]),
    ]

    notes = api.fetch_user_posts("someone", "https://misskey.example/@someone", 40)

    assert notes[0].url == "https://elsewhere/notes/note1"


def test_more_notes_than_a_page_are_asked_for_from_the_last_one(api, http, reply):
    def note(n):
        return {"id": f"note{n}", "createdAt": "2026-01-01T00:00:00Z"}

    http.post.side_effect = [
        reply(200, [{"id": "local", "host": None}]),
        reply(200, [note(n) for n in range(100)]),
        reply(200, [note(100)]),
    ]

    notes = api.fetch_user_posts("someone", "https://misskey.example/@someone", 150)

    assert len(notes) == 101
    assert http.post.call_args_list[2][0][1] == {
        "userId": "local", "limit": 50, "untilId": "note99",
    }


def test_notes_from_a_page_we_cannot_read_are_kept(api, http, reply):
    http.post.side_effect = [
        reply(200, [{"id": "local", "host": None}]),
        reply(200, [{"id": f"note{n}", "createdAt": "2026-01-01T00:00:00Z"}
                    for n in range(100)]),
        reply(500),
    ]

    notes = api.fetch_user_posts("someone", "https://misskey.example/@someone", 150)

    assert len(notes) == 100


def test_user_posts_give_up_when_the_account_is_not_found(api, http, reply):
    http.post.return_value = reply(200, [{"id": "remote", "host": "elsewhere"}])
    assert api.fetch_user_posts("someone", "https://misskey.example/@someone", 40) is None


def test_user_posts_give_up_on_a_search_error(api, http, reply):
    http.post.return_value = reply(500)
    assert api.fetch_user_posts("someone", "https://misskey.example/@someone", 40) is None


def test_user_posts_give_up_when_the_request_fails(api, http):
    http.post.side_effect = Exception("no route to host")
    assert api.fetch_user_posts("someone", "https://misskey.example/@someone", 40) is None


def test_context_combines_children_and_conversation(api, http, reply):
    http.post.side_effect = [
        reply(200, [{"id": "child"}]),
        reply(200, [{"id": "parent"}]),
    ]

    urls = api.fetch_context_urls("note1", "https://misskey.example/notes/note1")

    assert urls == [
        "https://misskey.example/notes/child",
        "https://misskey.example/notes/parent",
    ]


def test_context_keeps_what_it_can_when_one_endpoint_fails(api, http, reply):
    http.post.side_effect = [reply(500), reply(200, [{"id": "parent"}])]

    urls = api.fetch_context_urls("note1", "https://misskey.example/notes/note1")

    assert urls == ["https://misskey.example/notes/parent"]


def test_context_is_empty_when_the_request_fails(api, http):
    http.post.side_effect = Exception("no route to host")
    assert api.fetch_context_urls("note1", "https://misskey.example/notes/note1") == []


def test_context_is_empty_when_the_body_makes_no_sense(api, http, reply):
    http.post.return_value = reply(200, None)
    assert api.fetch_context_urls("note1", "https://misskey.example/notes/note1") == []


def test_a_misskey_note_is_given_a_url_when_it_has_none():
    post = built(to_post(
        {"id": "note1", "createdAt": WHEN, "visibility": "public"}, "misskey.example"
    ))

    assert post.url == "https://misskey.example/notes/note1"


def test_a_misskey_note_that_knows_its_own_url_keeps_it():
    post = built(to_post(
        {"id": "note1", "url": "https://elsewhere/notes/1", "createdAt": WHEN},
        "misskey.example",
    ))

    assert post.url == "https://elsewhere/notes/1"


@pytest.mark.parametrize(
    "visibility,expected",
    [("public", True), ("home", True), ("followers", False), ("specified", False)],
)
def test_a_misskey_note_is_public_when_it_is_not_for_a_chosen_few(visibility, expected):
    post = built(to_post(
        {"id": "1", "createdAt": WHEN, "visibility": visibility}, "misskey.example"
    ))
    assert post.is_public is expected


def test_a_renote_is_a_boost_by_another_name():
    post = built(to_post({
        "id": "1", "createdAt": WHEN, "visibility": "public",
        "renote": {"id": "9", "createdAt": WHEN, "visibility": "public"},
    }, "misskey.example"))

    assert post.is_boost
    assert post.original.url == "https://misskey.example/notes/9"


def test_a_misskey_note_says_what_it_replied_to_in_its_own_words():
    post = built(to_post(
        {"id": "1", "createdAt": WHEN, "replyId": "7", "repliesCount": 3},
        "misskey.example",
    ))

    assert post.in_reply_to_id == "7"
    assert post.reply_count == 3


def test_a_misskey_note_without_an_id_is_dropped():
    assert to_post({"createdAt": WHEN}, "misskey.example") is None


def test_the_name_this_api_uses_for_an_account(api):
    assert api.username_from("https://misskey.example/@someone") == "someone"


def test_a_profile_url_this_api_does_not_use(api):
    # a Misskey account is only ever @name, never a bare path
    assert api.username_from("https://misskey.example/someone") is None


def test_the_way_this_api_addresses_a_post(api):
    assert api.post_id_from("https://misskey.example/notes/abc123") == "abc123"


@pytest.mark.parametrize(
    "post_url",
    ["https://misskey.example/@someone/abc123", "https://misskey.example/notes/"],
)
def test_a_post_url_this_api_does_not_use(api, post_url):
    assert api.post_id_from(post_url) is None
