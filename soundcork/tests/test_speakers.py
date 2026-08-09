import errno
import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from soundcork.config import Settings
from soundcork.ui.speakers import Speakers

DEVICE_ID = "AABBCCDDEEFF"
OTHER_DEVICE_ID = "001122334455"


class FakeDiscovery:
    def __init__(self, error: OSError | None = None) -> None:
        self.error = error
        self.VerifiedDevices: dict[str, object] = {}
        self.calls: list[int] = []

    def DiscoverDevices(self, timeout: int) -> None:
        self.calls.append(timeout)
        if self.error:
            raise self.error


def test_initial_discovery_enodev_keeps_configured_devices_available(
    monkeypatch, caplog
):
    discovery = FakeDiscovery(OSError(errno.ENODEV, "No such device"))
    monkeypatch.setattr(
        "soundcork.ui.speakers.SoundTouchDiscovery", lambda **_kwargs: discovery
    )
    datastore = MagicMock()
    datastore.list_accounts.return_value = ["account-id"]
    datastore.list_devices.return_value = ["device-id"]
    datastore.get_device_info.return_value = SimpleNamespace(
        ip_address="192.0.2.20", name="Configured speaker"
    )

    with caplog.at_level(logging.WARNING):
        devices = Speakers(datastore, Settings()).all_devices()

    assert discovery.calls == [1]
    assert "disappearing network interface" in caplog.text
    configured = devices["device-id"]
    assert configured.name == "Configured speaker"
    assert configured.online is False
    assert configured.in_soundcork is True
    assert configured.st_device is None


def test_initial_discovery_propagates_other_os_errors(monkeypatch):
    discovery = FakeDiscovery(OSError(errno.EACCES, "Permission denied"))
    monkeypatch.setattr(
        "soundcork.ui.speakers.SoundTouchDiscovery", lambda **_kwargs: discovery
    )

    with pytest.raises(OSError, match="Permission denied"):
        Speakers(object(), object())


def configured_datastore():
    datastore = MagicMock()
    datastore.list_accounts.return_value = ["account-id"]
    datastore.list_devices.return_value = [DEVICE_ID]
    datastore.get_device_info.return_value = SimpleNamespace(
        ip_address="192.0.2.20", name="Configured speaker"
    )
    return datastore


def configured_soundtouch_device(device_id: str = DEVICE_ID):
    return SimpleNamespace(
        DeviceId=device_id,
        Host="192.0.2.20",
        DeviceName="Configured speaker",
        StreamingAccountUUID="account-id",
        StreamingUrl="http://unifi:8001/marge",
    )


def test_control_resolves_configured_device_by_ip_without_caching_it(monkeypatch):
    discovery = FakeDiscovery()
    monkeypatch.setattr(
        "soundcork.ui.speakers.SoundTouchDiscovery", lambda **_kwargs: discovery
    )
    constructor_calls = []
    st_device = configured_soundtouch_device()

    def make_device(**kwargs):
        constructor_calls.append(kwargs)
        return st_device

    now_playing = object()
    volume = SimpleNamespace(Actual=23, Target=23, IsMuted=False)
    play_calls = []
    client_managers = []

    class FakeClient:
        def __init__(self, device, manager=None):
            assert device is st_device
            client_managers.append(manager)

        def GetNowPlayingStatus(self):
            return now_playing

        def GetVolume(self, refresh=False):
            assert refresh is True
            return volume

        def PlayContentItem(self, content_item, delay):
            play_calls.append((content_item, delay))

    monkeypatch.setattr("soundcork.ui.speakers.SoundTouchDevice", make_device)
    monkeypatch.setattr("soundcork.ui.speakers.SoundTouchClient", FakeClient)
    datastore = configured_datastore()
    datastore.get_content_item.return_value = SimpleNamespace(
        name="Rádio Dechovka",
        source="LOCAL_INTERNET_RADIO",
        type="stationurl",
        location="http://example.com/dechovka.mp3",
        source_account="",
        is_presetable=True,
    )
    speakers = Speakers(datastore, Settings())

    assert speakers.get_now_playing_and_volume(DEVICE_ID) == (now_playing, volume)
    assert len(constructor_calls) == 1
    assert client_managers == [constructor_calls[0]["proxyManager"]]
    assert constructor_calls[0]["host"] == "192.0.2.20"
    assert constructor_calls[0]["connectTimeout"] == 1

    manager = constructor_calls[0]["proxyManager"]
    timeout = manager.connection_pool_kw["timeout"]
    retries = manager.connection_pool_kw["retries"]
    assert timeout.connect_timeout == 1.0
    assert timeout.read_timeout == 2.0
    assert retries.total == 0

    configured = speakers.all_devices()[DEVICE_ID]
    assert configured.online is False
    assert configured.st_device is None

    assert speakers.play_content_item(DEVICE_ID, "5") is True
    assert len(play_calls) == 1
    assert play_calls[0][1] == 0
    assert len(constructor_calls) == 2


def test_control_rejects_configured_ip_for_a_different_device(monkeypatch, caplog):
    discovery = FakeDiscovery()
    monkeypatch.setattr(
        "soundcork.ui.speakers.SoundTouchDiscovery", lambda **_kwargs: discovery
    )
    monkeypatch.setattr(
        "soundcork.ui.speakers.SoundTouchDevice",
        lambda **_kwargs: configured_soundtouch_device(OTHER_DEVICE_ID),
    )
    client = MagicMock()
    monkeypatch.setattr("soundcork.ui.speakers.SoundTouchClient", client)
    speakers = Speakers(configured_datastore(), Settings())

    with caplog.at_level(logging.WARNING):
        assert speakers.get_now_playing_status(DEVICE_ID) is None

    assert f"belongs to device {OTHER_DEVICE_ID}" in caplog.text
    client.assert_not_called()
    configured = speakers.all_devices()[DEVICE_ID]
    assert configured.online is False
    assert configured.st_device is None


