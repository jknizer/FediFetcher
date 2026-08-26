from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any, ClassVar

from fedifetcher.api.paging import in_pages
from fedifetcher.posts import Post
from fedifetcher.servers import ApiFlavour
from fedifetcher.translate import first_name_in, parse_date, unusable, usable

if TYPE_CHECKING:
    from collections.abc import Callable

    from fedifetcher.http import HttpClient

logger = logging.getLogger("FediFetcher")

# the most entries this API will hand out at once
PAGE_SIZE = 50

COMMUNITY_PATH = re.compile(r"^https://[^/]+/c/")
USER_PATH = re.compile(r"^https://[^/]+/u/")

PROFILE_PATHS = (re.compile(r"^https://[^/]+/(?:u|c)/(?P<name>[^/]+)"),)
POST_PATHS = (re.compile(r"^https://[^/]+/(?:comment|post)/(?P<name>[^/]+)"),)


def to_post(raw: dict[str, Any]) -> Post | None:
    """A Lemmy post or comment, which names itself by its ActivityPub id"""
    ap_id = raw.get("ap_id")
    created_at = parse_date(raw.get("published"))
    if ap_id is None or created_at is None:
        unusable("lemmy", raw.get("id"))
        return None

    # Lemmy has no visibility setting: a community is readable or it is not
    return Post(url=ap_id, uri=ap_id, created_at=created_at, is_public=True)


class LemmyApi:
    """Lemmy, whose URLs say whether they name a community, user, post or comment"""

    flavour: ClassVar[ApiFlavour] = ApiFlavour.LEMMY

    def __init__(self, webserver: str, http: HttpClient) -> None:
        self.webserver = webserver
        self._http = http

    def username_from(self, profile_url: str) -> str | None:
        return first_name_in(PROFILE_PATHS, profile_url)

    def post_id_from(self, post_url: str) -> str | None:
        return first_name_in(POST_PATHS, post_url)

    def fetch_user_posts(
        self, username: str, profile_url: str, limit: int
    ) -> list[Post] | None:
        if COMMUNITY_PATH.match(profile_url):
            return self._fetch_community_posts(username, limit)
        if USER_PATH.match(profile_url):
            return self._fetch_account_posts(username, limit)

        logger.error(f"Unknown Lemmy profile URL type {profile_url}")
        return None

    def _fetch_community_posts(self, username: str, limit: int) -> list[Post] | None:
        return self._paged(
            f"post/list?community_name={username}",
            lambda body: [entry['post'] for entry in body['posts']],
            limit,
            f"community posts for community {username}",
        )

    def _fetch_account_posts(self, username: str, limit: int) -> list[Post] | None:
        # an account's page holds up to a full page of each, so a page short of
        # what was asked for has run out of both, which is what ends the paging
        return self._paged(
            f"user?username={username}",
            lambda body: [entry['post'] for entry in body['comments'] + body['posts']],
            limit,
            f"user posts for user {username}",
        )

    def _paged(
        self,
        path: str,
        entries: Callable[[Any], list[Any]],
        limit: int,
        describe: str,
    ) -> list[Post] | None:
        """Read a collection, which Lemmy numbers the pages of rather than
        pointing at the next one, so where we got to is a page count."""

        def page(wanted: int, gathered: list[Any]) -> list[Any] | None:
            try:
                url = f"https://{self.webserver}/api/v3/{path}&sort=New&limit={wanted}&page={len(gathered) // PAGE_SIZE + 1}"
                response = self._http.get(url)

                if(response.status_code == 200):
                    return entries(response.json())

                logger.error(f"Error getting {describe}. Status code: {response.status_code}")
            except Exception as ex:
                logger.error(f"Error getting {describe}: {ex}")
            return None

        raw = in_pages(page, limit, PAGE_SIZE)
        return None if raw is None else usable(to_post(post) for post in raw)

    def fetch_context_urls(self, post_id: str, post_url: str) -> list[str]:
        if "/comment/" in post_url:
            return self._comment_context(post_id, post_url)
        if "/post/" in post_url:
            return self._post_comments(post_id, post_url)

        logger.error(f'unknown lemmy url type {post_url}')
        return []

    def _comment_context(self, comment_id: str, post_url: str) -> list[str]:
        """get the URLs of the context toots of the given toot"""
        url = f"https://{self.webserver}/api/v3/comment?id={comment_id}"
        try:
            resp = self._http.get(url)
        except Exception as ex:
            logger.error(f"Error getting comment {comment_id} from {post_url}. Exception: {ex}")
            return []

        if resp.status_code == 200:
            try:
                res = resp.json()
                post_id = res['comment_view']['comment']['post_id']
                return self._post_comments(post_id, post_url)
            except Exception as ex:
                logger.error(f"Error parsing context for comment {post_url}. Exception: {ex}")
            return []

        logger.error(f"Error getting comment {comment_id} from {post_url}. Status code: {resp.status_code}")
        return []

    def _post_comments(self, post_id: str, post_url: str) -> list[str]:
        """get the URLs of the comments of the given post"""
        urls: list[str] = []
        url = f"https://{self.webserver}/api/v3/post?id={post_id}"
        try:
            resp = self._http.get(url)
        except Exception as ex:
            logger.error(f"Error getting post {post_id} from {post_url}. Exception: {ex}")
            return []

        if resp.status_code == 200:
            try:
                res = resp.json()
                if res['post_view']['counts']['comments'] == 0:
                    return []
                urls.append(res['post_view']['post']['ap_id'])
            except Exception as ex:
                logger.error(f"Error parsing post {post_id} from {post_url}. Exception: {ex}")

        url = f"https://{self.webserver}/api/v3/comment/list?post_id={post_id}&sort=New&limit=50"
        try:
            resp = self._http.get(url)
        except Exception as ex:
            logger.error(f"Error getting comments for post {post_id} from {post_url}. Exception: {ex}")
            return []

        if resp.status_code == 200:
            try:
                res = resp.json()
                list_of_urls = [comment_info['comment']['ap_id'] for comment_info in res['comments']]
                logger.debug(f"Got {len(list_of_urls)} comments for post {post_url}")
                urls.extend(list_of_urls)
                return urls
            except Exception as ex:
                logger.error(f"Error parsing comments for post {post_url}. Exception: {ex}")

        logger.error(f"Error getting comments for post {post_url}. Status code: {resp.status_code}")
        return []
