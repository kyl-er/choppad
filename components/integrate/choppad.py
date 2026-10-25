"""choppad CLI: `choppad <url|file> [--grid 1/4] [--meter 4/4] [--serve] [--port 8960]`.

Runs ingest -> rhythm -> chop into media/<id>/, then (with --serve) serves the web UI at
http://127.0.0.1:<port>/?id=<id> with /media/<id>/* static. See contracts/README.md.
"""


def run_pipeline(source: str, media_root: str, grid: str = "1/4", meter: str | None = None) -> str:
    """Return the media dir containing meta.json, analysis.json, chops.json."""
    raise NotImplementedError("integrate.run_pipeline")


def make_server(media_root: str, port: int = 8960):
    """Return an http.server-compatible server (not started) serving components/ui at / and media_root at /media/."""
    raise NotImplementedError("integrate.make_server")
