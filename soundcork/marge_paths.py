"""Shared path and endpoint helpers for the SoundTouch Marge protocol."""


def _is_path_or_child(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(f"{prefix}/")


def is_marge_protocol_path(path: str) -> bool:
    """Return whether a request targets a canonical or legacy Marge route."""
    return any(
        _is_path_or_child(path, prefix) for prefix in ("/streaming", "/oauth", "/marge")
    )


def is_streaming_protocol_path(path: str) -> bool:
    """Return whether a request targets a canonical or legacy streaming route."""
    return any(
        _is_path_or_child(path, prefix) for prefix in ("/streaming", "/marge/streaming")
    )


def legacy_marge_prefix(path: str) -> str:
    """Return the compatibility prefix used by a legacy Marge request."""
    return "/marge" if _is_path_or_child(path, "/marge") else ""


def is_soundcork_marge_url(marge_url: str | None, base_url: str) -> bool:
    """Accept both canonical and legacy URLs for this Soundcork instance."""
    if not marge_url:
        return False

    normalized_base = base_url.strip().rstrip("/")
    normalized_url = marge_url.strip().rstrip("/")
    if not normalized_base or not normalized_url:
        return False
    return normalized_url in {normalized_base, f"{normalized_base}/marge"}
