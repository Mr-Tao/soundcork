import xml.etree.ElementTree as ET
from types import SimpleNamespace

from soundcork.devices import (
    _filter_items_for_sources,
    default_sources,
    get_bose_devices,
    notify_account_sources_updated,
    notify_sources_updated,
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


def test_notify_sources_updated_posts_exact_notification(monkeypatch):
    captured = {}

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self) -> bytes:
            return b"<status>/notification</status>"

    def fake_urlopen(request, timeout: int):
        captured["request"] = request
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr("soundcork.devices.urllib.request.urlopen", fake_urlopen)

    assert notify_sources_updated("192.0.2.10", "AABBCCDDEEFF", timeout=3)
    request = captured["request"]
    assert request.full_url == "http://192.0.2.10:8090/notification"
    assert request.get_method() == "POST"
    assert captured["timeout"] == 3
    root = ET.fromstring(request.data)
    assert root.tag == "updates"
    assert root.attrib == {"deviceID": "AABBCCDDEEFF"}
    assert root.find("sourcesUpdated") is not None


def test_notify_account_sources_updated_fans_out_to_stored_devices(monkeypatch):
    calls = []

    class Store:
        def list_devices(self, account: str):
            assert account == "8208423"
            return ["AABBCCDDEEFF", "112233445566", "66778899AABB"]

        def get_device_info(self, account: str, device_id: str):
            return SimpleNamespace(
                ip_address={
                    "AABBCCDDEEFF": "192.0.2.10",
                    "112233445566": "192.0.2.11",
                    "66778899AABB": "192.0.2.12",
                }[device_id]
            )

    device_by_host = {
        "192.0.2.10": "AABBCCDDEEFF",
        "192.0.2.11": "112233445566",
        "192.0.2.12": "66778899AABB",
    }
    monkeypatch.setattr(
        "soundcork.devices.read_device_info",
        lambda host: (
            f'<info deviceID="{device_by_host[host]}">'
            f"<margeURL>{'http://other:8001' if host == '192.0.2.12' else 'http://unifi:8001'}/marge</margeURL>"
            "</info>"
        ),
    )
    monkeypatch.setattr(
        "soundcork.devices.notify_sources_updated",
        lambda host, device_id: calls.append((host, device_id)) or True,
    )

    notify_account_sources_updated(Store(), "8208423", "http://unifi:8001")

    assert calls == [
        ("192.0.2.10", "AABBCCDDEEFF"),
        ("192.0.2.11", "112233445566"),
    ]


def test_notify_account_sources_updated_skips_broken_device(monkeypatch):
    calls = []

    class Store:
        def list_devices(self, _account: str):
            return ["AABBCCDDEEFF", "112233445566"]

        def get_device_info(self, _account: str, device_id: str):
            if device_id == "AABBCCDDEEFF":
                raise RuntimeError("incomplete device data")
            return SimpleNamespace(ip_address="192.0.2.11")

    monkeypatch.setattr(
        "soundcork.devices.read_device_info",
        lambda _host: (
            '<info deviceID="112233445566">'
            "<margeURL>http://unifi:8001/marge</margeURL>"
            "</info>"
        ),
    )
    monkeypatch.setattr(
        "soundcork.devices.notify_sources_updated",
        lambda host, device_id: calls.append((host, device_id)) or True,
    )

    notify_account_sources_updated(Store(), "8208423", "http://unifi:8001")

    assert calls == [("192.0.2.11", "112233445566")]


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
