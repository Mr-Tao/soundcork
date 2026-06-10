from types import SimpleNamespace

from fastapi.testclient import TestClient

from soundcork.bmx import bmx_service_by_name
from soundcork.bmx_providers import RadioBrowserCatalogProvider, TuneInProvider
from soundcork.radio_browser import (
    NAV_COUNTRIES,
    RadioBrowserProvider,
    RadioBrowserStation,
    encode_nav_uri,
    filter_playable_stations,
    sort_stations,
)


def station(
    station_uuid: str = "9610c454-0601-11e8-ae97-52543be04c81",
    name: str = "Radio Example",
    stream_url: str = "http://example.com/radio.mp3",
    codec: str = "MP3",
    bitrate: int = 128,
    lastcheckok: bool = True,
    hls: bool = False,
) -> RadioBrowserStation:
    return RadioBrowserStation(
        uuid=station_uuid,
        name=name,
        url=stream_url,
        url_resolved="",
        favicon="http://example.com/logo.png",
        homepage="http://example.com/",
        tags="radio",
        countrycode="CZ",
        codec=codec,
        bitrate=bitrate,
        hls=hls,
        lastcheckok=lastcheckok,
        votes=10,
        clickcount=20,
    )


class FakeRadioBrowserClient:
    def __init__(self) -> None:
        self.clicks: list[str] = []

    def search_stations(self, **kwargs):
        return [station()]

    def countries(self, limit: int = 50):
        return [{"name": "Czechia", "iso_3166_1": "CZ", "stationcount": 300}]

    def tags(self, limit: int = 50):
        return [{"name": "jazz", "stationcount": 20}]

    def station_by_uuid(self, station_uuid: str):
        return station(station_uuid=station_uuid, name="Original Name")

    def click_station(self, station_uuid: str):
        self.clicks.append(station_uuid)
        return {"name": "Clicked Name", "url": "http://example.com/clicked.mp3"}


def test_radio_browser_service_uses_local_soundcork_base_url(monkeypatch):
    monkeypatch.chdir("soundcork")

    service = bmx_service_by_name(
        SimpleNamespace(base_url="http://unifi:8001"), "RADIO_BROWSER"
    )

    assert service.baseUrl == "http://unifi:8001/bmx/radio-browser"
    assert service.streamTypes == ["liveRadio"]


def test_radio_browser_token_endpoint_supports_anonymous_bmx_account(monkeypatch):
    monkeypatch.chdir("soundcork")
    from soundcork.main import app

    response = TestClient(app).post(
        "/bmx/radio-browser/v1/token",
        json={"grant_type": "password", "username": "", "password": "", "is_anonymous": True},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["access_token"]
    assert payload["refresh_token"]
    assert payload["token_type"] == "Bearer"


def test_tunein_catalog_provider_delegates_search(monkeypatch):
    expected = object()

    monkeypatch.setattr(
        "soundcork.bmx_providers.tunein_search_v1",
        lambda query: expected,
    )

    assert TuneInProvider(SimpleNamespace()).search("jazz") is expected


def test_radio_browser_catalog_provider_delegates_to_radio_browser(monkeypatch):
    fake_provider = SimpleNamespace(search=lambda query: {"query": query})

    monkeypatch.setattr(
        "soundcork.bmx_providers.RadioBrowserProvider.from_settings",
        lambda settings: fake_provider,
    )

    provider = RadioBrowserCatalogProvider(SimpleNamespace())

    assert provider.search("vltava") == {"query": "vltava"}


def test_radio_browser_top_level_exposes_browse_and_station_items():
    provider = RadioBrowserProvider(FakeRadioBrowserClient())

    response = provider.navigate()

    browse = response.bmx_sections[0]
    popular = response.bmx_sections[1]

    assert browse.name == "Browse"
    assert browse.items[0].name == "Countries"
    assert browse.items[0].links.bmx_navigate.href == (
        f"/v1/navigate/{encode_nav_uri(NAV_COUNTRIES)}"
    )
    assert popular.name == "Popular stations"
    assert popular.items[0].links.bmx_playback.href == (
        "/stations/byuuid/9610c454-0601-11e8-ae97-52543be04c81"
    )
    assert popular.items[0].links.bmx_preset.href == (
        "/stations/byuuid/9610c454-0601-11e8-ae97-52543be04c81"
    )


def test_radio_browser_country_navigation_uses_country_code():
    provider = RadioBrowserProvider(FakeRadioBrowserClient())

    response = provider.navigate(encode_nav_uri(NAV_COUNTRIES))

    item = response.bmx_sections[0].items[0]
    assert item.name == "Czechia"
    assert item.subtitle == "300 stations"
    assert (
        item.links.bmx_navigate.href == f"/v1/navigate/{encode_nav_uri('country:CZ')}"
    )


def test_radio_browser_playback_counts_click_and_prefers_click_url():
    client = FakeRadioBrowserClient()
    provider = RadioBrowserProvider(client)

    response = provider.playback_station("9610c454-0601-11e8-ae97-52543be04c81")

    assert client.clicks == ["9610c454-0601-11e8-ae97-52543be04c81"]
    assert response.name == "Clicked Name"
    assert response.audio.streamUrl == "http://example.com/clicked.mp3"
    assert response.audio.streams[0].streamUrl == "http://example.com/clicked.mp3"


def test_radio_browser_station_filtering_and_sorting_prefers_playable_streams():
    broken = station(
        station_uuid="broken",
        name="Broken",
        stream_url="http://example.com/broken.mp3",
        lastcheckok=False,
    )
    flac = station(
        station_uuid="flac",
        name="FLAC",
        stream_url="http://example.com/flac.flac",
        codec="FLAC",
        bitrate=1000,
    )
    mp3 = station(
        station_uuid="mp3",
        name="MP3",
        stream_url="http://example.com/mp3.mp3",
        codec="MP3",
        bitrate=128,
    )

    playable = filter_playable_stations([broken, flac, mp3])

    assert [item.uuid for item in playable] == ["flac", "mp3"]
    assert [item.uuid for item in sort_stations(playable)] == ["mp3", "flac"]
