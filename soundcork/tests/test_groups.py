import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from soundcork.constants import DEVICE_INFO_FILE
from soundcork.datastore import DataStore
from soundcork.groups import get_groups_router
from soundcork.marge import get_device_group_xml


def _datastore(tmp_path: Path, monkeypatch) -> DataStore:
    monkeypatch.setattr("soundcork.datastore.settings.data_dir", str(tmp_path))
    return DataStore()


def _write_st10(
    datastore: DataStore,
    account: str,
    device_id: str,
    ip_address: str,
    name: str,
    product_code: str = "SoundTouch 10 sm2",
) -> None:
    device_dir = Path(datastore.account_devices_dir(account)) / device_id
    device_dir.mkdir(parents=True, exist_ok=True)
    (device_dir / DEVICE_INFO_FILE).write_text(
        f"""
        <info deviceID="{device_id}">
            <name>{name}</name>
            <type>{product_code}</type>
            <components>
                <component>
                    <componentCategory>SCM</componentCategory>
                    <softwareVersion>27.0.0</softwareVersion>
                    <serialNumber>{device_id}</serialNumber>
                </component>
                <component>
                    <componentCategory>PackagedProduct</componentCategory>
                    <serialNumber>{device_id}</serialNumber>
                </component>
            </components>
            <networkInfo type="SCM">
                <macAddress>{device_id}</macAddress>
                <ipAddress>{ip_address}</ipAddress>
            </networkInfo>
        </info>
        """,
        encoding="utf-8",
    )


def _group_payload(
    left_id: str = "AABBCCDDEEFF",
    right_id: str = "112233445566",
) -> str:
    return f"""
    <group>
        <name>Loznice pair</name>
        <masterDeviceId>{left_id}</masterDeviceId>
        <roles>
            <groupRole>
                <deviceId>{left_id}</deviceId>
                <role>LEFT</role>
            </groupRole>
            <groupRole>
                <deviceId>{right_id}</deviceId>
                <role>RIGHT</role>
            </groupRole>
        </roles>
    </group>
    """


def _registry_group(
    account: str = "12345",
    group_id: str = "7654321",
    left_id: str = "AABBCCDDEEFF",
    right_id: str = "112233445566",
) -> dict:
    return {
        "key": "loznice-stereo",
        "owner_site": "udm",
        "current_site": "udm",
        "account_id": account,
        "group_id": group_id,
        "name": "Loznice pair",
        "master_device_id": left_id,
        "roles": {
            "left": {"device_id": left_id, "ip_address": "192.0.2.10"},
            "right": {"device_id": right_id, "ip_address": "192.0.2.11"},
        },
        "observed_at": "2026-07-19T10:37:56+0200",
    }


def _write_registry(registry_file: Path, stereo_groups: object) -> None:
    registry_file.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generated_at": "2026-07-19T10:38:00+0200",
                "site": "udr",
                "sites": {},
                "speakers": [],
                "stereo_groups": stereo_groups,
            }
        ),
        encoding="utf-8",
    )


def test_group_datastore_roundtrip_uses_group_id_from_filename(tmp_path, monkeypatch):
    account = "12345"
    left_id = "AABBCCDDEEFF"
    right_id = "112233445566"
    datastore = _datastore(tmp_path, monkeypatch)
    _write_st10(datastore, account, left_id, "192.0.2.10", "Left")
    _write_st10(datastore, account, right_id, "192.0.2.11", "Right")

    group = datastore.group_from_xml("", ET.fromstring(_group_payload()))
    group_xml = datastore.add_group(account, group)

    group_id = group_xml.get("id")
    assert group_id
    assert (tmp_path / account / "devices" / f"Group_{group_id}.xml").exists()

    groups = datastore.list_groups(account)
    assert [group.id for group in groups] == [group_id]
    assert groups[0].left_ip == "192.0.2.10"
    assert groups[0].right_ip == "192.0.2.11"
    assert datastore.group_for_device(account, left_id).id == group_id
    assert datastore.group_to_xml(groups[0]).get("id") == group_id


