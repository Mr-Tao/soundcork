import base64
import json
import logging
import random
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from soundcork.config import Settings
from soundcork.model import (
    Audio,
    BmxNavItem,
    BmxNavResponse,
    BmxNavSection,
    BmxPlaybackResponse,
    Stream,
)

logger = logging.getLogger(__name__)

DEFAULT_API_BASE = "https://all.api.radio-browser.info"
DEFAULT_USER_AGENT = "SoundCork/1.0 (+https://github.com/deborahgu/soundcork)"
DEFAULT_LIMIT = 50

NAV_COUNTRIES = "countries"
NAV_COUNTRY_PREFIX = "country:"
NAV_TAGS = "tags"
NAV_TAG_PREFIX = "tag:"


@dataclass(frozen=True)
class RadioBrowserStation:
    uuid: str
    name: str
    url: str
    url_resolved: str
    favicon: str
    homepage: str
    tags: str
    countrycode: str
    codec: str
    bitrate: int
    hls: bool
    lastcheckok: bool
    votes: int
    clickcount: int

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "RadioBrowserStation":
        return cls(
            uuid=str(data.get("stationuuid", "")).strip(),
            name=str(data.get("name", "")).strip(),
            url=str(data.get("url", "")).strip(),
            url_resolved=str(data.get("url_resolved", "")).strip(),
            favicon=str(data.get("favicon", "")).strip(),
            homepage=str(data.get("homepage", "")).strip(),
            tags=str(data.get("tags", "")).strip(),
            countrycode=str(data.get("countrycode", "")).strip(),
            codec=str(data.get("codec", "")).strip().upper(),
            bitrate=int_value(data.get("bitrate")),
            hls=bool_value(data.get("hls")),
            lastcheckok=bool_value(data.get("lastcheckok")),
            votes=int_value(data.get("votes")),
            clickcount=int_value(data.get("clickcount")),
        )

    @property
    def stream_url(self) -> str:
        return self.url_resolved or self.url

    @property
    def subtitle(self) -> str:
        parts = []
        if self.countrycode:
            parts.append(self.countrycode)
        if self.codec:
            parts.append(self.codec)
        if self.bitrate > 0:
            parts.append(f"{self.bitrate} kbps")
        if self.hls:
            parts.append("HLS")
        return " - ".join(parts)

    @property
    def has_playlist(self) -> bool:
        if self.hls:
            return True
        path = urllib.parse.urlsplit(self.stream_url).path.lower()
        return path.endswith((".m3u", ".m3u8", ".pls", ".asx", ".xspf"))


