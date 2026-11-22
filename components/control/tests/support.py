"""Shared helpers for the control tests: a synthetic song folder and a real server on a free port (stdlib only)."""
import json, os, sys, tempfile, threading
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
from _common import *  # noqa: E402,F403
sys.path.insert(0, os.path.join(ROOT, "components", "control"))  # noqa: F405
import server as srvmod  # noqa: E402

CHOPS = {"schema": "choppad.chops/2", "grid": "1/4", "bpm": 120, "beats_per_bar": 4,
         "chops": [{"index": i, "start": i * 0.5, "end": i * 0.5 + 0.5, "bar": i // 4, "beat": i % 4} for i in range(40)],
         "banks": [list(range(0, 16)), list(range(16, 32)), list(range(32, 40))]}


def jwrite(path, doc):
    with open(path, "w") as f:
        json.dump(doc, f)


def jread(path):
    with open(path) as f:
        return json.load(f)


def make_song_root():
    """media root with one song, `song1` (json only; peaks are pre-cached so no numpy is needed)."""
    root = tempfile.mkdtemp(prefix="choppad-ctl-")
    d = os.path.join(root, "song1")
    os.makedirs(d)
    jwrite(os.path.join(d, "meta.json"), {"id": "song1", "title": "Test Song", "duration_s": 20.0})
    jwrite(os.path.join(d, "chops.json"), CHOPS)
    jwrite(os.path.join(d, "analysis.json"), {"duration_s": 20.0, "bpm": 120, "meter": {"beats_per_bar": 4, "beat_unit": 4},
                                              "beats": [], "downbeats": []})
    jwrite(os.path.join(d, "peaks.json"), {"duration_s": 20.0, "peaks": [0.5] * 10})
    return root


def start_server(root, **kw):
    srv = srvmod.make_server(root, port=0, **kw)
    threading.Thread(target=lambda: srv.serve_forever(poll_interval=0.05), daemon=True).start()
    return srv, srv.server_address[1]
