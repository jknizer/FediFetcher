from datetime import datetime
from unittest.mock import Mock, patch

import pytest

from fedifetcher.servers import (
    ApiFlavour,
    ServerInfo,
    get_nodeinfo,
    get_server_from_host_meta,
    get_server_info,
)
from fedifetcher.state import ServerCache

HOST_META = """<?xml version="1.0"?>
<XRD xmlns="http://docs.oasis-open.org/ns/xri/xrd-1.0">
  <Link rel="lrdd" template="https://web.example/.well-known/webfinger?resource={uri}"/>
</XRD>
"""

WELL_KNOWN = {
    "links": [
        {
            "rel": "http://nodeinfo.diaspora.software/ns/schema/2.0",
            "href": "https://example.social/nodeinfo/2.0",
        }
    ]
}


def nodeinfo(software="mastodon", version="4.2.1", **extra):
    return {
        "protocols": ["activitypub"],
        "software": {"name": software, "version": version},
        **extra,
    }


def response(status_code=200, json_data=None, text=""):
    resp = Mock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.text = text
    return resp


def test_software_maps_to_its_api():
    info = ServerInfo.from_nodeinfo("example.social", nodeinfo("firefish"))
    assert info.supports(ApiFlavour.MISSKEY)
    assert not info.supports(ApiFlavour.MASTODON)


@pytest.mark.parametrize(
    "software,flavour",
    [
        ("mastodon", ApiFlavour.MASTODON),
        ("akkoma", ApiFlavour.MASTODON),
        ("Iceshrimp.NET", ApiFlavour.MASTODON),
        ("sharkey", ApiFlavour.MISSKEY),
        ("lemmy", ApiFlavour.LEMMY),
        ("peertube", ApiFlavour.PEERTUBE),
    ],
)
def test_known_software_is_recognised(software, flavour):
    info = ServerInfo.from_nodeinfo("example.social", nodeinfo(software))
    assert info.apis == {flavour}


def test_unknown_software_supports_nothing():
    info = ServerInfo.from_nodeinfo("example.social", nodeinfo("something-new"))
    assert info.apis == frozenset()


def test_advertised_mastodon_api_is_believed():
    info = ServerInfo.from_nodeinfo(
        "example.social",
        nodeinfo("something-new", metadata={"features": ["mastodon_api"]}),
    )
    assert info.supports(ApiFlavour.MASTODON)


def test_features_that_are_not_a_list_are_ignored():
    info = ServerInfo.from_nodeinfo(
        "example.social", nodeinfo("something-new", metadata={"features": "mastodon_api"})
    )
    assert not info.supports(ApiFlavour.MASTODON)


def test_lookup_time_is_recorded():
    info = ServerInfo.from_nodeinfo("example.social", nodeinfo())
    assert isinstance(info.last_checked, datetime)


def test_server_info_survives_a_round_trip_through_the_state_file():
    info = ServerInfo.from_nodeinfo("example.social", nodeinfo("lemmy"))
    restored = ServerInfo.from_state(info.to_state())
    assert restored == info


def test_state_keeps_the_names_older_versions_wrote():
    state = ServerInfo.from_nodeinfo("example.social", nodeinfo()).to_state()
    assert state["mastodonApiSupport"] is True
    assert state["misskeyApiSupport"] is False
    assert state["webserver"] == "example.social"


def test_a_failed_lookup_reads_back_as_nothing_known():
    assert ServerInfo.from_state({"info": None, "last_checked": datetime.now()}) is None


def test_get_server_info_remembers_what_it_found():
    cache = ServerCache({})
    http = Mock()
    http.get.side_effect = [
        response(200, WELL_KNOWN),
        response(200, nodeinfo("lemmy")),
    ]

    info = get_server_info("example.social", cache, http=http)

    assert info is not None
    assert info.supports(ApiFlavour.LEMMY)
    assert "example.social" in cache


def test_get_server_info_answers_from_the_cache_the_second_time():
    cache = ServerCache({})
    http = Mock()
    http.get.side_effect = [
        response(200, WELL_KNOWN),
        response(200, nodeinfo("lemmy")),
    ]

    get_server_info("example.social", cache, http=http)
    again = get_server_info("example.social", cache, http=http)

    assert again is not None
    assert again.supports(ApiFlavour.LEMMY)
    assert http.get.call_count == 2


def test_get_server_info_remembers_failures_too():
    cache = ServerCache({})
    http = Mock()
    http.get.return_value = response(500)

    assert get_server_info("example.social", cache, http=http) is None
    assert "example.social" in cache
    assert get_server_info("example.social", cache, http=http) is None


def test_a_server_cached_as_a_failure_is_not_mistaken_for_a_lookup():
    """A previously failed host reached via host-meta must not look like a result"""
    cache = ServerCache({})
    cache.add("example.social", {"info": None, "last_checked": datetime.now()})
    http = Mock()
    http.get.return_value = response(200, WELL_KNOWN)

    assert get_nodeinfo("display.example", cache, http=http) is None


def test_both_domains_are_cached_when_they_differ():
    cache = ServerCache({})
    http = Mock()
    http.get.side_effect = [
        response(200, WELL_KNOWN),
        response(200, nodeinfo()),
    ]

    get_server_info("display.example", cache, http=http)

    assert "display.example" in cache
    assert "example.social" in cache


def test_servers_without_activitypub_are_skipped():
    cache = ServerCache({})
    http = Mock()
    http.get.side_effect = [
        response(200, WELL_KNOWN),
        response(200, {"protocols": ["zot"], "software": {"name": "x", "version": "1"}}),
    ]

    assert get_server_info("example.social", cache, http=http) is None


def test_nodeinfo_gives_up_when_the_request_fails():
    http = Mock()
    http.get.side_effect = Exception("no route to host")
    assert get_nodeinfo("example.social", ServerCache({}), http=http) is None


def test_nodeinfo_gives_up_without_a_known_schema_link():
    http = Mock()
    http.get.return_value = response(200, {"links": []})
    assert get_nodeinfo("example.social", ServerCache({}), http=http) is None


def test_nodeinfo_falls_back_to_host_meta():
    cache = ServerCache({})
    http = Mock()
    http.get.side_effect = [
        response(404),
        response(200, WELL_KNOWN),
        response(200, nodeinfo()),
    ]

    with patch(
        "fedifetcher.servers.get_server_from_host_meta", return_value="web.example"
    ):
        info = get_nodeinfo("display.example", cache, http=http)

    assert info is not None
    assert info.webserver == "example.social"


def test_nodeinfo_stops_when_host_meta_points_back_at_the_same_server():
    http = Mock()
    http.get.return_value = response(404)

    with patch(
        "fedifetcher.servers.get_server_from_host_meta", return_value="example.social"
    ):
        assert get_nodeinfo("example.social", ServerCache({}), http=http) is None


def test_host_meta_yields_the_web_domain():
    http = Mock()
    http.get.return_value = response(200, text=HOST_META)
    assert get_server_from_host_meta("display.example", http=http) == "web.example"


def test_host_meta_gives_up_on_an_error_status():
    http = Mock()
    http.get.return_value = response(500)
    assert get_server_from_host_meta("display.example", http=http) is None


def test_host_meta_gives_up_on_unparseable_xml():
    http = Mock()
    http.get.return_value = response(200, text="not xml")
    assert get_server_from_host_meta("display.example", http=http) is None


def test_host_meta_gives_up_when_the_request_fails():
    http = Mock()
    http.get.side_effect = Exception("no route to host")
    assert get_server_from_host_meta("display.example", http=http) is None
