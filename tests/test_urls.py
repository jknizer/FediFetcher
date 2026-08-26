import pytest

from fedifetcher.urls import PostRef, host_of


def test_refs_are_tuples():
    """Call sites still index these positionally, so the tuple shape has to hold."""
    ref = PostRef("mstdn.thms.uk", "12345")
    assert ref == ("mstdn.thms.uk", "12345")
    assert ref[0] == ref.server
    assert ref[1] == ref.post_id


@pytest.mark.parametrize(
    "url,host",
    [
        ("https://mstdn.example/@someone", "mstdn.example"),
        ("https://video.example/video-channels/a-channel", "video.example"),
        ("https://example.test", "example.test"),
        ("https://example.test:8443/@a", "example.test:8443"),
    ],
)
def test_the_host_is_read_without_knowing_the_software(url, host):
    assert host_of(url) == host


@pytest.mark.parametrize(
    "url",
    ["http://mstdn.example/@someone", "not a url", "", "ftp://example.test/x"],
)
def test_a_url_we_cannot_take_a_host_from(url):
    assert host_of(url) is None
