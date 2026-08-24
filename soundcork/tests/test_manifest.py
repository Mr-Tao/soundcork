import json
import tomllib
from pathlib import Path

import pytest

from soundcork.manifest import (
    MANIFEST,
    VERSION,
    BuildInfo,
    load_build_info,
    load_manifest,
)

ROOT = Path(__file__).resolve().parents[2]


def test_tracked_manifest_has_fixed_soundfork_contract():
    payload = json.loads(
        (ROOT / "soundcork/soundfork.json").read_text(encoding="utf-8")
    )

    assert payload == {
        "schema": 1,
        "product": "soundfork",
        "version": "0.1.0",
        "capabilities": [
            "marge.canonical-routes.v1",
            "marge.legacy-prefix.v1",
        ],
    }
    assert load_manifest() == MANIFEST
    assert MANIFEST.as_dict() == payload


@pytest.mark.parametrize(
    "payload",
    [
        {
            "schema": 2,
            "product": "soundfork",
            "version": "0.1.0",
            "capabilities": ["marge.canonical-routes.v1"],
        },
        {
            "schema": 1,
            "product": "soundfork",
            "version": "0.1.0",
            "capabilities": [
                "marge.canonical-routes.v1",
                "marge.canonical-routes.v1",
            ],
        },
    ],
)
def test_load_manifest_rejects_invalid_static_contract(tmp_path, payload):
    path = tmp_path / "soundfork.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError):
        load_manifest(path)


def test_package_version_matches_static_manifest():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert pyproject["project"]["version"] == VERSION == MANIFEST.version
    assert pyproject["tool"]["setuptools"]["package-data"]["soundcork"] == [
        "soundfork.json"
    ]


def test_fastapi_version_matches_static_manifest(monkeypatch):
    monkeypatch.chdir(Path(__file__).resolve().parents[1])
    from soundcork.main import app

    assert app.version == VERSION


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {
            "vcs_revision": "abc123",
            "source_dirty": False,
            "managed_tree_sha256": "b" * 64,
        },
        {
            "vcs_revision": "a" * 40,
            "source_dirty": "false",
            "managed_tree_sha256": "b" * 64,
        },
        {
            "schema": 1,
            "product": "soundcork",
            "version": "0.1.0",
            "vcs_revision": "a" * 40,
            "source_dirty": False,
            "managed_tree_sha256": "b" * 64,
            "capabilities": [
                "marge.canonical-routes.v1",
                "marge.legacy-prefix.v1",
            ],
        },
        {
            "schema": 1,
            "product": "soundfork",
            "version": "0.1.0",
            "vcs_revision": "a" * 40,
            "source_dirty": False,
            "managed_tree_sha256": "b" * 64,
            "capabilities": ["marge.canonical-routes.v1"],
        },
    ],
)
def test_load_build_info_rejects_invalid_or_partial_sidecar(tmp_path, payload):
    sidecar = tmp_path / "soundfork-build.json"
    if payload is None:
        sidecar.write_text("not json", encoding="utf-8")
    else:
        sidecar.write_text(json.dumps(payload), encoding="utf-8")

    assert load_build_info(sidecar) == BuildInfo()


def test_load_build_info_accepts_complete_matching_sidecar(tmp_path):
    sidecar = tmp_path / "soundfork-build.json"
    sidecar.write_text(
        json.dumps(
            {
                **MANIFEST.as_dict(),
                "vcs_revision": "a" * 40,
                "source_dirty": False,
                "managed_tree_sha256": "b" * 64,
            }
        ),
        encoding="utf-8",
    )

    assert load_build_info(sidecar) == BuildInfo(
        vcs_revision="a" * 40,
        source_dirty=False,
        managed_tree_sha256="b" * 64,
    )
