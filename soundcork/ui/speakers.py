import errno
import ipaddress
import logging
import re
import xml.etree.ElementTree as ET

from bosesoundtouchapi.models import NowPlayingStatus, Volume  # type: ignore
from bosesoundtouchapi.soundtouchclient import (  # type: ignore
    ContentItem as BCContentItem,
    SoundTouchClient,
    SoundTouchDevice,
    SoundTouchNodes,
)
from bosesoundtouchapi.soundtouchdiscovery import SoundTouchDiscovery  # type: ignore
from pydantic import BaseModel, Field
from urllib3 import PoolManager, Retry, Timeout

from soundcork.config import Settings
from soundcork.datastore import DataStore
from soundcork.model import ContentItem

logger = logging.getLogger(__name__)

SPEAKER_CONNECT_TIMEOUT = 1.0
SPEAKER_READ_TIMEOUT = 2.0
SOUNDTOUCH_DEVICE_ID_PATTERN = re.compile(r"^[0-9A-Fa-f]{12}$")


class CombinedDevice(BaseModel):
    """Device: either detected, configured, or both

    A Device that's at least one of:
    - A physical SoundTouch speaker detected on the network
    - A configured DeviceInfo block stored in the datastore.

    Property:
    - id: Bose-issued unique speaker ID from DeviceInfo
    - ip: The speaker's IP address
    - name: Human-readable speaker name
    - online: Discoverable on the network as of last-update to this object. Not updated on disconnect.
    - account: Account ID
    - in_soundcork: In the soundcork datastore
    - marge_server: API this speaker uses for Marge: (ie. Bose, or this Soundcork instance)
    - reachable:  Has been configured (ie. with a USB key) to have shell-access available.
      Equivalent to ssh_reachable and kept for compatibility.
    - rest_reachable: The speaker's HTTP /info endpoint responded.
    - ssh_reachable: The speaker has SSH open on port 22.
    - telnet_reachable: The speaker has the SoundTouch CLIServer open on port 17000.
    - repair_available: Soundcork can try to point Bose URLs at this instance.
    - playback_capability: Whether internet radio playback is currently usable.
    - st_device: SoundTouchDevice instance as discovered by BoseSoundTouchApi
    """

    id: str
    ip: str
    name: str
    online: bool
    account: str | None
    in_soundcork: bool
    marge_server: str
    reachable: bool
    st_device: SoundTouchDevice | None
    language_code: str | None = None
    rest_reachable: bool = False
    ssh_reachable: bool = False
    telnet_reachable: bool = False
    repair_available: bool = False
    marge_url: str | None = None
    source_statuses: dict[str, str] = Field(default_factory=dict)
    internet_radio_ready: bool | None = None
    playback_capability: str = "Unknown"
    playback_capability_detail: str | None = None
    state_source: str = "discovery"
    error: str | None = None

    class Config:
        arbitrary_types_allowed = True


