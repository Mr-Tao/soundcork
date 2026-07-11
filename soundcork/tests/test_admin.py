from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from soundcork.admin import (
    _combined_from_management_device,
    _set_device_account,
    get_admin_router,
)
from soundcork.management import ManagementDevice, ManagementDevicesResponse
from soundcork.ui.speakers import CombinedDevice

ACCOUNT_ID = "8208423"
ALT_ACCOUNT_ID = "5700454"
DEVICE_ID = "000C8A123456"
DEVICE_IP = "192.168.11.71"


class FakeDatastore:
    def __init__(
        self,
        accounts: list[str] | None = None,
        device_accounts: dict[str, list[str]] | None = None,
    ):
        self.accounts = accounts or [ACCOUNT_ID]
        self.device_accounts = device_accounts or {DEVICE_ID: [ACCOUNT_ID]}
        self.removed_devices: list[tuple[str, str]] = []
        self.deleted_groups: list[tuple[str, str]] = []

    def list_accounts(self) -> list[str]:
        return self.accounts

    def account_exists(self, account_id: str) -> bool:
        return account_id in self.accounts

    def device_exists(self, account_id: str, device_id: str) -> bool:
        return account_id in self.device_accounts.get(device_id, [])

    def group_for_device(self, _account_id: str, _device_id: str):
        return None

    def delete_group(self, account_id: str, group_id: str):
        self.deleted_groups.append((account_id, group_id))

    def remove_device(self, account_id: str, device_id: str) -> bool:
        self.removed_devices.append((account_id, device_id))
        accounts = self.device_accounts.get(device_id, [])
        if account_id in accounts:
            accounts.remove(account_id)
        return True


class FakeSpeakers:
    def __init__(self, account_id: str = ACCOUNT_ID):
        self.account_id = account_id
        self.cleared_devices: list[str] = []

    def all_devices(self) -> dict[str, CombinedDevice]:
        return {
            DEVICE_ID: CombinedDevice(
                id=DEVICE_ID,
                ip=DEVICE_IP,
                name="Kitchen",
                online=True,
                account=self.account_id,
                in_soundcork=True,
                marge_server="Unknown",
                reachable=False,
                st_device=None,
            )
        }

    def clear_device(self, device_id: str):
        self.cleared_devices.append(device_id)


class EmptySpeakers(FakeSpeakers):
    def all_devices(self) -> dict[str, CombinedDevice]:
        return {}


def management_devices_response(
    marge_server: str = "Bose",
    account_id: str = ACCOUNT_ID,
) -> ManagementDevicesResponse:
    return ManagementDevicesResponse(
        devices=[
            ManagementDevice(
                device_id=DEVICE_ID,
                account_id=account_id,
                reported_account_id=account_id,
                name="Kitchen",
                product_code="SoundTouch10 SM2",
                ip_address=DEVICE_IP,
                stored_ip_address=DEVICE_IP,
                reported_ip_address=DEVICE_IP,
                in_soundcork=True,
                rest_reachable=True,
                marge_url="https://streaming.bose.com",
                marge_server=marge_server,
                uses_this_soundcork=False,
                internet_radio_ready=False,
                playback_capability="Needs repair",
                playback_capability_detail="Radio sources are not ready.",
                source="datastore",
            )
        ]
    )


def make_client(
    monkeypatch,
    speakers: FakeSpeakers | None = None,
    datastore: FakeDatastore | None = None,
):
    monkeypatch.chdir(Path(__file__).resolve().parents[1])
    app = FastAPI()
    fake_speakers = speakers or FakeSpeakers()
    fake_datastore = datastore or FakeDatastore()
    app.include_router(get_admin_router(fake_datastore, fake_speakers))
    return TestClient(app), fake_speakers, fake_datastore


