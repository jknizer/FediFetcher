from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any, ClassVar, cast

from fedifetcher.api.paging import in_pages
from fedifetcher.posts import Post
from fedifetcher.servers import ApiFlavour
from fedifetcher.translate import first_name_in, parse_date, unusable, usable

if TYPE_CHECKING:
    from fedifetcher.http import HttpClient

logger = logging.getLogger("FediFetcher")

# "home" is Misskey's unlisted: public, but kept off the public timelines
PUBLIC = ("public", "home")

# the most notes this API will hand out at once
PAGE_SIZE = 100

PROFILE_PATHS = (re.compile(r"^https://[^/]+/@(?P<name>[^/]+)"),)
POST_PATHS = (re.compile(r"^https://[^/]+/notes/(?P<name>[^/]+)"),)


def to_post(raw: dict[str, Any], webserver: str) -> Post | None:
    """A Misskey note, which only has a URL of its own when it is a copy"""
    note_id = raw.get("id")
    created_at = parse_date(raw.get("createdAt"))
    if note_id is None or created_at is None:
        unusable("misskey", note_id)
        return None

    url = raw.get("url") or f"https://{webserver}/notes/{note_id}"
    renote = raw.get("renote")
    return Post(
        url=url,
        uri=raw.get("uri") or url,
        created_at=created_at,
        is_public=raw.get("visibility") in PUBLIC,
        reblog=to_post(renote, webserver) if renote is not None else None,
        in_reply_to_id=raw.get("replyId"),
        reply_count=raw.get("repliesCount"),
    )


class MisskeyApi:
    """Misskey and its forks: Calckey, Firefish, Foundkey, Sharkey"""

    flavour: ClassVar[ApiFlavour] = ApiFlavour.MISSKEY

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
        user_id = self._find_user_id(username)
        if user_id is None:
            return None

        raw = in_pages(
            lambda wanted, gathered: self._notes(user_id, username, wanted, gathered),
            limit,
            PAGE_SIZE,
        )
        if raw is None:
            return None
        return usable(to_post(note, self.webserver) for note in raw)

    def _notes(
        self, user_id: str, username: str, wanted: int, gathered: list[Any]
    ) -> list[Any] | None:
        try:
            params: dict[str, Any] = { 'userId': user_id, 'limit': wanted }
            if gathered:
                # everything older than the oldest note we already have
                params['untilId'] = gathered[-1]['id']

            url = f'https://{self.webserver}/api/users/notes'
            resp = self._http.post(url, params)

            if resp.status_code == 200:
                return cast("list[Any]", resp.json())

            logger.error(f"Error getting posts by user {username} from {self.webserver}. Status Code: {resp.status_code}")
        except Exception as ex:
            logger.error(f"Error getting posts by user {username} from {self.webserver}. Exception: {ex}")
        return None

    def _find_user_id(self, username: str) -> str | None:
        # query user info via search api
        # we could filter by host but there's no way to limit that to just the main host on firefish currently
        # on misskey it works if you supply '.' as the host but firefish does not
        try:
            url = f'https://{self.webserver}/api/users/search-by-username-and-host'
            resp = self._http.post(url, { 'username': username })

            if resp.status_code != 200:
                logger.error(f"Error finding user {username} from {self.webserver}. Status Code: {resp.status_code}")
                return None

            for user in resp.json():
                if user['host'] is None:
                    return cast("str", user['id'])
        except Exception as ex:
            logger.error(f"Error finding user {username} from {self.webserver}. Exception: {ex}")
            return None

        logger.error(f'Error finding user {username} from {self.webserver}: user not found on server in search')
        return None

    def fetch_context_urls(self, post_id: str, post_url: str) -> list[str]:
        """get the URLs of the comments of a given misskey post"""
        urls: list[str] = []
        for endpoint, params in (
            ('children', { 'noteId': post_id, 'limit': 100, 'depth': 12 }),
            ('conversation', { 'noteId': post_id, 'limit': 100 }),
        ):
            url = f"https://{self.webserver}/api/notes/{endpoint}"
            try:
                resp = self._http.post(url, params)
            except Exception as ex:
                logger.error(f"Error getting post {post_id} from {post_url}. Exception: {ex}")
                return []

            if resp.status_code != 200:
                logger.error(f"Error getting post {post_id} from {post_url}. Status Code: {resp.status_code}")
                continue

            try:
                res = resp.json()
                logger.debug(f"Got {endpoint} for misskey post {post_url}")
                urls.extend(
                    f'https://{self.webserver}/notes/{note["id"]}' for note in res
                )
            except Exception as ex:
                logger.error(f"Error parsing post {post_id} from {post_url}. Exception: {ex}")

        return urls
