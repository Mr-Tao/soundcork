import pytest

from soundcork.marge_paths import (
    is_marge_protocol_path,
    is_soundcork_marge_url,
    is_streaming_protocol_path,
    legacy_marge_prefix,
)


@pytest.mark.parametrize(
    "url",
    (
        "http://unifi:8001",
        "http://unifi:8001/",
        "http://unifi:8001/marge",
        "http://unifi:8001/marge/",
    ),
)
def test_soundcork_marge_url_accepts_canonical_and_legacy(url):
    assert is_soundcork_marge_url(url, "http://unifi:8001/")


@pytest.mark.parametrize(
    "url",
    (
        None,
        "",
        "/",
        "http://unifi:8001/marge-old",
        "http://other:8001/marge",
    ),
)
def test_soundcork_marge_url_rejects_other_endpoints(url):
    assert not is_soundcork_marge_url(url, "http://unifi:8001")


def test_soundcork_marge_url_rejects_empty_base_url():
    assert not is_soundcork_marge_url("/", "")


@pytest.mark.parametrize(
    "path",
    (
        "/streaming",
        "/streaming/account/1",
        "/oauth/device/1",
        "/marge",
        "/marge/unknown",
        "/marge/streaming/account/1",
        "/marge/oauth/device/1",
    ),
)
def test_marge_protocol_path_accepts_canonical_and_legacy(path):
    assert is_marge_protocol_path(path)


@pytest.mark.parametrize("path", ("/", "/marge-old/streaming", "/streaming-old"))
def test_marge_protocol_path_rejects_unrelated_paths(path):
    assert not is_marge_protocol_path(path)


@pytest.mark.parametrize(
    ("path", "expected"),
    (
        ("/streaming/account/1", True),
        ("/marge/streaming/account/1", True),
        ("/oauth/device/1", False),
        ("/marge/oauth/device/1", False),
    ),
)
def test_streaming_protocol_path(path, expected):
    assert is_streaming_protocol_path(path) is expected


@pytest.mark.parametrize(
    ("path", "expected"),
    (
        ("/streaming/account/1", ""),
        ("/marge", "/marge"),
        ("/marge/streaming/account/1", "/marge"),
        ("/marge-old/streaming/account/1", ""),
    ),
)
def test_legacy_marge_prefix(path, expected):
    assert legacy_marge_prefix(path) == expected
