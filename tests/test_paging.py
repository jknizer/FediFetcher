from typing import Any

from fedifetcher.api.paging import in_pages


def pages_of(*pages: Any):
    """A page fetcher handing out the given pages in turn, recording its asks"""
    asked: list[tuple[int, int]] = []
    remaining = list(pages)

    def fetch(wanted: int, gathered: list[Any]) -> list[Any] | None:
        asked.append((wanted, len(gathered)))
        return remaining.pop(0) if remaining else []

    return fetch, asked


def test_a_single_short_page_is_all_there_is():
    fetch, asked = pages_of([1, 2])

    assert in_pages(fetch, 10, 5) == [1, 2]
    assert asked == [(5, 0)]


def test_pages_are_asked_for_until_the_limit_is_reached():
    fetch, asked = pages_of([1, 2], [3, 4], [5, 6])

    assert in_pages(fetch, 5, 2) == [1, 2, 3, 4, 5, 6]
    # each page is asked for what is still wanted, and told what came before it
    assert asked == [(2, 0), (2, 2), (1, 4)]


def test_a_page_the_server_did_not_fill_ends_the_paging():
    fetch, asked = pages_of([1, 2], [3])

    assert in_pages(fetch, 100, 2) == [1, 2, 3]
    # no request is made for a page we already know is not there
    assert len(asked) == 2


def test_nothing_at_all_is_not_a_failure():
    fetch, _ = pages_of([])

    assert in_pages(fetch, 10, 5) == []


def test_a_first_page_we_cannot_read_yields_nothing():
    fetch, _ = pages_of(None)

    assert in_pages(fetch, 10, 5) is None


def test_a_later_page_we_cannot_read_keeps_what_came_before():
    fetch, _ = pages_of([1, 2], None)

    assert in_pages(fetch, 10, 2) == [1, 2]


def test_a_limit_below_one_page_asks_for_no_more_than_that():
    fetch, asked = pages_of([1, 2, 3])

    assert in_pages(fetch, 3, 40) == [1, 2, 3]
    assert asked == [(3, 0)]
