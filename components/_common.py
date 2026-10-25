"""Shared test helpers: path setup + fixture access. Import from tests as `from _common import *`."""
import glob, json, os, sys
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_v = f"python{sys.version_info.major}.{sys.version_info.minor}"
for sp in glob.glob(os.path.join(ROOT, ".venv", "lib", _v, "site-packages")):
    if sp not in sys.path:
        sys.path.insert(0, sp)
CONTRACTS = os.path.join(ROOT, "contracts")
FIX = os.path.join(CONTRACTS, "fixtures")
for p in (CONTRACTS, FIX, *(os.path.join(ROOT, "components", c) for c in ("ingest", "rhythm", "chop", "advisor", "export", "integrate"))):
    if p not in sys.path:
        sys.path.insert(0, p)
from validate import ContractError, validate_advisor, validate_analysis, validate_chops, validate_meta  # noqa: E402,F401
from make_fixtures import click_track, golden  # noqa: E402,F401

def load_fixture(name):
    return json.load(open(os.path.join(FIX, f"{name}.sample.json")))

def make_video(path, wav, seconds):
    """Mux a test-pattern video with the given WAV (needs ffmpeg)."""
    import subprocess
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size=320x240:rate=25:duration={seconds}",
                    "-i", wav, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", path], check=True)