def test_admin_shows_live_marge_and_telnet_repair_action(monkeypatch):
    list_calls = []

    def fake_list_management_devices(*_args, **kwargs):
        list_calls.append(kwargs)
        return management_devices_response()

    monkeypatch.setattr(
        "soundcork.admin.list_management_devices",
        fake_list_management_devices,
    )
    monkeypatch.setattr(
        "soundcork.admin.addr_port_is_reachable",
        lambda _host, port, timeout=2: port == 17000,
    )

    client, _speakers, _datastore = make_client(monkeypatch)
    response = client.get("/admin/")

    assert response.status_code == 200
    assert "Status guide" in response.text
    assert "Playback" in response.text
    assert "Needs repair" in response.text
    assert "REST /info" in response.text
    assert "Telnet CLIServer" in response.text
    assert "Switch to Soundcork" in response.text
    assert f"/admin/switchToSoundcork/{DEVICE_ID}" in response.text
    assert "B0D5CC0391DB" not in response.text
    assert "Swtich" not in response.text
    assert list_calls[0]["include_discovered"] is False


def test_combined_from_management_device_supports_registry_only_devices():
    combined = _combined_from_management_device(
        ManagementDevice(
            device_id="AABBCCDDEEFF",
            reported_account_id="7320031",
            name="remote speaker",
            ip_address="192.168.101.222",
            in_soundcork=False,
            rest_reachable=False,
            marge_server="Unknown",
            uses_this_soundcork=False,
            playback_capability="Unknown",
            source="registry",
            registry_key="remote",
            registry_stale=True,
        )
    )

    assert combined.id == "AABBCCDDEEFF"
    assert combined.name == "remote speaker"
    assert combined.ip == "192.168.101.222"
    assert combined.account == "7320031"
    assert combined.in_soundcork is False
    assert combined.online is False
    assert combined.st_device is None


def test_admin_shows_registry_only_devices(monkeypatch):
    def fake_list_management_devices(*_args, **_kwargs):
        return ManagementDevicesResponse(
            devices=[
                ManagementDevice(
                    device_id="AABBCCDDEEFF",
                    name="remote speaker",
                    ip_address="192.168.101.222",
                    in_soundcork=False,
                    rest_reachable=False,
                    marge_server="Unknown",
                    uses_this_soundcork=False,
                    playback_capability="Unknown",
                    playback_capability_detail="Registry entry is stale.",
                    source="registry",
                    registry_key="remote",
                    registry_stale=True,
                )
            ]
        )

    monkeypatch.setattr(
        "soundcork.admin.list_management_devices",
        fake_list_management_devices,
    )
    monkeypatch.setattr(
        "soundcork.admin.addr_port_is_reachable",
        lambda _host, _port, timeout=2: False,
    )

    client, _speakers, _datastore = make_client(monkeypatch, EmptySpeakers())
    response = client.get("/admin/")

    assert response.status_code == 200
    assert "remote speaker" in response.text
    assert "192.168.101.222" in response.text
    assert "Registry entry is stale." in response.text


def test_switch_to_soundcork_uses_telnet_when_ssh_is_unavailable(monkeypatch):
    called_hosts: list[str] = []

    async def fake_non_rooted(host: str) -> bool:
        called_hosts.append(host)
        return True

    def fail_ssh(_host: str) -> bool:
        raise AssertionError("SSH repair should not be used")

    monkeypatch.setattr(
        "soundcork.admin.list_management_devices",
        lambda *_args, **_kwargs: management_devices_response(),
    )
    monkeypatch.setattr(
        "soundcork.admin.addr_port_is_reachable",
        lambda _host, port, timeout=2: port == 17000,
    )
    monkeypatch.setattr("soundcork.admin.override_speaker_config", fail_ssh)
    monkeypatch.setattr(
        "soundcork.admin.override_speaker_config_non_rooted", fake_non_rooted
    )
    monkeypatch.setattr("soundcork.admin.time.sleep", lambda _seconds: None)

    client, speakers, _datastore = make_client(monkeypatch)
    response = client.post(
        f"/admin/switchToSoundcork/{DEVICE_ID}", follow_redirects=False
    )

    assert response.status_code == 302
    assert response.headers["location"] == f"/admin/wait/{DEVICE_ID}/0"
    assert called_hosts == [DEVICE_IP]
    assert speakers.cleared_devices == [DEVICE_ID]


