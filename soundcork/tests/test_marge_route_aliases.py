from pathlib import Path

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient


def _main(monkeypatch):
    monkeypatch.chdir(Path(__file__).resolve().parents[1])
    import soundcork.main as main

    return main


def _route_contract(app, prefix: str) -> set[tuple[str, frozenset[str]]]:
    return {
        (route.path.removeprefix(prefix), frozenset(route.methods or set()))
        for route in app.routes
        if isinstance(route, APIRoute)
        and (route.path == prefix or route.path.startswith(f"{prefix}/"))
    }


def test_canonical_and_legacy_routes_have_the_same_contract(monkeypatch):
    main = _main(monkeypatch)

    canonical_streaming = _route_contract(main.app, "/streaming")
    canonical_oauth = _route_contract(main.app, "/oauth")

    assert canonical_streaming == _route_contract(main.app, "/marge/streaming")
    assert canonical_oauth == _route_contract(main.app, "/marge/oauth")
    assert canonical_streaming
    assert canonical_oauth


def test_only_canonical_routes_are_in_openapi(monkeypatch):
    paths = _main(monkeypatch).app.openapi()["paths"]

    assert "/streaming/sourceproviders" in paths
    assert (
        "/oauth/device/{device_id}/music/musicprovider/{provider_id}/token/{token_type}"
        in paths
    )
    assert not any(path.startswith("/marge/streaming") for path in paths)
    assert not any(path.startswith("/marge/oauth") for path in paths)


def test_read_routes_are_equivalent(monkeypatch):
    client = TestClient(_main(monkeypatch).app)

    canonical = client.get("/streaming/sourceproviders")
    legacy = client.get("/marge/streaming/sourceproviders")

    assert canonical.status_code == legacy.status_code == 200
    assert canonical.content == legacy.content
    assert canonical.headers["content-type"] == legacy.headers["content-type"]


def test_unsupported_oauth_provider_is_equivalent(monkeypatch):
    client = TestClient(_main(monkeypatch).app)
    suffix = "/device/AABBCCDDEEFF/music/musicprovider/99/token/access"

    canonical = client.post(f"/oauth{suffix}")
    legacy = client.post(f"/marge/oauth{suffix}")

    assert canonical.status_code == legacy.status_code == 404
    assert canonical.content == legacy.content


def test_mutating_aliases_dispatch_once(monkeypatch):
    main = _main(monkeypatch)
    calls = []
    monkeypatch.setattr(
        main,
        "update_device_poweron",
        lambda _datastore, body: calls.append(body) or "8208423",
    )
    client = TestClient(main.app)

    for path in ("/streaming/support/power_on", "/marge/streaming/support/power_on"):
        response = client.post(path, content=b"<info />")
        assert response.status_code == 200

    assert calls == [b"<info />", b"<info />"]


def test_delete_locations_preserve_legacy_and_use_canonical_routes(monkeypatch):
    main = _main(monkeypatch)
    monkeypatch.setattr(main.settings, "base_url", "http://soundcork.test")
    monkeypatch.setattr(main, "remove_device_from_account", lambda *_args: None)
    monkeypatch.setattr(main, "remove_source_from_account", lambda *_args: None)
    monkeypatch.setattr(main, "notify_account_sources_updated", lambda *_args: None)
    client = TestClient(main.app)
    account = "8208423"
    device = "AABBCCDDEEFF"

    canonical_device = client.delete(f"/streaming/account/{account}/device/{device}")
    legacy_device = client.delete(f"/marge/streaming/account/{account}/device/{device}")
    canonical_source = client.delete(f"/streaming/account/{account}/source/20")
    legacy_source = client.delete(f"/marge/streaming/account/{account}/source/20")

    assert canonical_device.headers["location"] == (
        f"http://soundcork.test/streaming/account/{account}/device/{device}"
    )
    assert legacy_device.headers["location"] == (
        f"http://soundcork.test/marge/account/{account}/device/{device}"
    )
    assert canonical_source.headers["location"] == (
        f"http://soundcork.test/streaming/account/{account}/source/20"
    )
    assert legacy_source.headers["location"] == (
        f"http://soundcork.test/marge/account/{account}/source/20"
    )


def test_spotify_primer_observes_both_streaming_route_families(monkeypatch):
    main = _main(monkeypatch)
    registrations = []
    monkeypatch.setattr(
        type(main.zeroconf_primer), "enabled", property(lambda _self: True)
    )
    monkeypatch.setattr(
        main.zeroconf_primer,
        "register_speaker",
        lambda account, device: registrations.append((account, device)),
    )
    client = TestClient(main.app)

    for prefix in ("", "/marge"):
        response = client.get(
            f"{prefix}/streaming/account/8208423/device/AABBCCDDEEFF/not-a-route"
        )
        assert response.status_code == 404

    assert registrations == [
        ("8208423", "AABBCCDDEEFF"),
        ("8208423", "AABBCCDDEEFF"),
    ]


def test_bare_legacy_prefix_remains_unhandled(monkeypatch):
    response = TestClient(_main(monkeypatch).app).get("/marge")

    assert response.status_code == 404
