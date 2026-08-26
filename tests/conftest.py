from datetime import UTC, datetime
from typing import Any
from unittest.mock import Mock

import pytest

from fedifetcher.posts import Post
from fedifetcher.store import State
from fedifetcher.users import User


def make_post(**overrides: Any) -> Post:
    """A Post that is fine in every way the test does not care about"""
    fields: dict[str, Any] = {
        "url": "https://remote.example/@someone/1",
        "uri": "https://remote.example/@someone/1",
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
        "is_public": True,
    }
    return Post(**{**fields, **overrides})


def make_user(**overrides: Any) -> User:
    """A User who has not asked to be left alone, unless a test says so"""
    fields: dict[str, Any] = {
        "acct": "someone@remote.example",
        "url": "https://remote.example/@someone",
    }
    return User(**{**fields, **overrides})


@pytest.fixture
def http():
    """A stand-in HttpClient whose get/post return whatever a test sets up"""
    return Mock()


def response(status_code=200, json_data=None, text=""):
    resp = Mock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.text = text
    return resp


@pytest.fixture
def reply():
    return response


@pytest.fixture
def state():
    """Real state: it is a plain bag of collections, so mocking it hides bugs"""
    return State()


@pytest.fixture
def home():
    """A stand-in HomeServer: our own instance, already authenticated"""
    home = Mock()
    home.server = "test_server"
    return home
