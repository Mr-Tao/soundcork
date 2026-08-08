import errno
import logging

import pytest

from soundcork.ui.speakers import Speakers


class FakeDiscovery:
    def __init__(self, error: OSError | None = None) -> None:
        self.error = error
        self.VerifiedDevices = {"192.0.2.10:8090": object()}
        self.calls: list[int] = []

    def DiscoverDevices(self, timeout: int) -> None:
        self.calls.append(timeout)
        if self.error:
            raise self.error


def test_initial_discovery_tolerates_disappearing_interface(monkeypatch, caplog):
    discovery = FakeDiscovery(OSError(errno.ENODEV, "No such device"))
    monkeypatch.setattr(
        "soundcork.ui.speakers.SoundTouchDiscovery", lambda **_kwargs: discovery
    )

    with caplog.at_level(logging.WARNING):
        speakers = Speakers(object(), object())

    assert discovery.calls == [1]
    assert speakers.soundtouch_devices() == discovery.VerifiedDevices
    assert "disappearing network interface" in caplog.text


def test_initial_discovery_propagates_other_os_errors(monkeypatch):
    discovery = FakeDiscovery(OSError(errno.EACCES, "Permission denied"))
    monkeypatch.setattr(
        "soundcork.ui.speakers.SoundTouchDiscovery", lambda **_kwargs: discovery
    )

    with pytest.raises(OSError, match="Permission denied"):
        Speakers(object(), object())
