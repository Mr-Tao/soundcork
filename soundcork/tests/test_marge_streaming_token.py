import xml.etree.ElementTree as ET
from pathlib import Path

from fastapi.testclient import TestClient


def test_streaming_token_matches_soundtouch_contract(monkeypatch):
    # Codex: main.py resolves static assets relative to its historical cwd.
    monkeypatch.chdir(Path(__file__).resolve().parents[1])
    from soundcork.main import app

    response = TestClient(app).get(
        "/marge/streaming/device/AABBCCDDEEFF/streaming_token"
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/vnd.bose.streaming-v1.2+xml"
    assert response.headers["etag"].isdigit()
    assert response.content.startswith(
        b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    )

    authorization = response.headers["authorization"]
    assert authorization.startswith("Bearer ")

    token = ET.fromstring(response.content)
    assert token.tag == "bearertoken"
    assert token.attrib["value"] == authorization
