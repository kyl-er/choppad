"""Shared helpers for the daw tests: paths, a synthetic song folder, fakes for OSC, subprocesses and MIDI. Stdlib + the repo venv."""
import json, os, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
from _common import *  # noqa: E402,F403  (repo paths, click_track, load_fixture)
for _c in ("daw", "control", "integrate", "export"):
    sys.path.insert(0, os.path.join(ROOT, "components", _c))  # noqa: F405

import daw_project  # noqa: E402


def make_media(bpm=120, title="Test Song", sid="song1", pattern=None, project_extra=None):
    """A media folder with audio.wav (clicks), meta.json, chops.json (the contract sample), project.json."""
    d = tempfile.mkdtemp(prefix="choppad-daw-media-")
    song = os.path.join(d, sid)
    os.makedirs(song)
    click_track(os.path.join(song, "audio.wav"), bpm)
    for name, doc in (("meta.json", {"id": sid, "title": title, "duration_s": 20.0}), ("chops.json", load_fixture("chops"))):  # noqa: F405
        with open(os.path.join(song, name), "w") as f:
            json.dump(doc, f)
    proj = {"pads": [{} for _ in range(16)], "overrides": {}}
    if pattern is not None:
        proj["pattern"] = pattern
    proj.update(project_extra or {})
    with open(os.path.join(song, "project.json"), "w") as f:
        json.dump(proj, f)
    return d, song


def fake_project(pattern=None, bank=0, bpm=120.0, beats_per_bar=4, wav_dir="/kits/k", empty_from=16, sid="song1"):
    chops = [daw_project.Chop(p, f"{wav_dir}/chop_{p:02d}.wav", 36 + p) if p < empty_from else None for p in range(16)]
    return daw_project.DawProject(song_id=sid, title="Test Song", bpm=bpm, beats_per_bar=beats_per_bar, bank=bank, chops=chops,
                                  pattern=pattern or {"bars": 2, "events": []}, kit_name="ChopPad Test Song B1 abcd1234",
                                  kit_dir=wav_dir, key="abcd1234")


class FakeOsc:
    """Records (address, args) like OscClient."""
    def __init__(self, host="127.0.0.1", port=0):
        self.host, self.port, self.sent, self.closed = host, port, [], False

    def send(self, address, *args):
        self.sent.append((address, list(args)))

    def close(self):
        self.closed = True


class FakeBackend:
    """MIDI backend: records raw messages."""
    def __init__(self):
        self.msgs, self.closed = [], False
        self.address = "130:0"

    def send(self, msg):
        self.msgs.append(bytes(msg))

    def close(self):
        self.closed = True


class FakeProc:
    def __init__(self, pid=4242):
        self.pid, self.returncode, self.terminated, self.killed = pid, None, False, False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated, self.returncode = True, 0

    def kill(self):
        self.killed, self.returncode = True, -9

    def wait(self, timeout=None):
        return self.returncode


def make_song_root(sid="song1"):
    """A media root with one json-only song (for the HTTP tests); the chops are the contract sample."""
    root = tempfile.mkdtemp(prefix="choppad-daw-root-")
    d = os.path.join(root, sid)
    os.makedirs(d)
    docs = {"meta.json": {"id": sid, "title": "Test Song", "duration_s": 20.0}, "chops.json": load_fixture("chops"),  # noqa: F405
            "analysis.json": {"duration_s": 20.0, "bpm": 120, "meter": {"beats_per_bar": 4, "beat_unit": 4}, "beats": [], "downbeats": []},
            "peaks.json": {"duration_s": 20.0, "peaks": [0.5] * 10}}
    for name, doc in docs.items():
        with open(os.path.join(d, name), "w") as f:
            json.dump(doc, f)
    return root


def start_server(root, **kw):
    import threading
    import server as srvmod
    srv = srvmod.make_server(root, port=0, **kw)
    threading.Thread(target=lambda: srv.serve_forever(poll_interval=0.05), daemon=True).start()
    return srv, srv.server_address[1]
