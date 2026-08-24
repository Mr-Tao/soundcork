"""
Handles group calls to the marge server.

Groups, in SoundTouch terminology, are ST10 devices (which are mono) paired
togeter to act as a single stereo device. If you don't have two ST10s then
you will likely never use Groups.
"""

import json
import logging
import re
import xml.etree.ElementTree as ET
from http import HTTPStatus
from ipaddress import AddressValueError, IPv4Address
from pathlib import Path as FilePath
from typing import Annotated

from fastapi import APIRouter, Path, Query, Request, Response

from soundcork.config import Settings
from soundcork.constants import ACCOUNT_RE, DEVICE_RE, GROUP_RE
from soundcork.marge import add_group, get_device_group_xml, modify_group
from soundcork.marge_paths import legacy_marge_prefix
from soundcork.model import BoseXMLResponse, Group

logger = logging.getLogger(__name__)
settings = Settings()

router = APIRouter(tags=["marge"])

BOSE_PORT = 8090
BOSE_ADDGROUP = "/addGroup"  # POST + XML
BOSE_UPDATEGROUP = "/updateGroup"  # POST + XML
BOSE_REMOVEGROUP = "/removeGroup"  # GET


def _bose_xml_str(xml: ET.Element) -> str:
    return f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>{ET.tostring(xml, encoding="unicode")}'


def _required_string(item: dict, field: str) -> str | None:
    value = item.get(field)
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def _registry_group(item: object, account: str) -> Group | None:
    if not isinstance(item, dict):
        return None

    account_id = _required_string(item, "account_id")
    group_id = _required_string(item, "group_id")
    name = _required_string(item, "name")
    master_id = _required_string(item, "master_device_id")
    roles = item.get("roles")
    if (
        account_id != account
        or not group_id
        or not re.fullmatch(GROUP_RE, group_id)
        or not name
        or not master_id
        or not isinstance(roles, dict)
        or set(roles) != {"left", "right"}
    ):
        return None

    left = roles.get("left")
    right = roles.get("right")
    if not isinstance(left, dict) or not isinstance(right, dict):
        return None

    left_id = _required_string(left, "device_id")
    left_ip = _required_string(left, "ip_address")
    right_id = _required_string(right, "device_id")
    right_ip = _required_string(right, "ip_address")
    if (
        not left_id
        or not re.fullmatch(DEVICE_RE, left_id)
        or not left_ip
        or not right_id
        or not re.fullmatch(DEVICE_RE, right_id)
        or not right_ip
        or left_id.upper() == right_id.upper()
        or master_id.upper() not in {left_id.upper(), right_id.upper()}
    ):
        return None
    try:
        IPv4Address(left_ip)
        IPv4Address(right_ip)
    except AddressValueError:
        return None

    return Group(
        id=group_id,
        name=name,
        master_id=master_id,
        left_id=left_id,
        left_ip=left_ip,
        right_id=right_id,
        right_ip=right_ip,
    )


def _load_registry_groups(registry_file: str, account: str) -> list[Group]:
    if not registry_file:
        return []

    registry_path = FilePath(registry_file)
    try:
        payload = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        logger.info("Unable to read SoundTouch registry %s: %s", registry_path, error)
        return []

    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        return []

    stereo_groups = payload.get("stereo_groups", [])
    if not isinstance(stereo_groups, list):
        return []

    groups = []
    for item in stereo_groups:
        group = _registry_group(item, account)
        if group:
            groups.append(group)
    return groups


def _group_device_ids(group: Group) -> set[str]:
    return {
        device_id.upper() for device_id in (group.left_id, group.right_id) if device_id
    }


def _registry_groups_for_read(
    account: str, registry_file: str, local_groups: list[Group]
) -> list[Group]:
    covered_device_ids = set().union(
        *(_group_device_ids(group) for group in local_groups)
    )
    registry_groups = []
    for group in _load_registry_groups(registry_file, account):
        device_ids = _group_device_ids(group)
        if device_ids & covered_device_ids:
            continue
        registry_groups.append(group)
        covered_device_ids.update(device_ids)
    return registry_groups


