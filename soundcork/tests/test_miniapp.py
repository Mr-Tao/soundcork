from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from fastapi import FastAPI
from fastapi.testclient import TestClient

from soundcork.miniapp import (
    STARTED_OPTIMISTIC_SECONDS,
    get_device_image,
    get_miniapp_router,
    miniapp_artwork_url,
    use_started_state,
)
from soundcork.model import Preset

ACCOUNT_ID = "8208423"
DEVICE_ID = "device-1"
STARTED_AT = 1000.0
STARTED_AT_QUERY = "1000"


def inside_started_window() -> float:
    return STARTED_AT + STARTED_OPTIMISTIC_SECONDS / 2


def outside_started_window() -> float:
    return STARTED_AT + STARTED_OPTIMISTIC_SECONDS + 1.0


class FakeDatastore:
    def __init__(
        self,
        content_source: str = "LOCAL_INTERNET_RADIO",
        container_art: str = "",
    ) -> None:
        self.content_source = content_source
        self.container_art = container_art

    def account_exists(self, account_id: str) -> bool:
        return account_id == ACCOUNT_ID

    def list_accounts(self) -> list[str]:
        return [ACCOUNT_ID]

    def get_account_info(self, account_id: str) -> str:
        assert account_id == ACCOUNT_ID
        return "Účet ložnice"

    def list_devices(self, account_id: str) -> list[str]:
        assert account_id == ACCOUNT_ID
        return [DEVICE_ID]

    def get_device_info(self, account_id: str, device_id: str):
        assert account_id == ACCOUNT_ID
        assert device_id == DEVICE_ID
        return SimpleNamespace(
            name="ložnice",
            product_code="SoundTouch10",
            device_id=DEVICE_ID,
        )

    def get_content_item(self, account: str, device_id: str, ci_id: str):
        assert account == ACCOUNT_ID
        assert device_id == DEVICE_ID
        return SimpleNamespace(source=self.content_source)

    def get_presets(self, account_id: str) -> list[Preset]:
        assert account_id == ACCOUNT_ID
        return [
            Preset(
                id="4",
                name="Rádio Proglas",
                source="LOCAL_INTERNET_RADIO",
                type="STORED_MUSIC",
                location="proglas",
                container_art=self.container_art,
            )
        ]


class FakeSpeakers:
    def __init__(
        self,
        play_result: bool = True,
        now_playing_status=None,
        online: bool = True,
    ) -> None:
        self.play_result = play_result
        self.now_playing_status = now_playing_status
        self.online = online
        self.play_calls: list[tuple[str, str]] = []
        self.now_playing_calls: list[str] = []

    def all_devices(self):
        return {
            DEVICE_ID: SimpleNamespace(
                account=ACCOUNT_ID,
                online=self.online,
                in_soundcork=True,
                marge_server="Soundcork",
            )
        }

    def play_content_item(self, device_id: str, content_item_id: str) -> bool:
        self.play_calls.append((device_id, content_item_id))
        return self.play_result

    def get_now_playing_status(self, device_id: str):
        assert device_id == DEVICE_ID
        self.now_playing_calls.append(device_id)
        return self.now_playing_status

    def get_volume(self, device_id: str):
        assert device_id == DEVICE_ID
        return SimpleNamespace(Actual=23, Target=23, IsMuted=False)

    def get_now_playing_and_volume(self, device_id: str):
        return (
            self.get_now_playing_status(device_id),
            self.get_volume(device_id),
        )

    def stop_playback(self, device_id: str) -> bool:
        assert device_id == DEVICE_ID
        return True


class FakePrimer:
    enabled = True

    def __init__(self) -> None:
        self.prime_calls: list[tuple[str, str | None, bool, float]] = []

    def prime_before_play(
        self,
        device_id: str,
        account_id: str | None = None,
        force: bool = True,
        wait_seconds: float = 1.0,
    ) -> bool:
        self.prime_calls.append((device_id, account_id, force, wait_seconds))
        return False


