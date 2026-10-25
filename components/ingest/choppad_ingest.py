"""ingest: URL or local media file -> media/<id>/{video.mp4, audio.wav, meta.json}. See contracts/README.md §1."""


def ingest(source: str, media_root: str) -> str:
    """Fetch/convert `source` and return the media dir path. Idempotent: re-running reuses existing files."""
    raise NotImplementedError("ingest")
