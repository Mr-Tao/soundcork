import xml.etree.ElementTree as ET

from soundcork.devices import (
    _filter_items_for_sources,
    default_sources,
    get_bose_devices,
    read_file_from_speaker_http,
)


def test_read_file_from_speaker_http_passes_timeout(monkeypatch):
    calls = []

    class Response:
        def read(self) -> bytes:
            return b"<info />"

    def fake_urlopen(url: str, timeout: int):
        calls.append((url, timeout))
        return Response()

    monkeypatch.setattr("soundcork.devices.urllib.request.urlopen", fake_urlopen)

    result = read_file_from_speaker_http("192.0.2.10", "/info", timeout=7)

    assert result == "<info />"
    assert calls == [("http://192.0.2.10:8090/info", 7)]


def test_get_bose_devices_passes_discovery_timeout(monkeypatch):
    timeouts = []

    monkeypatch.setattr(
        "soundcork.devices.upnpclient.discover",
        lambda timeout: timeouts.append(timeout) or [],
    )

    assert get_bose_devices(timeout=1) == []
    assert timeouts == [1]


def test_filter_items_for_sources_removes_unavailable_recent_source():
    recents = """
        <recents>
            <recent id="1">
                <contentItem source="LOCAL_INTERNET_RADIO">
                    <itemName>Radio Proglas</itemName>
                </contentItem>
            </recent>
            <recent id="2">
                <contentItem source="SPOTIFY" sourceAccount="listener">
                    <itemName>Spotify item</itemName>
                </contentItem>
            </recent>
        </recents>
    """

    filtered = _filter_items_for_sources(
        recents, "recent", "contentItem", default_sources()
    )

    items = ET.fromstring(filtered).findall("recent")
    assert [item.findtext("contentItem/itemName") for item in items] == [
        "Radio Proglas"
    ]


def test_filter_items_for_sources_keeps_unchanged_supported_presets():
    presets = """
        <presets>
            <preset id="1">
                <ContentItem source="TUNEIN">
                    <itemName>Radio station</itemName>
                </ContentItem>
            </preset>
        </presets>
    """

    filtered = _filter_items_for_sources(
        presets, "preset", "ContentItem", default_sources()
    )

    assert filtered == presets