def test_get_device_group_xml_returns_empty_group_for_ungrouped_st10(
    tmp_path, monkeypatch
):
    account = "12345"
    device_id = "AABBCCDDEEFF"
    datastore = _datastore(tmp_path, monkeypatch)
    _write_st10(datastore, account, device_id, "192.0.2.10", "Left")

    group_xml = get_device_group_xml(datastore, account, device_id)

    assert group_xml.tag == "group"
    assert group_xml.attrib == {}
    assert list(group_xml) == []


def test_marge_group_routes_match_stockholm_and_speaker_shapes(tmp_path, monkeypatch):
    account = "12345"
    left_id = "AABBCCDDEEFF"
    right_id = "112233445566"
    datastore = _datastore(tmp_path, monkeypatch)
    _write_st10(datastore, account, left_id, "192.0.2.10", "Left")
    _write_st10(datastore, account, right_id, "192.0.2.11", "Right")
    app = FastAPI()
    app.include_router(get_groups_router(datastore))
    client = TestClient(app)

    created = client.post(
        f"/marge/streaming/account/{account}/group/",
        content=_group_payload(),
        headers={"Content-Type": "application/vnd.bose.streaming-v1.2+xml"},
    )

    assert created.status_code == 201
    created_xml = ET.fromstring(created.text)
    group_id = created_xml.get("id")
    assert group_id
    assert created.headers["location"].endswith(f"/group/{group_id}")

    device_group = client.get(
        f"/marge/streaming/account/{account}/device/{left_id}/group/"
    )
    assert device_group.status_code == 200
    assert ET.fromstring(device_group.text).get("id") == group_id

    groups = client.get(f"/marge/streaming/account/{account}/groups")
    assert groups.status_code == 200
    groups_xml = ET.fromstring(groups.text)
    assert groups_xml.tag == "groups"
    assert groups_xml.find("group").get("id") == group_id

    renamed = client.put(
        f"/marge/streaming/account/{account}/group/{group_id}",
        content=f"""
        <group id="{group_id}">
            <name>Renamed pair</name>
            <masterDeviceId>{left_id}</masterDeviceId>
        </group>
        """,
    )
    assert renamed.status_code == 200
    assert ET.fromstring(renamed.text).findtext("name") == "Renamed pair"

    deleted = client.delete(f"/marge/streaming/account/{account}/group/")
    assert deleted.status_code == 200
    assert datastore.list_groups(account) == []

    ungrouped = client.get(f"/marge/streaming/account/{account}/device/{left_id}/group")
    assert ungrouped.status_code == 200
    assert ET.fromstring(ungrouped.text).tag == "group"
    assert list(ET.fromstring(ungrouped.text)) == []


def test_account_groups_includes_remote_registry_overlay(tmp_path, monkeypatch):
    account = "12345"
    registry_file = tmp_path / "registry.json"
    _write_registry(registry_file, [_registry_group(account=account)])
    registry_contents = registry_file.read_text(encoding="utf-8")
    datastore = _datastore(tmp_path / "data", monkeypatch)
    app = FastAPI()
    app.include_router(get_groups_router(datastore, str(registry_file)))
    client = TestClient(app)

    response = client.get(f"/marge/streaming/account/{account}/groups")

    assert response.status_code == 200
    groups = ET.fromstring(response.text).findall("group")
    assert [group.get("id") for group in groups] == ["7654321"]
    assert groups[0].findtext("name") == "Loznice pair"
    assert groups[0].findtext("masterDeviceId") == "AABBCCDDEEFF"
    assert datastore.list_groups(account) == []

    deleted = client.delete(f"/marge/streaming/account/{account}/group/")
    visible_after_delete = client.get(f"/marge/streaming/account/{account}/groups")
    assert deleted.status_code == 200
    assert ET.fromstring(visible_after_delete.text).find("group").get("id") == "7654321"
    assert registry_file.read_text(encoding="utf-8") == registry_contents