def test_control_keeps_unreachable_configured_device_offline(monkeypatch, caplog):
    discovery = FakeDiscovery()
    monkeypatch.setattr(
        "soundcork.ui.speakers.SoundTouchDiscovery", lambda **_kwargs: discovery
    )

    def unavailable_device(**_kwargs):
        raise TimeoutError("speaker did not answer")

    monkeypatch.setattr("soundcork.ui.speakers.SoundTouchDevice", unavailable_device)
    client = MagicMock()
    monkeypatch.setattr("soundcork.ui.speakers.SoundTouchClient", client)
    speakers = Speakers(configured_datastore(), Settings())

    with caplog.at_level(logging.INFO):
        assert speakers.get_now_playing_status(DEVICE_ID) is None

    assert "is not reachable through REST" in caplog.text
    client.assert_not_called()
    configured = speakers.all_devices()[DEVICE_ID]
    assert configured.online is False
    assert configured.st_device is None


@pytest.mark.parametrize(
    ("device_id", "configured_ip"),
    [
        ("not-a-device-id", "192.0.2.20"),
        (DEVICE_ID, "not-an-ip"),
        (DEVICE_ID, "127.0.0.1"),
        (DEVICE_ID, "0.0.0.0"),
        (DEVICE_ID, "224.0.0.1"),
        (DEVICE_ID, "2001:db8::20"),
    ],
)
def test_control_rejects_invalid_identity_or_address(
    monkeypatch, device_id, configured_ip
):
    discovery = FakeDiscovery()
    monkeypatch.setattr(
        "soundcork.ui.speakers.SoundTouchDiscovery", lambda **_kwargs: discovery
    )
    datastore = configured_datastore()
    datastore.list_devices.return_value = [device_id]
    datastore.get_device_info.return_value = SimpleNamespace(
        ip_address=configured_ip,
        name="Configured speaker",
    )
    constructor = MagicMock()
    monkeypatch.setattr("soundcork.ui.speakers.SoundTouchDevice", constructor)
    speakers = Speakers(datastore, Settings())

    assert speakers.get_now_playing_status(device_id) is None

    constructor.assert_not_called()


def test_control_retries_resolution_once_when_configured_ip_changes(monkeypatch):
    discovery = FakeDiscovery()
    monkeypatch.setattr(
        "soundcork.ui.speakers.SoundTouchDiscovery", lambda **_kwargs: discovery
    )
    datastore = configured_datastore()
    addresses = iter(
        [
            "192.0.2.20",
            "192.0.2.21",
            "192.0.2.21",
            "192.0.2.21",
        ]
    )

    def device_info(*_args):
        return SimpleNamespace(
            ip_address=next(addresses),
            name="Configured speaker",
        )

    datastore.get_device_info.side_effect = device_info
    constructor_hosts = []

    def make_device(**kwargs):
        constructor_hosts.append(kwargs["host"])
        device = configured_soundtouch_device()
        device.Host = kwargs["host"]
        return device

    class FakeClient:
        def __init__(self, device, manager=None):
            assert device.Host == "192.0.2.21"
            assert manager is not None

        def GetNowPlayingStatus(self):
            return "now-playing"

    monkeypatch.setattr("soundcork.ui.speakers.SoundTouchDevice", make_device)
    monkeypatch.setattr("soundcork.ui.speakers.SoundTouchClient", FakeClient)
    speakers = Speakers(datastore, Settings())

    assert speakers.get_now_playing_status(DEVICE_ID) == "now-playing"
    assert constructor_hosts == ["192.0.2.20", "192.0.2.21"]


def test_play_timeout_is_not_retried(monkeypatch, caplog):
    discovery = FakeDiscovery()
    monkeypatch.setattr(
        "soundcork.ui.speakers.SoundTouchDiscovery", lambda **_kwargs: discovery
    )
    st_device = configured_soundtouch_device()
    constructor = MagicMock(return_value=st_device)
    play_calls = []

    class FakeClient:
        def __init__(self, device, manager=None):
            assert device is st_device
            assert manager is not None

        def PlayContentItem(self, content_item, delay):
            play_calls.append((content_item, delay))
            raise TimeoutError("response was lost")

    monkeypatch.setattr("soundcork.ui.speakers.SoundTouchDevice", constructor)
    monkeypatch.setattr("soundcork.ui.speakers.SoundTouchClient", FakeClient)
    datastore = configured_datastore()
    datastore.get_content_item.return_value = SimpleNamespace(
        name="Rádio Dechovka",
        source="LOCAL_INTERNET_RADIO",
        type="stationurl",
        location="http://example.com/dechovka.mp3",
        source_account="",
        is_presetable=True,
    )
    speakers = Speakers(datastore, Settings())

    with caplog.at_level(logging.ERROR):
        assert speakers.play_content_item(DEVICE_ID, "5") is False

    assert len(play_calls) == 1
    assert play_calls[0][1] == 0
    assert constructor.call_count == 1
    assert "outcome may be uncertain" in caplog.text
