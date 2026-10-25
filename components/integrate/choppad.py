"""choppad CLI: `choppad <url|file> [--grid 1/4] [--meter 4/4] [--bpm N] [--max-duration 420]
   [--cookies-from-browser B] [--serve] [--port 8960] [--clean] [--force]`.

Runs ingest -> rhythm -> chop into media/<id>/, then (with --serve) serves the web UI at
http://127.0.0.1:<port>/?id=<id> with /media/<id>/* static. See contracts/README.md.
"""


def run_pipeline(source: str, media_root: str, grid: str = "1/4", meter: str | None = None,
                 bpm: float | None = None, **ingest_kw) -> str:
    """Return the media dir containing meta.json, analysis.json, chops.json."""
    raise NotImplementedError("integrate.run_pipeline")


def make_server(media_root: str, port: int = 8960):
    """ThreadingHTTPServer (not started) on 127.0.0.1: components/ui at /, components/midi/midi.js at /midi.js,
    media_root at /media/ with Range -> 206, and POST /api/regrid {id, grid, meter, beat_offset, bpm_mult}
    -> re-runs chop on media/<id>/analysis.json, writes chops.json, returns it (< 50 ms)."""
    raise NotImplementedError("integrate.make_server")
