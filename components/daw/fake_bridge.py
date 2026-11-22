"""Test hook: DAW bridges that start nothing. `CHOPPAD_DAW_FAKE=<log file>` makes the server use these instead of Hydrogen and
Ardour, so browser tests can check what would reach a DAW without any DAW, MIDI port or audio device.

Every call is appended to the log as one JSON line `{"t": <time.time()>, "daw": ..., "op": "launch"|"push"|"transport"|"note"|
"close", ...}`; `t` is wall-clock time at the moment the bridge received the call, so a test can compare it with a
timestamp taken in the page.
"""
import json
import os
import threading
import time


class FakeBridge:
    def __init__(self, name, log_path):
        self.name, self.log_path = name, log_path
        self._lock = threading.Lock()

    def _log(self, op, **kw):
        line = json.dumps({"t": time.time(), "daw": self.name, "op": op, **kw})
        with self._lock, open(self.log_path, "a") as f:
            f.write(line + "\n")

    def available(self):
        return True

    def launch(self, project):
        self._log("launch", song=project.song_id, bank=project.bank)
        return {"fake": True, "kit": project.kit_name}

    def push(self, project):
        self._log("push", song=project.song_id, bank=project.bank)
        return {"fake": True, "pushed": True}

    def transport(self, command, *, bpm=None):
        self._log("transport", command=command, bpm=bpm)

    def note(self, pad, velocity=1.0):
        self._log("note", pad=pad, velocity=velocity)

    def close(self):
        self._log("close")


def fake_bridges(log_path):
    os.makedirs(os.path.dirname(os.path.abspath(log_path)), exist_ok=True)
    return {name: (lambda n=name: FakeBridge(n, log_path)) for name in ("hydrogen", "ardour")}


def no_midi():
    raise RuntimeError("fake DAW mode: no MIDI port")