class Speakers:
    """
    This class contains methods used to interact with speakers, primarily through the
    bosesoundtouchapi package (https://github.com/thlucas1/bosesoundtouchapi)
    """

    def __init__(self, datastore: DataStore, settings: Settings) -> None:
        self._st_discovery = SoundTouchDiscovery(areDevicesVerified=True)
        try:
            self._st_discovery.DiscoverDevices(timeout=1)
        except OSError as exc:
            if exc.errno != errno.ENODEV:
                raise
            logger.warning(
                "Initial SoundTouch discovery was interrupted by a disappearing "
                "network interface; continuing with configured devices"
            )
        self._datastore = datastore
        self._settings = settings

    def soundtouch_devices(self) -> dict:
        return self._st_discovery.VerifiedDevices

    def clear_device(self, device_id: str):
        cd = self.all_devices().get(device_id)
        if cd:
            st = cd.st_device
            if st:
                self._st_discovery.VerifiedDevices.pop(f"{st.Host}:8090")
                self._st_discovery.DiscoveredDeviceNames.pop(f"{st.Host}:8090")

    def device_by_id(self, ip_port: str) -> SoundTouchDevice:
        logger.debug(f"Getting device by id: {ip_port}")
        return self._st_discovery.VerifiedDevices.get(ip_port)

    def all_devices(self) -> dict[str, CombinedDevice]:
        """
        Returns a combination of all devices seen on the network and
        all devices configured in soundcork as a dict with the device
        id as the key
        """
        combined_devices = {}
        account_ids = self._datastore.list_accounts()
        for account_id in account_ids:
            if account_id:
                for device_id in self._datastore.list_devices(account_id):
                    if device_id:
                        device_info = self._datastore.get_device_info(
                            account_id, device_id
                        )
                        cd = CombinedDevice(
                            # If the IP changes on a device reboot, it would have made a `/power_on`
                            # call to Soundcork, which will have already updated the datastore.
                            id=device_id,
                            ip=device_info.ip_address,
                            name=device_info.name,
                            online=False,
                            account=account_id,
                            in_soundcork=True,
                            marge_server="Unknown",
                            reachable=False,
                            st_device=None,
                        )
                        combined_devices[device_id] = cd
                        logger.debug(
                            f"cd for {device_id} = {combined_devices[device_id]}"
                        )

        verified = self.soundtouch_devices()
        for key in verified.keys():
            st_device = verified[key]
            id = st_device.DeviceId
            sc_device = combined_devices.get(id, None)

            if sc_device:
                sc_device.online = True
                sc_device.st_device = st_device
            else:
                new_cd = CombinedDevice(
                    id=id,
                    ip=st_device.Host,
                    name=st_device.DeviceName,
                    online=True,
                    account=st_device.StreamingAccountUUID,
                    in_soundcork=False,
                    marge_server=st_device.StreamingUrl,
                    reachable=False,
                    st_device=st_device,
                )
                combined_devices[id] = new_cd
                sc_device = new_cd
            if st_device.StreamingUrl == "https://streaming.bose.com":
                sc_device.marge_server = "Bose"
            elif st_device.StreamingUrl == f"{self._settings.base_url}/marge":
                sc_device.marge_server = "Soundcork"
            else:
                sc_device.marge_server = f"Unknown ({st_device.StreamingUrl})"

        return combined_devices

    @staticmethod
    def _http_manager() -> PoolManager:
        """Create a request-local HTTP manager with fully bounded I/O."""
        return PoolManager(
            headers={"User-Agent": "SoundCork/1.0"},
            timeout=Timeout(
                connect=SPEAKER_CONNECT_TIMEOUT,
                read=SPEAKER_READ_TIMEOUT,
            ),
            retries=Retry(total=0, connect=0, read=0, redirect=0),
            num_pools=2,
            maxsize=2,
            block=True,
        )

    def _device_for_control(
        self, device_id: str, *, retry_generation: bool = True
    ) -> tuple[CombinedDevice | None, PoolManager]:
        """Resolve a live device, using its configured IP as a verified fallback."""
        manager = self._http_manager()
        if not SOUNDTOUCH_DEVICE_ID_PATTERN.fullmatch(device_id):
            logger.warning("Refusing invalid SoundTouch device ID %s", device_id)
            return None, manager

        combined_device = self.all_devices().get(device_id)
        if (
            not combined_device
            or combined_device.st_device
            or not combined_device.in_soundcork
            or not combined_device.ip
        ):
            return combined_device, manager

        try:
            configured_ip = ipaddress.ip_address(combined_device.ip)
        except ValueError:
            logger.warning(
                "Refusing invalid configured address %s for device %s",
                combined_device.ip,
                device_id,
            )
            return None, manager

        if (
            not isinstance(configured_ip, ipaddress.IPv4Address)
            or configured_ip.is_loopback
            or configured_ip.is_unspecified
            or configured_ip.is_multicast
        ):
            logger.warning(
                "Refusing unsafe configured address %s for device %s",
                combined_device.ip,
                device_id,
            )
            return None, manager

        expected_account = combined_device.account
        expected_ip = str(configured_ip)

        try:
            st_device = SoundTouchDevice(
                host=expected_ip,
                connectTimeout=int(SPEAKER_CONNECT_TIMEOUT),
                proxyManager=manager,
            )
        except Exception as exc:
            logger.info(
                "Configured device %s at %s is not reachable through REST: %s",
                device_id,
                expected_ip,
                exc,
            )
            return None, manager

        if st_device.DeviceId.upper() != device_id.upper():
            logger.warning(
                "Configured address %s for device %s belongs to device %s",
                expected_ip,
                device_id,
                st_device.DeviceId,
            )
            return None, manager

        current_device = self.all_devices().get(device_id)
        if (
            not current_device
            or current_device.account != expected_account
            or current_device.ip != expected_ip
        ):
            logger.info(
                "Configured location changed while resolving device %s; discarding %s",
                device_id,
                expected_ip,
            )
            if retry_generation:
                return self._device_for_control(device_id, retry_generation=False)
            return None, manager

        current_device.st_device = st_device
        current_device.online = True
        current_device.rest_reachable = True
        return current_device, manager

    def _client_for_control(
        self, device_id: str
    ) -> tuple[CombinedDevice | None, SoundTouchClient | None]:
        combined_device, manager = self._device_for_control(device_id)
        if not combined_device or not combined_device.st_device:
            return combined_device, None
        return combined_device, SoundTouchClient(
            combined_device.st_device,
            manager=manager,
        )

    def _content_item_to_soundtouchclient(self, ci: ContentItem) -> BCContentItem:
        """Maps our ContentItem to a SoundTouchClient ContentItem."""
        return BCContentItem(
            name=ci.name,
            source=ci.source,
            typeValue=ci.type,
            location=ci.location,
            sourceAccount=ci.source_account,
            isPresetable=ci.is_presetable,
        )

    def play_content_item(self, device_id: str, content_item_id: str) -> bool:
        """Play a content_item on a specific device.

        Args:
            device_id: The device ID to play on
            content_item: The content item ID to play

        Returns:
            True if successful, False otherwise
        """
        cd, client = self._client_for_control(device_id)
        if not cd or not client:
            logger.error(f"Device {device_id} not found or not online")
            return False

        if not cd.account:
            logger.error(f"Device {device_id} not associated with an account")
            return False

        content_item = self._datastore.get_content_item(
            account=cd.account,
            device_id=cd.id,
            ci_id=content_item_id,
        )
        if not content_item:
            logger.error(f"{content_item_id} is not a defined ContentItem")
            return False

        logger.info(
            f"Attempting playback of content item {content_item_id} on device {device_id}"
        )
        bose_content_item = self._content_item_to_soundtouchclient(content_item)
        try:
            client.PlayContentItem(bose_content_item, delay=0)
            return True
        except Exception:
            logger.exception(
                "Playback request for device %s failed; its outcome may be uncertain",
                device_id,
            )
            return False

    def stop_playback(self, device_id: str) -> bool:
        """Stop playback on a specific device.

        Args:
            device_id: The device ID to stop

        Returns:
            True if successful, False otherwise
        """
        cd, client = self._client_for_control(device_id)
        if not cd or not client:
            logger.error(f"Device {device_id} not found or not online")
            return False

        try:
            client.MediaStop()
            logger.info(f"Stopped playback on device {device_id}")
            return True
        except Exception as e:
            logger.error(
                "Stop request for device %s failed; its outcome may be uncertain: %s",
                device_id,
                e,
            )
            return False

    def get_volume(self, device_id: str, refresh: bool = False) -> Volume | None:
        """Get the volume of a specific speaker device

        Args:
            device_id: The device ID to play on

            refresh (bool): True to query the device for realtime information
               and refresh the cache; otherwise, False. Default False.

        Returns:
            Volume object if successful, None otherwise
        """
        cd, client = self._client_for_control(device_id)
        if not cd or not client:
            logger.error(f"Device {device_id} not found or not online")
            return None

        volume = client.GetVolume(refresh=refresh)
        logger.info(f"Volume: {volume.Actual, volume.Target, volume.IsMuted}")

        return volume

    async def set_name(self, device_id: str, name: str) -> bool:
        """Sets the name of a device.

        Args:
            device_id: The device ID of the device
            name: the new name

        Returns:
            True if successful, False otherwise
        """
        logger.info(f"setting name {device_id} to {name}")
        combined_device, st_client = self._client_for_control(device_id)
        if combined_device and st_client:
            logger.info(f"found device {combined_device.id}")
            logger.info("setting name")
            root = ET.Element("name")
            root.text = name
            payload = ET.tostring(root, "utf-8").decode()
            logger.info(f"sending set name payload {payload}")

            try:
                st_client.Put(SoundTouchNodes.name, payload)
                logger.info("set name was successful")
                return True
            except Exception:
                logger.exception(
                    "Rename request for device %s failed; its outcome may be uncertain",
                    device_id,
                )
        return False

    async def set_language(self, device_id: str, language: str) -> bool:
        """Sets the language for a device.

        Args:
            device_id: The device ID of the device
            language: the new language

        Returns:
            True if successful, False otherwise
        """
        combined_device, st_client = self._client_for_control(device_id)
        if combined_device and st_client:
            try:
                st_client.SetLanguage(language)
                return True
            except Exception:
                logger.exception(
                    "Language request for device %s failed; its outcome may be uncertain",
                    device_id,
                )
        return False

    def set_account(self, device_id: str, account_id: str) -> bool:
        """Sets the account for a device.

        Args:
            device_id: The device ID of the device
            account_id: The ID of the account

        Returns:
            True if successful, False otherwise
        """
        combined_device, st_client = self._client_for_control(device_id)

        if combined_device and st_client:
            root = ET.Element("PairDeviceWithAccount")
            ET.SubElement(root, "accountId").text = account_id
            ET.SubElement(root, "userAuthToken").text = "dontcare"
            payload = ET.tostring(root, "utf-8").decode()
            logger.info(f"sending set account payload {payload}")
            try:
                st_client.Put(SoundTouchNodes.setMargeAccount, payload)
                return True
            except Exception:
                logger.exception(
                    "Account request for device %s failed; its outcome may be uncertain",
                    device_id,
                )
        return False

    def get_now_playing_status(self, device_id: str) -> NowPlayingStatus | None:
        """Get the Now Playing information from the device.

        Args:
            device_id: The device being queried.

        Returns:
            A BoseSoundTouchApi NowPlayingStatus object.
        """
        cd, client = self._client_for_control(device_id)
        if not cd or not client:
            logger.error(f"Device {device_id} not found or not online")
            return None

        return client.GetNowPlayingStatus()

    def get_now_playing_and_volume(
        self, device_id: str
    ) -> tuple[NowPlayingStatus | None, Volume | None]:
        """Read current playback and volume through one verified, bounded client."""
        cd, client = self._client_for_control(device_id)
        if not cd or not client:
            logger.error(f"Device {device_id} not found or not online")
            return None, None

        now_playing = client.GetNowPlayingStatus()
        try:
            volume = client.GetVolume(refresh=True)
        except Exception as exc:
            logger.warning("Error getting volume for %s: %s", device_id, exc)
            volume = None
        return now_playing, volume