def make_client(
    monkeypatch,
    speakers: FakeSpeakers | None = None,
    datastore: FakeDatastore | None = None,
    primer: FakePrimer | None = None,
):
    monkeypatch.chdir(Path(__file__).resolve().parents[1])
    app = FastAPI()
    fake_speakers = speakers or FakeSpeakers()
    fake_datastore = datastore or FakeDatastore()
    app.include_router(
        get_miniapp_router(
            cast(Any, fake_datastore), cast(Any, fake_speakers), cast(Any, primer)
        )
    )
    return TestClient(app), fake_speakers


def set_cookie_headers(response) -> list[str]:
    return response.headers.get_list("set-cookie")


def cookie_headers_text(response) -> str:
    return "\n".join(set_cookie_headers(response))


def assert_cookie_deleted(cookies: str, cookie_name: str) -> None:
    assert f"{cookie_name}=" in cookies
    assert "Max-Age=0" in cookies


def test_dashboard_decodes_display_cookies(monkeypatch):
    client, _speakers = make_client(monkeypatch)

    response = client.get(
        "/miniapp/dashboard",
        headers={
            "Cookie": (
                f"soundcork_account_id={ACCOUNT_ID}; "
                "soundcork_account_label=%C3%9A%C4%8Det%20lo%C5%BEnice; "
            )
        },
    )

    assert response.status_code == 200
    assert "Účet ložnice" in response.text
    assert "ložnice" in response.text
    assert "Rádio Proglas" in response.text


def test_dashboard_resolves_selected_configured_device(monkeypatch):
    now_playing = SimpleNamespace(
        StationName="Rádio Dechovka",
        ContentItem=SimpleNamespace(Name="Rádio Dechovka"),
        ContainerArtUrl="",
        PlayStatus="PLAY_STATE",
    )
    speakers = FakeSpeakers(
        now_playing_status=now_playing,
        online=False,
    )
    client, _speakers = make_client(monkeypatch, speakers=speakers)

    response = client.get(
        f"/miniapp/dashboard?selected_device_id={DEVICE_ID}",
        headers={"Cookie": f"soundcork_account_id={ACCOUNT_ID}"},
    )

    assert response.status_code == 200
    assert speakers.now_playing_calls == [DEVICE_ID]
    assert "Now Playing on ložnice" in response.text
    assert "Rádio Dechovka" in response.text
    assert 'class="device-card online"' in response.text


def test_dashboard_does_not_probe_unselected_configured_device(monkeypatch):
    speakers = FakeSpeakers(online=False)
    client, _speakers = make_client(monkeypatch, speakers=speakers)

    response = client.get(
        "/miniapp/dashboard",
        headers={"Cookie": f"soundcork_account_id={ACCOUNT_ID}"},
    )

    assert response.status_code == 200
    assert speakers.now_playing_calls == []
    assert 'class="device-card offline"' in response.text


def test_dashboard_proxies_tunein_preset_art(monkeypatch):
    art_url = "http://cdn-profiles.tunein.com/s123/images/logoq.png?t=1"
    client, _speakers = make_client(
        monkeypatch,
        datastore=FakeDatastore(container_art=art_url),
    )

    response = client.get(
        "/miniapp/dashboard?selected_content_item_id=4",
        headers={"Cookie": f"soundcork_account_id={ACCOUNT_ID}"},
    )

    assert response.status_code == 200
    assert f'src="{miniapp_artwork_url(art_url)}"' in response.text


def test_login_clears_stale_selection_cookies(monkeypatch):
    client, _speakers = make_client(monkeypatch)

    response = client.post(
        "/miniapp/login",
        data={"account_id": ACCOUNT_ID},
        headers={
            "Cookie": (
                "soundcork_selected_device=Bos%C3%ADk; "
                "soundcork_selected_device_id=stale-device; "
                "soundcork_selected_content_item_name=BBC; "
                "soundcork_selected_content_item_id=99; "
                "soundcork_is_playing=true"
            )
        },
        follow_redirects=False,
    )

    cookies = cookie_headers_text(response)
    assert response.status_code == 303
    assert_cookie_deleted(cookies, "soundcork_selected_device")
    assert_cookie_deleted(cookies, "soundcork_selected_device_id")
    assert_cookie_deleted(cookies, "soundcork_selected_content_item_name")
    assert_cookie_deleted(cookies, "soundcork_selected_content_item_id")
    assert_cookie_deleted(cookies, "soundcork_is_playing")