def test_admin_shows_move_account_for_configured_device(monkeypatch):
    monkeypatch.setattr(
        "soundcork.admin.list_management_devices",
        lambda *_args, **_kwargs: management_devices_response(
            marge_server="Soundcork",
            account_id=ALT_ACCOUNT_ID,
        ),
    )
    monkeypatch.setattr(
        "soundcork.admin.addr_port_is_reachable",
        lambda _host, _port, timeout=2: False,
    )

    datastore = FakeDatastore(
        accounts=[ACCOUNT_ID, ALT_ACCOUNT_ID],
        device_accounts={DEVICE_ID: [ALT_ACCOUNT_ID]},
    )
    client, _speakers, _datastore = make_client(
        monkeypatch,
        FakeSpeakers(account_id=ALT_ACCOUNT_ID),
        datastore,
    )
    response = client.get("/admin/")

    assert response.status_code == 200
    assert f"/admin/{DEVICE_ID}/setAccount" in response.text
    assert f'value="{ACCOUNT_ID}"' in response.text
    assert f'value="{ALT_ACCOUNT_ID}"' not in response.text
    assert "Move to Account" in response.text


def test_set_device_account_imports_new_account_then_removes_old(monkeypatch):
    datastore = FakeDatastore(
        accounts=[ACCOUNT_ID, ALT_ACCOUNT_ID],
        device_accounts={DEVICE_ID: [ALT_ACCOUNT_ID]},
    )
    speakers = FakeSpeakers(account_id=ALT_ACCOUNT_ID)
    posted: list[tuple[str, str]] = []
    imported: list[tuple[str, bool]] = []

    monkeypatch.setattr(
        "soundcork.admin._management_devices_by_id",
        lambda _datastore: {
            DEVICE_ID: management_devices_response(
                marge_server="Soundcork",
                account_id=ALT_ACCOUNT_ID,
            ).devices[0]
        },
    )
    monkeypatch.setattr(
        "soundcork.admin._post_speaker_account",
        lambda host, account: posted.append((host, account)) or True,
    )
    monkeypatch.setattr(
        "soundcork.admin._wait_for_speaker_account",
        lambda host, device, account: (host, device, account)
        == (DEVICE_IP, DEVICE_ID, ACCOUNT_ID),
    )
    monkeypatch.setattr(
        "soundcork.admin.addr_port_is_reachable",
        lambda _host, port, timeout=2: port == 22,
    )

    def fake_add_device_by_ip(host: str, reachable: bool) -> bool:
        imported.append((host, reachable))
        datastore.device_accounts.setdefault(DEVICE_ID, []).append(ACCOUNT_ID)
        return True

    monkeypatch.setattr("soundcork.admin.add_device_by_ip", fake_add_device_by_ip)

    assert _set_device_account(datastore, speakers, DEVICE_ID, ACCOUNT_ID) is True
    assert posted == [(DEVICE_IP, ACCOUNT_ID)]
    assert imported == [(DEVICE_IP, True)]
    assert datastore.removed_devices == [(ALT_ACCOUNT_ID, DEVICE_ID)]
    assert datastore.device_accounts[DEVICE_ID] == [ACCOUNT_ID]
    assert speakers.cleared_devices == [DEVICE_ID]


def test_set_device_account_does_not_cleanup_if_live_account_does_not_change(
    monkeypatch,
):
    datastore = FakeDatastore(
        accounts=[ACCOUNT_ID, ALT_ACCOUNT_ID],
        device_accounts={DEVICE_ID: [ALT_ACCOUNT_ID]},
    )
    speakers = FakeSpeakers(account_id=ALT_ACCOUNT_ID)

    monkeypatch.setattr(
        "soundcork.admin._management_devices_by_id",
        lambda _datastore: {
            DEVICE_ID: management_devices_response(
                marge_server="Soundcork",
                account_id=ALT_ACCOUNT_ID,
            ).devices[0]
        },
    )
    monkeypatch.setattr("soundcork.admin._post_speaker_account", lambda *_args: True)
    monkeypatch.setattr(
        "soundcork.admin._wait_for_speaker_account", lambda *_args: False
    )

    assert _set_device_account(datastore, speakers, DEVICE_ID, ACCOUNT_ID) is False
    assert datastore.removed_devices == []
    assert datastore.device_accounts[DEVICE_ID] == [ALT_ACCOUNT_ID]
    assert speakers.cleared_devices == []
