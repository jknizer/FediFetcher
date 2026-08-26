from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

    Page = Callable[[int, list[Any]], list[Any] | None]


def in_pages(fetch: Page, limit: int, page_size: int) -> list[Any] | None:
    """Gather up to `limit` entries from a server that pages its collections.

    `fetch` is handed how many entries are still wanted and everything
    gathered so far, out of which each server's own idea of where we got to is
    worked out. It returns a page, or None if that page could not be read:
    failing on the first page means we learned nothing and say so, while
    failing later only means we stop early with what we already have.
    """
    gathered: list[Any] = []
    while len(gathered) < limit:
        wanted = min(page_size, limit - len(gathered))
        page = fetch(wanted, gathered)
        if page is None:
            return gathered or None

        gathered += page
        # a server with more to give fills the page it was asked for, so this
        # saves a request for every account that has posted less than we want
        if len(page) < wanted:
            break

    return gathered


__all__ = ["in_pages"]