def test_local_group_wins_over_registry_group_with_changed_id(tmp_path, monkeypatch):
    account = "12345"
    left_id = "AABBCCDDEEFF"
    right_id = "112233445566"
    registry_file = tmp_path / "registry.json"
    _write_registry(
        registry_file,
        [
            _registry_group(
                account=account,
                group_id="7654321",
                left_id=left_id,
                right_id=right_id,
            )
        ],
    )
    datastore = _datastore(tmp_path / "data", monkeypatch)
    _write_st10(datastore, account, left_id, "192.0.2.10", "Left")
    _write_st10(datastore, account, right_id, "192.0.2.11", "Right")
    monkeypatch.setattr(datastore, "_generate_group_id", lambda _account: "1234567")
    local_group = datastore.group_from_xml("", ET.fromstring(_group_payload()))
    datastore.add_group(account, local_group)
    app = FastAPI()
    app.include_router(get_groups_router(datastore, str(registry_file)))
    client = TestClient(app)

    response = client.get(f"/marge/streaming/account/{account}/groups")
    device_response = client.get(
        f"/marge/streaming/account/{account}/device/{left_id}/group"
    )

    groups = ET.fromstring(response.text).findall("group")
    assert [group.get("id") for group in groups] == ["1234567"]
    assert ET.fromstring(device_response.text).get("id") == "1234567"


def test_registry_groups_are_filtered_by_account(tmp_path, monkeypatch):
    registry_file = tmp_path / "registry.json"
    _write_registry(registry_file, [_registry_group(account="67890")])
    datastore = _datastore(tmp_path / "data", monkeypatch)
    app = FastAPI()
    app.include_router(get_groups_router(datastore, str(registry_file)))

    response = TestClient(app).get("/marge/streaming/account/12345/groups")

    assert response.status_code == 200
    assert ET.fromstring(response.text).findall("group") == []


def test_device_group_includes_remote_registry_overlay(tmp_path, monkeypatch):
    account = "12345"
    device_id = "112233445566"
    registry_file = tmp_path / "registry.json"
    _write_registry(registry_file, [_registry_group(account=account)])
    datastore = _datastore(tmp_path / "data", monkeypatch)
    app = FastAPI()
    app.include_router(get_groups_router(datastore, str(registry_file)))

    response = TestClient(app).get(
        f"/marge/streaming/account/{account}/device/{device_id}/group"
    )

    assert response.status_code == 200
    assert ET.fromstring(response.text).get("id") == "7654321"
    assert datastore.list_groups(account) == []


@pytest.mark.parametrize(
    "registry_contents",
    [
        None,
        "{not-json",
        json.dumps({"schema_version": 1}),
        json.dumps({"schema_version": 1, "stereo_groups": {}}),
        json.dumps({"schema_version": 2, "stereo_groups": [_registry_group()]}),
        json.dumps({"schema_version": 1, "stereo_groups": [{"group_id": "1"}]}),
    ],
    ids=[
        "missing-file",
        "invalid-json",
        "absent-stereo-groups",
        "invalid-stereo-groups",
        "unsupported-schema",
        "malformed-group",
    ],
)
def test_missing_or_malformed_registry_is_an_empty_overlay(
    tmp_path, monkeypatch, registry_contents
):
    registry_file = tmp_path / "registry.json"
    if registry_contents is not None:
        registry_file.write_text(registry_contents, encoding="utf-8")
    datastore = _datastore(tmp_path / "data", monkeypatch)
    app = FastAPI()
    app.include_router(get_groups_router(datastore, str(registry_file)))

    response = TestClient(app).get("/marge/streaming/account/12345/groups")

    assert response.status_code == 200
    assert ET.fromstring(response.text).findall("group") == []
