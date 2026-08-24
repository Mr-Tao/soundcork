"""Public SoundFork identity, capabilities, and build provenance."""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

MANIFEST_PATH: Final = Path(__file__).resolve().with_name("soundfork.json")
BUILD_INFO_PATH: Final = Path(__file__).resolve().parent.parent / "soundfork-build.json"

# Codex: Validate the tracked identity contract without inventing runtime provenance.
_MANIFEST_KEYS = frozenset({"schema", "product", "version", "capabilities"})
_BUILD_INFO_KEYS = _MANIFEST_KEYS | frozenset(
    {"vcs_revision", "source_dirty", "managed_tree_sha256"}
)
_PRODUCT_RE = re.compile(r"[a-z][a-z0-9-]*\Z")
_VERSION_RE = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\Z")
_CAPABILITY_RE = re.compile(r"[a-z][a-z0-9-]*(?:\.[a-z0-9-]+)+\Z")
_VCS_REVISION_RE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class StaticManifest:
    """Validated public identity and capability contract."""

    schema: int
    product: str
    version: str
    capabilities: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "schema": self.schema,
            "product": self.product,
            "version": self.version,
            "capabilities": list(self.capabilities),
        }


@dataclass(frozen=True)
class BuildInfo:
    """Validated dynamic provenance, or explicit unknown values."""

    vcs_revision: str | None = None
    source_dirty: bool | None = None
    managed_tree_sha256: str | None = None


def load_manifest(path: str | Path | None = None) -> StaticManifest:
    """Load and validate the tracked schema-1 ``soundfork.json`` manifest."""
    manifest_path = Path(path) if path is not None else MANIFEST_PATH
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RuntimeError(
            f"Cannot load SoundFork manifest: {manifest_path}"
        ) from error

    if not isinstance(payload, dict) or set(payload) != _MANIFEST_KEYS:
        raise ValueError("SoundFork manifest must contain exactly the schema-1 fields")

    schema = payload["schema"]
    product = payload["product"]
    version = payload["version"]
    capabilities = payload["capabilities"]
    if type(schema) is not int or schema != 1:
        raise ValueError("SoundFork manifest schema must be 1")
    if not isinstance(product, str) or _PRODUCT_RE.fullmatch(product) is None:
        raise ValueError("SoundFork manifest product is invalid")
    if not isinstance(version, str) or _VERSION_RE.fullmatch(version) is None:
        raise ValueError("SoundFork manifest version is invalid")
    if (
        not isinstance(capabilities, list)
        or not capabilities
        or not all(
            isinstance(capability, str)
            and _CAPABILITY_RE.fullmatch(capability) is not None
            for capability in capabilities
        )
        or len(capabilities) != len(set(capabilities))
    ):
        raise ValueError("SoundFork manifest capabilities are invalid")

    return StaticManifest(
        schema=schema,
        product=product,
        version=version,
        capabilities=tuple(capabilities),
    )


MANIFEST: Final = load_manifest()
SCHEMA: Final = MANIFEST.schema
PRODUCT: Final = MANIFEST.product
VERSION: Final = MANIFEST.version
CAPABILITIES: Final = MANIFEST.capabilities


def _valid_identity(payload: dict[str, object]) -> bool:
    expected = MANIFEST.as_dict()
    return set(payload) == _BUILD_INFO_KEYS and all(
        payload[key] == value for key, value in expected.items()
    )


def load_build_info(path: str | Path | None = None) -> BuildInfo:
    """Load complete, validated provenance from ``soundfork-build.json``."""
    build_info_path = Path(path) if path is not None else BUILD_INFO_PATH
    try:
        payload = json.loads(build_info_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return BuildInfo()

    if not isinstance(payload, dict) or not _valid_identity(payload):
        return BuildInfo()

    vcs_revision = payload.get("vcs_revision")
    source_dirty = payload.get("source_dirty")
    managed_tree_sha256 = payload.get("managed_tree_sha256")
    if (
        not isinstance(vcs_revision, str)
        or _VCS_REVISION_RE.fullmatch(vcs_revision) is None
        or not isinstance(source_dirty, bool)
        or not isinstance(managed_tree_sha256, str)
        or _SHA256_RE.fullmatch(managed_tree_sha256) is None
    ):
        return BuildInfo()

    return BuildInfo(
        vcs_revision=vcs_revision,
        source_dirty=source_dirty,
        managed_tree_sha256=managed_tree_sha256,
    )


def capabilities_manifest(path: str | Path | None = None) -> dict[str, object]:
    """Return the public static manifest with optional validated provenance."""
    build_info = load_build_info(path)
    return {
        **MANIFEST.as_dict(),
        "vcs_revision": build_info.vcs_revision,
        "source_dirty": build_info.source_dirty,
        "managed_tree_sha256": build_info.managed_tree_sha256,
    }
