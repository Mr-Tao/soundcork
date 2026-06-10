from typing import Protocol

from soundcork.bmx import (
    bmx_service_by_name,
    tunein_navigate_profile_v1,
    tunein_navigate_v1,
    tunein_playback,
    tunein_playback_podcast,
    tunein_podcast_info,
    tunein_search_v1,
)
from soundcork.config import Settings
from soundcork.model import (
    BmxNavResponse,
    BmxPlaybackResponse,
    BmxPodcastInfoResponse,
    Service,
)
from soundcork.radio_browser import RadioBrowserProvider


class BmxCatalogProvider(Protocol):
    service_name: str

    def service(self) -> Service: ...

    def navigate(
        self, encoded_uri: str = "", subsection: int | None = None
    ) -> BmxNavResponse: ...

    def search(self, query: str) -> BmxNavResponse: ...

    def playback_station(self, station_id: str) -> BmxPlaybackResponse: ...


class TuneInProvider:
    service_name = "TUNEIN"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def service(self) -> Service:
        return bmx_service_by_name(self.settings, self.service_name)

    def navigate(
        self, encoded_uri: str = "", subsection: int | None = None
    ) -> BmxNavResponse:
        return tunein_navigate_v1(encoded_uri, subsection)

    def navigate_profile(self, encoded_uri: str = "") -> BmxNavResponse:
        return tunein_navigate_profile_v1(encoded_uri)

    def search(self, query: str) -> BmxNavResponse:
        return tunein_search_v1(query)

    def playback_station(self, station_id: str) -> BmxPlaybackResponse:
        return tunein_playback(station_id)

    def podcast_info(
        self, episode_id: str, encoded_name: str
    ) -> BmxPodcastInfoResponse:
        return tunein_podcast_info(episode_id, encoded_name)

    def playback_podcast(self, episode_id: str) -> BmxPlaybackResponse:
        return tunein_playback_podcast(episode_id)


class RadioBrowserCatalogProvider:
    service_name = RadioBrowserProvider.service_name

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.provider = RadioBrowserProvider.from_settings(settings)

    def service(self) -> Service:
        return bmx_service_by_name(self.settings, self.service_name)

    def navigate(
        self, encoded_uri: str = "", subsection: int | None = None
    ) -> BmxNavResponse:
        return self.provider.navigate(encoded_uri, subsection)

    def search(self, query: str) -> BmxNavResponse:
        return self.provider.search(query)

    def playback_station(self, station_id: str) -> BmxPlaybackResponse:
        return self.provider.playback_station(station_id)
