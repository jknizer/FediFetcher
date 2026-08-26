from datetime import UTC, datetime

import pytest

from fedifetcher.translate import parse_date, unusable, usable

WHEN = datetime(2026, 1, 1, tzinfo=UTC)


def test_the_things_we_could_use_are_the_ones_we_keep():
    # generic on purpose: it filters Posts and Users alike
    assert usable(["a", None, "b"]) == ["a", "b"]


def test_nothing_usable_is_an_empty_list_rather_than_nothing():
    assert usable([None, None]) == []


@pytest.mark.parametrize(
    "value,expected",
    [
        ("2026-01-01T00:00:00.000Z", datetime(2026, 1, 1, tzinfo=UTC)),
        ("2026-01-01T00:00:00+00:00", datetime(2026, 1, 1, tzinfo=UTC)),
        (WHEN, WHEN),
    ],
)
def test_a_date_is_read_however_the_server_spelled_it(value, expected):
    assert parse_date(value) == expected


@pytest.mark.parametrize("value", ["not a date", "", None, 17, {"when": "now"}])
def test_a_date_we_cannot_read_is_admitted_to_rather_than_guessed(value):
    assert parse_date(value) is None


def test_dropping_a_post_is_worth_saying_out_loud(caplog):
    with caplog.at_level("DEBUG"):
        unusable("newthing", "https://remote.example/1")

    assert "newthing" in caplog.text
    assert "https://remote.example/1" in caplog.text
