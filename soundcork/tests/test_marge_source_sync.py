import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from soundcork.marge import add_source_to_account


def test_account_source_parser_accepts_amazon_provider():
    captured = {}

    class Store:
        def add_source(self, account, source):
            captured["account"] = account
            captured["source"] = source
            source.id = "100020"
            return source

    result = add_source_to_account(
        Store(),
        "8208423",
        """<source>
  <username>listener@example.invalid</username>
  <sourceproviderid>20</sourceproviderid>
  <credential type="token">synthetic-secret</credential>
  <sourcename>Amazon Music</sourcename>
</source>""",
    )

    source = captured["source"]
    assert captured["account"] == "8208423"
    assert source.source_key_type == "AMAZON"
    assert source.source_key_account == "listener@example.invalid"
    assert source.secret_type == "token"
    assert source.secret == "synthetic-secret"
    assert result.attrib["id"] == "100020"


def test_adding_account_source_schedules_speaker_refresh(monkeypatch):
    # Codex: main.py resolves static assets relative to its historical cwd.
    monkeypatch.chdir(Path(__file__).resolve().parents[1])
    from soundcork import main

    class Store:
        def etag_for_account(self, account: str) -> int:
            assert account == "8208423"
            return 1

    store = Store()
    calls = []
    monkeypatch.setattr(main, "datastore", store)
    monkeypatch.setattr(main, "settings", SimpleNamespace(base_url="http://unifi:8001"))
    monkeypatch.setattr(
        main,
        "add_source_to_account",
        lambda received_store, account, xml: ET.Element(
            "source", {"id": f"{account}-{received_store is store}-{bool(xml)}"}
        ),
    )
    monkeypatch.setattr(
        main,
        "notify_account_sources_updated",
        lambda received_store, account, base_url: calls.append(
            (received_store, account, base_url)
        ),
    )

    response = TestClient(main.app).post(
        "/marge/streaming/account/8208423/source",
        content=b"<source />",
        headers={"Content-Type": "application/xml"},
    )

    assert response.status_code == 201
    assert calls == [(store, "8208423", "http://unifi:8001")]


def test_deleting_account_source_schedules_speaker_refresh(monkeypatch):
    monkeypatch.chdir(Path(__file__).resolve().parents[1])
    from soundcork import main

    store = object()
    calls = []
    monkeypatch.setattr(main, "datastore", store)
    monkeypatch.setattr(main, "settings", SimpleNamespace(base_url="http://unifi:8001"))
    monkeypatch.setattr(
        main,
        "remove_source_from_account",
        lambda received_store, account, source_id: calls.append(
            ("remove", received_store, account, source_id)
        ),
    )
    monkeypatch.setattr(
        main,
        "notify_account_sources_updated",
        lambda received_store, account, base_url: calls.append(
            ("notify", received_store, account, base_url)
        ),
    )

    response = TestClient(main.app).delete(
        "/marge/streaming/account/8208423/source/100020"
    )

    assert response.status_code == 200
    assert calls == [
        ("remove", store, "8208423", "100020"),
        ("notify", store, "8208423", "http://unifi:8001"),
    ]
