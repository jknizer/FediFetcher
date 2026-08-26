import pytest

from fedifetcher.api.lemmy import LemmyApi, to_post
from fedifetcher.posts import Post
from fedifetcher.servers import ApiFlavour


@pytest.fixture
def api(http):
    return LemmyApi("lemmy.world", http)

WHEN = "2026-01-01T00:00:00.000Z"


def built(post: Post | None) -> Post:
    """The builder drops what it cannot use; these tests are about the rest"""
    assert post is not None
    return post


def test_it_claims_the_lemmy_flavour():
    assert LemmyApi.flavour is ApiFlavour.LEMMY


def test_community_posts_are_read_from_the_community_endpoint(api, http, reply):
    http.get.return_value = reply(200, {"posts": [
        {"post": {"ap_id": "https://lemmy.world/post/1", "published": "2026-01-01T00:00:00Z"}},
    ]})

    posts = api.fetch_user_posts("news", "https://lemmy.world/c/news", 40)

    assert [post.url for post in posts] == ["https://lemmy.world/post/1"]
    assert "community_name=news" in http.get.call_args[0][0]


def test_account_posts_combine_posts_and_comments(api, http, reply):
    http.get.return_value = reply(200, {
        "comments": [{"post": {"ap_id": "https://lemmy.world/comment/1", "published": "2026-01-01T00:00:00Z"}}],
        "posts": [{"post": {"ap_id": "https://lemmy.world/post/2", "published": "2026-01-01T00:00:00Z"}}],
    })

    posts = api.fetch_user_posts("someone", "https://lemmy.world/u/someone", 40)

    assert [p.url for p in posts] == [
        "https://lemmy.world/comment/1",
        "https://lemmy.world/post/2",
    ]


def test_more_community_posts_than_a_page_are_asked_for_by_page_number(api, http, reply):
    def entry(n):
        return {"post": {"ap_id": f"https://lemmy.world/post/{n}", "published": WHEN}}

    http.get.side_effect = [
        reply(200, {"posts": [entry(n) for n in range(50)]}),
        reply(200, {"posts": [entry(50)]}),
    ]

    posts = api.fetch_user_posts("news", "https://lemmy.world/c/news", 100)

    assert len(posts) == 51
    assert "page=2" in http.get.call_args[0][0]


def test_more_account_posts_than_a_page_are_asked_for_by_page_number(api, http, reply):
    def entry(n):
        return {"post": {"ap_id": f"https://lemmy.world/post/{n}", "published": WHEN}}

    http.get.side_effect = [
        reply(200, {"comments": [entry(n) for n in range(30)],
                    "posts": [entry(n) for n in range(30, 60)]}),
        reply(200, {"comments": [], "posts": [entry(60)]}),
    ]

    posts = api.fetch_user_posts("someone", "https://lemmy.world/u/someone", 100)

    assert len(posts) == 61
    assert "page=2" in http.get.call_args[0][0]


def test_an_unrecognised_profile_url_yields_nothing(api, http):
    assert api.fetch_user_posts("someone", "https://lemmy.world/x/someone", 40) is None
    http.get.assert_not_called()


def test_user_posts_give_up_when_the_request_fails(api, http):
    http.get.side_effect = Exception("no route to host")
    assert api.fetch_user_posts("someone", "https://lemmy.world/u/someone", 40) is None


def test_post_context_lists_the_post_and_its_comments(api, http, reply):
    http.get.side_effect = [
        reply(200, {"post_view": {"counts": {"comments": 2}, "post": {"ap_id": "https://lemmy.world/post/5"}}}),
        reply(200, {"comments": [
            {"comment": {"ap_id": "https://lemmy.world/comment/6"}},
            {"comment": {"ap_id": "https://lemmy.world/comment/7"}},
        ]}),
    ]

    urls = api.fetch_context_urls("5", "https://lemmy.world/post/5")

    assert urls == [
        "https://lemmy.world/post/5",
        "https://lemmy.world/comment/6",
        "https://lemmy.world/comment/7",
    ]


def test_a_post_without_comments_has_no_context(api, http, reply):
    http.get.return_value = reply(200, {"post_view": {"counts": {"comments": 0}, "post": {}}})
    assert api.fetch_context_urls("5", "https://lemmy.world/post/5") == []


def test_comment_context_resolves_to_its_post(api, http, reply):
    http.get.side_effect = [
        reply(200, {"comment_view": {"comment": {"post_id": "5"}}}),
        reply(200, {"post_view": {"counts": {"comments": 1}, "post": {"ap_id": "https://lemmy.world/post/5"}}}),
        reply(200, {"comments": [{"comment": {"ap_id": "https://lemmy.world/comment/6"}}]}),
    ]

    urls = api.fetch_context_urls("6", "https://lemmy.world/comment/6")

    assert urls == ["https://lemmy.world/post/5", "https://lemmy.world/comment/6"]


def test_comment_context_gives_up_on_an_error_status(api, http, reply):
    http.get.return_value = reply(500)
    assert api.fetch_context_urls("6", "https://lemmy.world/comment/6") == []


def test_comment_context_gives_up_when_the_body_makes_no_sense(api, http, reply):
    http.get.return_value = reply(200, {"unexpected": True})
    assert api.fetch_context_urls("6", "https://lemmy.world/comment/6") == []


def test_an_unrecognised_post_url_yields_no_context(api, http):
    assert api.fetch_context_urls("5", "https://lemmy.world/x/5") == []
    http.get.assert_not_called()


def test_a_lemmy_post_is_named_by_its_activitypub_id():
    post = built(to_post({"ap_id": "https://lemmy.example/post/1", "published": WHEN}))

    assert post.url == "https://lemmy.example/post/1"
    assert post.uri == "https://lemmy.example/post/1"
    # Lemmy has nothing to hide: a community is readable or it is not
    assert post.is_public


def test_a_lemmy_post_without_an_id_is_dropped():
    assert to_post({"published": WHEN}) is None


@pytest.mark.parametrize(
    "profile_url,username",
    [
        ("https://lemmy.example/u/someone", "someone"),
        ("https://lemmy.example/c/a-community", "a-community"),
    ],
)
def test_the_names_this_api_uses_for_an_account(api, profile_url, username):
    assert api.username_from(profile_url) == username


def test_a_profile_url_this_api_does_not_use(api):
    assert api.username_from("https://lemmy.example/someone") is None


@pytest.mark.parametrize(
    "post_url,post_id",
    [
        ("https://lemmy.example/post/1234", "1234"),
        ("https://lemmy.example/comment/5678", "5678"),
    ],
)
def test_the_ways_this_api_addresses_a_post(api, post_url, post_id):
    assert api.post_id_from(post_url) == post_id


@pytest.mark.parametrize(
    "post_url",
    ["https://lemmy.example/post/", "https://lemmy.example/notes/1234", "not a url"],
)
def test_a_post_url_this_api_does_not_use(api, post_url):
    assert api.post_id_from(post_url) is None
