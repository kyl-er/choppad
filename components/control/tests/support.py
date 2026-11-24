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


def saved_project(root, song="song1"):
    """The project saved for a song: since the drum pad mega banks (contracts §15) that is the project of the song's
    mega bank (media/_mega/<id>/mega.json), no longer media/<song>/project.json. None when nothing was saved yet."""
    best = None
    mroot = os.path.join(root, "_mega")
    for d in sorted(os.listdir(mroot)) if os.path.isdir(mroot) else []:
        path = os.path.join(mroot, d, "mega.json")
        if d.startswith(".") or not os.path.isfile(path):
            continue
        doc = jread(path)
        if doc.get("source") == song and (best is None or str(doc.get("updated", "")) >= str(best.get("updated", ""))):
            best = doc
    return best["project"] if best is not None else None


def mega_id(root, song="song1"):
    """Id of the song's mega bank: the key of its event hub, lock and view state."""
    mroot = os.path.join(root, "_mega")
    for d in sorted(os.listdir(mroot)) if os.path.isdir(mroot) else []:
        path = os.path.join(mroot, d, "mega.json")
        if not d.startswith(".") and os.path.isfile(path) and jread(path).get("source") == song:
            return jread(path)["id"]
    return None