def test_play_primes_spotify_before_playback_when_primer_configured(monkeypatch):
    monkeypatch.setattr("soundcork.miniapp.time.time", lambda: 1000.1234)
    primer = FakePrimer()
    client, speakers = make_client(
        monkeypatch,
        datastore=FakeDatastore("SPOTIFY"),
        primer=primer,
    )
    client.cookies.set("soundcork_account_id", ACCOUNT_ID)

    response = client.post(
        f"/miniapp/play?selected_device_id={DEVICE_ID}&selected_content_item_id=content-1",
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert (
        response.headers["location"]
        == f"/miniapp/dashboard?selected_device_id={DEVICE_ID}&selected_content_item_id=content-1&started=true&started_at=1000.123"
    )
    assert primer.prime_calls == [(DEVICE_ID, ACCOUNT_ID, True, 1.0)]
    assert speakers.play_calls == [(DEVICE_ID, "content-1")]


def test_play_does_not_prime_non_spotify_content(monkeypatch):
    monkeypatch.setattr("soundcork.miniapp.time.time", lambda: 1000.1234)
    primer = FakePrimer()
    client, speakers = make_client(
        monkeypatch,
        datastore=FakeDatastore("LOCAL_INTERNET_RADIO"),
        primer=primer,
    )
    client.cookies.set("soundcork_account_id", ACCOUNT_ID)

    response = client.post(
        f"/miniapp/play?selected_device_id={DEVICE_ID}&selected_content_item_id=content-1",
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert primer.prime_calls == []
    assert speakers.play_calls == [(DEVICE_ID, "content-1")]


def test_select_content_item_plays_when_device_is_selected(monkeypatch):
    monkeypatch.setattr("soundcork.miniapp.time.time", lambda: 1000.1234)
    primer = FakePrimer()
    client, speakers = make_client(
        monkeypatch,
        datastore=FakeDatastore("SPOTIFY"),
        primer=primer,
    )
    client.cookies.set("soundcork_account_id", ACCOUNT_ID)

    response = client.post(
        f"/miniapp/select-content-item?selected_device_id={DEVICE_ID}",
        data={"content_item_id": "content-1", "content_item_name": "Spotify preset"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert (
        response.headers["location"]
        == f"/miniapp/dashboard?selected_content_item_id=content-1&selected_device_id={DEVICE_ID}&started=true&started_at=1000.123"
    )
    assert primer.prime_calls == [(DEVICE_ID, ACCOUNT_ID, True, 1.0)]
    assert speakers.play_calls == [(DEVICE_ID, "content-1")]


def test_started_dashboard_shows_optimistic_playback_state(monkeypatch):
    monkeypatch.setattr("soundcork.miniapp.time.time", inside_started_window)
    client, _speakers = make_client(monkeypatch)
    client.cookies.set("soundcork_account_id", ACCOUNT_ID)
    client.cookies.set("soundcork_account_label", "%C3%9A%C4%8Det%20lo%C5%BEnice")

    response = client.get(
        f"/miniapp/dashboard?selected_device_id={DEVICE_ID}&selected_content_item_id=4&started=true&started_at={STARTED_AT_QUERY}"
    )

    assert response.status_code == 200
    assert "Now Playing on ložnice" in response.text
    assert "Rádio Proglas" in response.text
    assert "Volume: 23" in response.text
    assert "Stop" in response.text


def test_started_dashboard_overrides_stale_playing_metadata(monkeypatch):
    monkeypatch.setattr("soundcork.miniapp.time.time", inside_started_window)
    stale_now_playing = SimpleNamespace(
        StationName="CRo D-dur",
        ContentItem=SimpleNamespace(Name="CRo D-dur"),
        ContainerArtUrl="http://example.com/ddur.png",
        PlayStatus="PLAY_STATE",
    )
    speakers = FakeSpeakers(now_playing_status=stale_now_playing)
    client, _speakers = make_client(monkeypatch, speakers=speakers)
    client.cookies.set("soundcork_account_id", ACCOUNT_ID)
    client.cookies.set("soundcork_account_label", "%C3%9A%C4%8Det%20lo%C5%BEnice")

    response = client.get(
        f"/miniapp/dashboard?selected_device_id={DEVICE_ID}&selected_content_item_id=4&started=true&started_at={STARTED_AT_QUERY}"
    )

    assert response.status_code == 200
    assert "Now Playing on ložnice" in response.text
    assert "Rádio Proglas" in response.text
    assert "CRo D-dur" not in response.text
    assert "Stop" in response.text


def test_started_dashboard_uses_actual_metadata_after_optimistic_window(monkeypatch):
    monkeypatch.setattr("soundcork.miniapp.time.time", outside_started_window)
    stale_now_playing = SimpleNamespace(
        StationName="CRo D-dur",
        ContentItem=SimpleNamespace(Name="CRo D-dur"),
        ContainerArtUrl="http://example.com/ddur.png",
        PlayStatus="PLAY_STATE",
    )
    speakers = FakeSpeakers(now_playing_status=stale_now_playing)
    client, _speakers = make_client(monkeypatch, speakers=speakers)
    client.cookies.set("soundcork_account_id", ACCOUNT_ID)

    response = client.get(
        f"/miniapp/dashboard?selected_device_id={DEVICE_ID}&selected_content_item_id=4&started=true&started_at={STARTED_AT_QUERY}"
    )

    assert response.status_code == 200
    assert "CRo D-dur" in response.text


def test_select_content_item_without_device_only_selects(monkeypatch):
    client, speakers = make_client(monkeypatch)

    response = client.post(
        "/miniapp/select-content-item",
        data={"content_item_id": "content-1", "content_item_name": "Radio preset"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert (
        response.headers["location"]
        == "/miniapp/dashboard?selected_content_item_id=content-1"
    )
    assert speakers.play_calls == []


def test_miniapp_artwork_url_proxies_only_tunein_artwork():
    tunein_art = "http://cdn-radiotime-logos.tunein.com/s15666q.png"
    other_art = "https://i.scdn.co/image/example"

    assert miniapp_artwork_url(tunein_art).startswith("/miniapp/artwork?url=")
    assert miniapp_artwork_url(other_art) == other_art


def test_use_started_state_is_short_lived(monkeypatch):
    monkeypatch.setattr("soundcork.miniapp.time.time", inside_started_window)

    assert use_started_state(True, STARTED_AT)
    assert not use_started_state(True, STARTED_AT - STARTED_OPTIMISTIC_SECONDS)
    assert not use_started_state(True, None)
    assert not use_started_state(False, STARTED_AT)


def test_started_optimistic_window_is_three_seconds():
    assert STARTED_OPTIMISTIC_SECONDS == 3.0


def test_artwork_proxy_rejects_unsupported_url(monkeypatch):
    client, _speakers = make_client(monkeypatch)

    response = client.get("/miniapp/artwork?url=http%3A%2F%2F127.0.0.1%2Fsecret.png")

    assert response.status_code == 400


def test_artwork_proxy_returns_image(monkeypatch):
    class FakeUpstream:
        headers = {"content-type": "Image/PNG; charset=binary"}
        content = b"png-bytes"

        def raise_for_status(self):
            return None

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, url, headers):
            assert url == "http://cdn-profiles.tunein.com/s123/logo.png"
            assert headers["User-Agent"] == "SoundCork miniapp artwork proxy"
            return FakeUpstream()

    monkeypatch.setattr("soundcork.miniapp.httpx.AsyncClient", FakeAsyncClient)
    client, _speakers = make_client(monkeypatch)

    response = client.get(
        "/miniapp/artwork?url=http%3A%2F%2Fcdn-profiles.tunein.com%2Fs123%2Flogo.png"
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"] == "public, max-age=86400"
    assert response.content == b"png-bytes"


def test_get_device_image_normalizes_product_code_whitespace():
    assert get_device_image("SoundTouch 10 sm2 ") == "d9.png"