# ----------------------------------------------------------------------
# Factory: creates router with access to datastore (Dependency Injection)
# ----------------------------------------------------------------------
def get_groups_router(datastore, registry_file: str | None = None):
    marge = APIRouter(tags=["marge"])
    if registry_file is None:
        registry_file = settings.soundtouch_registry_file

    @marge.get(
        "/streaming/account/{account}/groups",
        response_class=BoseXMLResponse,
        tags=["marge"],
    )
    async def account_groups(
        account: Annotated[str, Path(pattern=ACCOUNT_RE)],
    ):
        """marge group endpoint to list all groups for an account"""

        groups_elem = ET.Element("groups")
        local_groups = datastore.list_groups(account)
        registry_groups = _registry_groups_for_read(
            account, registry_file, local_groups
        )
        # Registry groups are response-only; mutations still use DataStore.
        for group in local_groups + registry_groups:
            groups_elem.append(datastore.group_to_xml(group))

        return _bose_xml_str(groups_elem)

    @marge.get(
        "/streaming/account/{account}/device/{device}/group/",
        response_class=BoseXMLResponse,
        tags=["marge"],
    )
    @marge.get(
        "/streaming/account/{account}/device/{device}/group",
        response_class=BoseXMLResponse,
        tags=["marge"],
    )
    async def device_group_status(
        account: Annotated[str, Path(pattern=ACCOUNT_RE)],
        device: Annotated[str, Path(pattern=DEVICE_RE)],
    ):
        """marge group endpoint to query group per device"""

        local_groups = datastore.list_groups(account)
        registry_groups = _registry_groups_for_read(
            account, registry_file, local_groups
        )
        matching_group = next(
            (
                group
                for group in local_groups + registry_groups
                if device.upper() in _group_device_ids(group)
            ),
            None,
        )
        result = (
            datastore.group_to_xml(matching_group)
            if matching_group
            else get_device_group_xml(datastore, account, device)
        )

        return _bose_xml_str(result)

    @marge.post(
        "/streaming/account/{account}/group/",
        response_class=BoseXMLResponse,
        tags=["marge"],
    )
    @marge.post(
        "/streaming/account/{account}/group",
        response_class=BoseXMLResponse,
        tags=["marge"],
    )
    async def add_group_endpoint(
        account: Annotated[str, Path(pattern=ACCOUNT_RE)],
        request: Request,
        response: Response,
    ) -> str:

        reqxml_bytes = await request.body()
        reqxml_str = reqxml_bytes.decode("utf-8")

        result = add_group(datastore, account, reqxml_str)
        response.status_code = HTTPStatus.CREATED
        group_id = result.get("id")
        if group_id:
            base_url = str(request.base_url).rstrip("/")
            legacy_prefix = legacy_marge_prefix(request.url.path)
            response.headers["Location"] = (
                f"{base_url}{legacy_prefix}/streaming/account/{account}/group/{group_id}"
            )

        return _bose_xml_str(result)

    @marge.put(
        "/streaming/account/{account}/group/{group}",
        response_class=BoseXMLResponse,
        tags=["marge"],
    )
    @marge.post(
        "/streaming/account/{account}/group/{group}",
        response_class=BoseXMLResponse,
        tags=["marge"],
    )
    async def mod_group_endpoint(
        account: Annotated[str, Path(pattern=ACCOUNT_RE)],
        group: Annotated[str, Path(pattern=GROUP_RE)],
        request: Request,
        response: Response,
    ):
        """marge group endpoint to add group"""
        try:
            body = await request.body()
            xml_str = body.decode("utf-8")
            result = modify_group(datastore, account, group, xml_str)

            return _bose_xml_str(result)

        except ET.ParseError:
            response.status_code = HTTPStatus.BAD_REQUEST
            return ("<error>Invalid XML payload</error>",)
        except UnicodeDecodeError:
            response.status_code = HTTPStatus.BAD_REQUEST
            return "<error>Invalid UTF-8 in request body</error>"

    @marge.delete(
        "/streaming/account/{account}/group/",
        response_class=BoseXMLResponse,
        tags=["marge"],
    )
    async def delete_account_groups_endpoint(
        account: Annotated[str, Path(pattern=ACCOUNT_RE)],
    ):
        """marge group endpoint to delete all account groups"""
        try:
            error = datastore.delete_all_groups(account)
            if error:
                return BoseXMLResponse(
                    content=f"<error>{error}</error>",
                    status_code=HTTPStatus.BAD_REQUEST,
                )
            return BoseXMLResponse(
                content="<status>Groups deleted successfully</status>"
            )
        except Exception as e:
            return BoseXMLResponse(
                content=f"<error>Unexpected error: {e}</error>", status_code=500
            )

    @marge.delete(
        "/streaming/account/{account}/group/{group}",
        response_class=BoseXMLResponse,
        tags=["marge"],
    )
    async def delete_group_endpoint(
        account: Annotated[str, Path(pattern=ACCOUNT_RE)],
        group: Annotated[str, Path(pattern=GROUP_RE)],
    ):
        """marge group endpoint to delete group"""
        if not datastore.account_exists(account):
            return BoseXMLResponse(
                content=f"<error>Account {account} not found</error>",
                status_code=HTTPStatus.BAD_REQUEST,
            )

        try:
            error = datastore.delete_group(account, group)
            if error:
                return BoseXMLResponse(
                    content=f"<error>{error}</error>",
                    status_code=HTTPStatus.BAD_REQUEST,
                )
            return BoseXMLResponse(
                content=f"<status>Group {group} deleted successfully</status>"
            )
        except Exception as e:
            return BoseXMLResponse(
                content=f"<error>Unexpected error: {e}</error>", status_code=500
            )

    return marge
