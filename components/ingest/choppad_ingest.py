"""ingest: URL or local media file -> media/<id>/{video.mp4, audio.wav, meta.json}. See contracts/README.md §1."""


def ingest(source: str, media_root: str, *, max_duration_s: float = 420, cookies_from_browser: str | None = None,
           force: bool = False, cap_bytes: int = 500 * 2**20) -> str:
    """Fetch/convert `source`, enforce the cache cap, return the media dir. Idempotent unless force=True.
    Raises ValueError if the source is longer than max_duration_s (checked before downloading)."""
    raise NotImplementedError("ingest")


def enforce_cap(media_root: str, cap_bytes: int, keep: str | None = None) -> list[str]:
    """Delete least-recently-used <id> dirs (by mtime) until media_root <= cap_bytes; never delete `keep`.
    Returns the removed dir names."""
    raise NotImplementedError("ingest.enforce_cap")