class RadioBrowserClient:
    def __init__(
        self,
        base_urls: list[str] | None = None,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout: int = 10,
    ) -> None:
        self.base_urls = [
            url.rstrip("/") for url in (base_urls or [DEFAULT_API_BASE]) if url.strip()
        ]
        if not self.base_urls:
            self.base_urls = [DEFAULT_API_BASE]
        self.user_agent = user_agent
        self.timeout = timeout

    @classmethod
    def from_settings(cls, settings: Settings) -> "RadioBrowserClient":
        raw_base_urls = getattr(settings, "radio_browser_api_base_urls", "")
        base_urls = [url.strip() for url in raw_base_urls.split(",") if url.strip()]
        user_agent = getattr(settings, "radio_browser_user_agent", "")
        return cls(
            base_urls=base_urls or None,
            user_agent=user_agent.strip() or DEFAULT_USER_AGENT,
        )

    def countries(self, limit: int = DEFAULT_LIMIT) -> list[dict[str, Any]]:
        rows = self.request_json(
            "/json/countries",
            {
                "hidebroken": "true",
                "order": "stationcount",
                "reverse": "true",
                "limit": str(limit),
            },
        )
        return rows if isinstance(rows, list) else []

    def tags(self, limit: int = DEFAULT_LIMIT) -> list[dict[str, Any]]:
        rows = self.request_json(
            "/json/tags",
            {
                "hidebroken": "true",
                "order": "stationcount",
                "reverse": "true",
                "limit": str(limit),
            },
        )
        return rows if isinstance(rows, list) else []

    def search_stations(
        self,
        *,
        name: str = "",
        countrycode: str = "",
        tag: str = "",
        limit: int = DEFAULT_LIMIT,
    ) -> list[RadioBrowserStation]:
        params = {
            "hidebroken": "true",
            "order": "clickcount",
            "reverse": "true",
            "limit": str(limit),
        }
        if name:
            params["name"] = name
        if countrycode:
            params["countrycode"] = countrycode
        if tag:
            params["tag"] = tag

        rows = self.request_json("/json/stations/search", params)
        if not isinstance(rows, list):
            return []
        stations = [RadioBrowserStation.from_api(row) for row in rows]
        return sort_stations(filter_playable_stations(stations))

    def station_by_uuid(self, station_uuid: str) -> RadioBrowserStation | None:
        rows = self.request_json("/json/stations/byuuid", {"uuids": station_uuid})
        if not isinstance(rows, list) or not rows:
            return None
        stations = sort_stations(
            filter_playable_stations(
                [RadioBrowserStation.from_api(row) for row in rows]
            )
        )
        return stations[0] if stations else None

    def click_station(self, station_uuid: str) -> dict[str, Any]:
        rows = self.request_json(f"/json/url/{urllib.parse.quote(station_uuid)}")
        return rows if isinstance(rows, dict) else {}

    def request_json(self, path: str, params: dict[str, str] | None = None) -> Any:
        urls = self.base_urls[:]
        random.shuffle(urls)
        last_error: Exception | None = None
        for base_url in urls:
            url = build_api_url(base_url, path, params)
            request = urllib.request.Request(
                url,
                headers={"User-Agent": self.user_agent, "Accept": "application/json"},
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return json.loads(response.read().decode("utf-8"))
            except Exception as exc:
                last_error = exc
                logger.warning("Radio Browser request failed for %s: %s", url, exc)
        if last_error:
            raise last_error
        return None


class RadioBrowserProvider:
    service_name = "RADIO_BROWSER"

    def __init__(self, client: RadioBrowserClient) -> None:
        self.client = client

    @classmethod
    def from_settings(cls, settings: Settings) -> "RadioBrowserProvider":
        return cls(RadioBrowserClient.from_settings(settings))

    def navigate(
        self, encoded_uri: str = "", subsection: int | None = None
    ) -> BmxNavResponse:
        if not encoded_uri:
            return self.top_level()

        nav_uri = decode_nav_uri(encoded_uri)
        if nav_uri == NAV_COUNTRIES:
            return self.country_list()
        if nav_uri == NAV_TAGS:
            return self.tag_list()
        if nav_uri.startswith(NAV_COUNTRY_PREFIX):
            countrycode = nav_uri.removeprefix(NAV_COUNTRY_PREFIX)
            return self.station_list(
                title=f"Popular stations - {countrycode}",
                stations=self.client.search_stations(countrycode=countrycode),
                self_href=f"/v1/navigate/{encoded_uri}",
            )
        if nav_uri.startswith(NAV_TAG_PREFIX):
            tag = nav_uri.removeprefix(NAV_TAG_PREFIX)
            return self.station_list(
                title=f"Popular stations - {tag}",
                stations=self.client.search_stations(tag=tag),
                self_href=f"/v1/navigate/{encoded_uri}",
            )

        return self.top_level()

    def search(self, query: str) -> BmxNavResponse:
        return self.station_list(
            title="Search results",
            stations=self.client.search_stations(name=query),
            self_href=f"/v1/search?{urllib.parse.urlencode({'q': query})}",
            include_search=True,
        )

    def playback_station(self, station_uuid: str) -> BmxPlaybackResponse:
        station = self.client.station_by_uuid(station_uuid)
        click_info = self.client.click_station(station_uuid)
        if station is None and not click_info:
            raise LookupError(f"Unknown Radio Browser station {station_uuid}")

        name = str(click_info.get("name", "")).strip()
        stream_url = str(click_info.get("url", "")).strip()
        if station is not None:
            name = name or station.name
            stream_url = stream_url or station.stream_url
            image_url = station.favicon
            has_playlist = station.has_playlist
        else:
            image_url = ""
            has_playlist = is_playlist_url(stream_url)

        if not stream_url:
            raise LookupError(f"Radio Browser station {station_uuid} has no stream URL")

        reporting_href = (
            f"/v1/report?{urllib.parse.urlencode({'stationuuid': station_uuid})}"
        )
        stream = Stream(
            links={"bmx_reporting": {"href": reporting_href}},
            bufferingTimeout=20,
            connectingTimeout=10,
            hasPlaylist=has_playlist,
            isRealtime=True,
            maxTimeout=60,
            streamUrl=stream_url,
        )
        return BmxPlaybackResponse(
            links={
                "bmx_reporting": {"href": reporting_href},
                "bmx_nowplaying": {
                    "href": f"/v1/now-playing/station/{station_uuid}",
                    "useInternalClient": "ALWAYS",
                },
            },
            audio=Audio(
                hasPlaylist=has_playlist,
                isRealtime=True,
                maxTimeout=60,
                streamUrl=stream_url,
                streams=[stream],
            ),
            imageUrl=image_url,
            isFavorite=False,
            name=name or station_uuid,
            streamType="liveRadio",
        )

    def top_level(self) -> BmxNavResponse:
        sections = [
            BmxNavSection(
                links={"self": {"href": "/v1/navigate"}},
                items=[
                    navigate_item("Countries", "Browse by country", NAV_COUNTRIES),
                    navigate_item("Tags", "Browse by tag", NAV_TAGS),
                ],
                layout="list",
                name="Browse",
            ),
            station_section(
                name="Popular stations",
                stations=self.client.search_stations(limit=20),
                layout="responsiveGrid",
                self_href="/v1/navigate",
            ),
        ]
        return BmxNavResponse(
            links=top_links("/v1/navigate"),
            bmx_sections=sections,
            layout="classic",
        )

    def country_list(self) -> BmxNavResponse:
        items = []
        for country in self.client.countries(limit=100):
            code = str(country.get("iso_3166_1", "")).strip()
            if not code:
                continue
            count = country.get("stationcount", "")
            items.append(
                navigate_item(
                    str(country.get("name", code)).strip(),
                    f"{count} stations" if count != "" else "",
                    NAV_COUNTRY_PREFIX + code,
                )
            )
        return BmxNavResponse(
            links=top_links(f"/v1/navigate/{encode_nav_uri(NAV_COUNTRIES)}"),
            bmx_sections=[
                BmxNavSection(
                    links={
                        "self": {
                            "href": f"/v1/navigate/{encode_nav_uri(NAV_COUNTRIES)}"
                        }
                    },
                    items=items,
                    layout="list",
                    name="Countries",
                )
            ],
            layout="classic",
        )

    def tag_list(self) -> BmxNavResponse:
        items = []
        for tag in self.client.tags(limit=100):
            name = str(tag.get("name", "")).strip()
            if not name:
                continue
            count = tag.get("stationcount", "")
            items.append(
                navigate_item(
                    name,
                    f"{count} stations" if count != "" else "",
                    NAV_TAG_PREFIX + name,
                )
            )
        return BmxNavResponse(
            links=top_links(f"/v1/navigate/{encode_nav_uri(NAV_TAGS)}"),
            bmx_sections=[
                BmxNavSection(
                    links={
                        "self": {"href": f"/v1/navigate/{encode_nav_uri(NAV_TAGS)}"}
                    },
                    items=items,
                    layout="list",
                    name="Tags",
                )
            ],
            layout="classic",
        )

    def station_list(
        self,
        title: str,
        stations: list[RadioBrowserStation],
        self_href: str,
        include_search: bool = True,
    ) -> BmxNavResponse:
        return BmxNavResponse(
            links=top_links(self_href, include_search=include_search),
            bmx_sections=[
                station_section(
                    name=title,
                    stations=stations,
                    layout="responsiveGrid",
                    self_href=self_href,
                )
            ],
            layout="classic",
        )


def build_api_url(
    base_url: str, path: str, params: dict[str, str] | None = None
) -> str:
    url = urllib.parse.urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
    if params:
        url += "?" + urllib.parse.urlencode(params)
    return url


def top_links(self_href: str, include_search: bool = True) -> dict[str, Any]:
    return {
        "self": {"href": self_href},
        "bmx_search": (
            {
                "filters": [],
                "href": "/v1/search?q={query}",
                "templated": True,
            }
            if include_search
            else None
        ),
        "filters": None,
    }


def station_section(
    name: str,
    stations: list[RadioBrowserStation],
    layout: str,
    self_href: str,
) -> BmxNavSection:
    return BmxNavSection(
        links={"self": {"href": self_href}},
        items=[station_item(station) for station in stations],
        layout=layout,
        name=name,
    )


def station_item(station: RadioBrowserStation) -> BmxNavItem:
    href = f"/stations/byuuid/{station.uuid}"
    return BmxNavItem(
        links={
            "bmx_playback": {
                "href": href,
                "type": "stationurl",
            },
            "bmx_preset": {
                "container_art": station.favicon,
                "href": href,
                "name": station.name,
                "type": "stationurl",
            },
        },
        image_url=station.favicon,
        name=station.name,
        subtitle=station.subtitle,
    )


def navigate_item(name: str, subtitle: str, nav_uri: str) -> BmxNavItem:
    return BmxNavItem(
        links={"bmx_navigate": {"href": f"/v1/navigate/{encode_nav_uri(nav_uri)}"}},
        image_url="",
        name=name,
        subtitle=subtitle,
    )


def encode_nav_uri(nav_uri: str) -> str:
    return base64.urlsafe_b64encode(nav_uri.encode("utf-8")).decode("ascii")


def decode_nav_uri(encoded_uri: str) -> str:
    return base64.urlsafe_b64decode(encoded_uri.encode("ascii")).decode("utf-8")


def filter_playable_stations(
    stations: list[RadioBrowserStation],
) -> list[RadioBrowserStation]:
    return [
        station
        for station in stations
        if station.uuid
        and station.name
        and station.stream_url
        and station.lastcheckok
        and station.stream_url.startswith(("http://", "https://"))
    ]


def sort_stations(stations: list[RadioBrowserStation]) -> list[RadioBrowserStation]:
    return sorted(stations, key=station_sort_key, reverse=True)


def station_sort_key(station: RadioBrowserStation) -> tuple[int, int, int, int]:
    codec_score = {
        "MP3": 5,
        "AAC": 5,
        "AAC+": 4,
        "OGG": 3,
        "VORBIS": 3,
        "FLAC": 2,
    }.get(station.codec, 1)
    hls_penalty = -1 if station.hls else 0
    return (
        codec_score + hls_penalty,
        station.bitrate,
        station.clickcount,
        station.votes,
    )


def is_playlist_url(stream_url: str) -> bool:
    path = urllib.parse.urlsplit(stream_url).path.lower()
    return path.endswith((".m3u", ".m3u8", ".pls", ".asx", ".xspf"))


def int_value(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def bool_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes"}
    return False
