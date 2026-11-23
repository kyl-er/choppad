"""ChopPad HTTP server (v3): static app, media with streamed Range, JSON API, background import jobs.

Routes
  GET  /                         app (components/ui/index.html); other app files from components/ui/
  GET  /midi.js                  components/midi/midi.js
  GET  /media/<id>/<file>        media, HTTP Range -> 206 (streamed in chunks)
  GET  /api/songs                library: [{id, title, duration_s, bpm, meter, chops, banks}]
  GET  /api/songs/<id>           {meta, analysis, chops, peaks}
  POST /api/songs  {url}         start import job -> {job}
  POST /api/upload               raw file body + X-Filename header (drag & drop) -> import job {job}
  GET  /api/jobs/<job>           {state: queued|running|done|error, stage, progress 0..1, id?, error?}
  POST /api/regrid {id, grid, meter, beat_offset, bpm_mult}   (also /api/songs/<id>/regrid)
  PUT  /api/songs/<id>/project   whole-document save (kept for compatibility; also broadcast as actions)
  POST /api/songs/<id>/actions   {actions:[...]} -> contracts §8 reducer -> project.json -> {project, applied, ...}
  GET  /api/songs/<id>/events    server-sent events: actions | chops | transport | daw (contracts §10)
  GET  /api/events               library-level server-sent events: `job` {job, state: queued|running|done|error, step, progress,
                                 song?, error?} for every import; a new stream is first told the recent jobs
  POST /api/songs/<id>/transport {command: play-pattern|stop|play-song|hit, pad?, velocity?, bank?, from?} -> events
                                 with `local: true` (a tab reporting its own hit/play/stop): not rebroadcast, only handed to
                                 an open DAW bridge (rate limited; 429 when too fast, delivered:false when no bridge)
  `role` event {primary, tabs} on a song stream opened with `?role=1` (a browser tab): the first tab to connect is primary and
                                 executes transport commands, later tabs only display; the next oldest is promoted when it leaves
  `daw` event {open, daw, bank} on the song stream (on open/close of a bridge; also first on a stream that connects while one
                                 is open; GET /api/songs/<id> has the same state as `daw`)
  POST /api/songs/<id>/daw       {daw: hydrogen|ardour, command: open|sync|play|stop, bank?} -> DAW bridge (contracts §11)
  CHOP-13 (contracts §15): the project of a song lives in its DRUM PAD MEGA BANK (media/_mega/<mid>/mega.json, mega.py); every
  /api/songs/<id>/... endpoint acts on "the song's mega bank" (the most recently updated one whose source is the song; made on
  first use, from the song's old project.json, which is only read). Hub, locks and view state are keyed by mega id.
  GET  /api/mega                 [{id, name, source, source_title, updated, used_pads, banks}]
  POST /api/mega {name?, source?}   new empty mega bank (201, same body as GET /api/mega/<mid>)
  GET  /api/mega/<mid>           {mega, project, chops (of the source or empty), view, daw}
  PUT  /api/mega/<mid> {name}    rename;  DELETE: moves the folder to media/_mega/.trash/
  PUT  /api/mega/<mid>/project   whole-document save (as PUT /api/songs/<id>/project)
  POST /api/mega/<mid>/actions | transport      as the song endpoints;  GET /api/mega/<mid>/events
  POST /api/mega/<mid>/source {song}   keep the used pads (rendered into samples/), release the rest, set the source
  POST /api/mega/<mid>/keep {bank, pad}   keep one pad now
  POST /api/mega/<mid>/source {song: null}   keep the used pads like a switch and leave the mega bank with NO source
  CHOP-13 step D: every per-project operation is keyed by mega bank (megaops.py); the song-keyed routes of the same name resolve the
  song's mega bank and call the same function:
  POST /api/mega/<mid>/export {kind: hydrogen|sfz|zip, bank, [overrides, pads, fx]}   works with no source song (zip/sfz of a mega bank
                                 with no source: media/_mega/<mid>/exports/, served at /media/_mega/<mid>/exports/<file>)
  POST /api/mega/<mid>/savekit | stretch | daw {daw, command: open|sync|play|stop|close}   (409 when nothing to close)
  POST /api/mega/<mid>/resample  raw 16-bit WAV body -> media/_mega/<mid>/samples/<hash>.wav (survives the song); {url, hash, duration, ...}
  POST /api/mega/<mid>/regrid | reanalyze | sections | key | advise | stems   act on the SOURCE song; 409 "... Pick a song first." when none
  `mega` event {change: created|renamed|removed|source, id, name?, source?} on GET /api/events (the library list); DELETE of a mega bank
                                 a tab has open first sends `mega {change: "removed", id}` on that mega bank's own stream
  GET  /media/_mega/<mid>/samples/<hash>.wav   a kept pad's audio (Range supported); only samples and exports are served from _mega
Writes are atomic (temp file + rename). Mutating endpoints read X-ChopPad-Origin and echo it in the event they cause.
"""
import hashlib
import itertools
import json
import os
import queue
import re
import select
import shutil
import socket
import sys
import tempfile
import threading
import time
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import kits  # W3: /api/kits (kits.py next to this file)
import mega as M  # CHOP-13: the drum pad mega bank store (mega.py next to this file)
import rendered  # W3b: resample uploads, files of a song a pad can play (rendered.py next to this file)

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "components", "control"))
import choppad_actions as A  # noqa: E402  (server-side twin of components/ui/actions.js)
import choppad_export as X  # noqa: E402  (song_project: the read-only mega-first project of a song)
UI_DIR = os.path.join(REPO, "components", "ui")
MIDI_JS = os.path.join(REPO, "components", "midi", "midi.js")
CAPTURE_WORKLET_JS = os.path.join(REPO, "components", "integrate", "capture_worklet.js")  # W3b: master-bus capture (AudioWorklet)
CHUNK = 256 * 1024
MAX_RANGE = 8 * 2**20  # answer open-ended ranges with at most 8 MiB; browsers ask again for more
MIME = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".mjs": "text/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8", ".json": "application/json", ".svg": "image/svg+xml", ".png": "image/png",
        ".jpg": "image/jpeg", ".webp": "image/webp", ".mp4": "video/mp4", ".wav": "audio/wav", ".sfz": "text/plain", ".zip": "application/zip",
        ".xml": "application/xml", ".ico": "image/x-icon"}
MAX_UPLOAD = 300 * 2**20
AUDIO_EXT = {".mp3", ".wav", ".flac", ".ogg", ".oga", ".opus", ".m4a", ".aac", ".aif", ".aiff", ".mp4", ".mov", ".webm", ".mkv"}
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
PEAK_POINTS = 6000


def compute_peaks(media_dir: str, points: int = PEAK_POINTS) -> dict:
    """Max-abs peaks of audio.wav for the waveform view; cached as peaks.json."""
    path = os.path.join(media_dir, "peaks.json")
    if os.path.isfile(path):
        return read_json(path)
    import numpy as np
    import soundfile as sf
    audio, sr = sf.read(os.path.join(media_dir, "audio.wav"), dtype="float32", always_2d=True)
    mono = np.abs(audio).max(axis=1)
    n = len(mono)
    edges = np.linspace(0, n, points + 1).astype(int)
    peaks = [float(mono[a:b].max()) if b > a else 0.0 for a, b in zip(edges, edges[1:])]
    top = max(peaks) or 1.0
    doc = {"duration_s": n / sr, "peaks": [round(p / top, 4) for p in peaks]}
    write_json_atomic(path, doc)
    return doc


def load_project(media_dir: str) -> dict:
    """The project of the song's mega bank (read only, nothing is created); without one the song's old project.json, else empty."""
    return X.song_project(media_dir) or M.empty_project()


def read_json(path: str):
    with open(path) as f:
        return json.load(f)


def write_json_atomic(path: str, doc) -> None:
    """Write JSON so a reader (or a crash) never sees a half-written file: temp file in the same folder, then rename."""
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(doc, f)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


TRANSPORT_COMMANDS = ("play-pattern", "stop", "play-song", "hit", "resample")
KEEPALIVE_S = 10.0


class Client:
    """One open event stream. `roles`: a browser tab that asked for a role (`?role=1`): see Hub._assign_locked."""
    _seq = itertools.count()

    def __init__(self, origin: str = "", roles: bool = False):
        self.origin = origin
        self.q = queue.Queue(maxsize=1000)
        self.roles = roles
        self.seq = next(Client._seq)  # connection order
        self.primary = None           # None until told


class Hub:
    """Fan-out of server-sent events, per song. publish() never blocks: a client whose queue is full is dropped."""
    def __init__(self):
        self.lock = threading.Lock()
        self.subs = {}  # song id -> set[Client]
        self.closed = False

    def subscribe(self, sid, origin="", roles=False):
        c = Client(origin, roles)
        with self.lock:
            if self.closed:
                c.q.put(None)
            self.subs.setdefault(sid, set()).add(c)
            self._assign_locked(sid)
        return c

    def unsubscribe(self, sid, c):
        with self.lock:
            self.subs.get(sid, set()).discard(c)
            self._assign_locked(sid)

    def _assign_locked(self, sid):
        """CHOP-11: of the tabs that asked for a role the first to connect is `primary` (the only one that executes
        transport commands: play, stop, hit), the others only display. Each tab is told its role (`role` event
        {primary}) when it changes; when the primary goes away the next oldest tab is promoted. Caller holds the lock."""
        tabs = sorted((c for c in self.subs.get(sid, ()) if c.roles), key=lambda c: c.seq)
        for i, c in enumerate(tabs):
            if c.primary != (i == 0):
                c.primary = i == 0
                try:
                    c.q.put_nowait(f"event: role\ndata: {json.dumps({'primary': c.primary, 'tabs': len(tabs)}, separators=(',', ':'))}\n\n")
                except queue.Full:
                    pass

    def listeners(self, sid):
        with self.lock:
            return len(self.subs.get(sid, ()))

    def publish(self, sid, event, data):
        """Send one event to every stream of song `sid` except the one that caused it. Returns how many got it."""
        msg = f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"
        n = 0
        with self.lock:
            for c in list(self.subs.get(sid, ())):
                if c.origin and c.origin == data.get("origin"):
                    continue
                try:
                    c.q.put_nowait(msg)
                    n += 1
                except queue.Full:  # a stuck client must not stall the others
                    self.subs[sid].discard(c)
                    self._assign_locked(sid)
        return n

    def close_all(self):
        with self.lock:
            self.closed = True
            for subs in self.subs.values():
                for c in subs:
                    try:
                        c.q.put_nowait(None)
                    except queue.Full:
                        pass


class ChopPadServer(ThreadingHTTPServer):
    daemon_threads = True  # long-lived event streams must never keep the process alive

    def __init__(self, addr, handler, hub):
        super().__init__(addr, handler)
        self.hub = hub

    def shutdown(self):
        self.hub.close_all()  # wake every event stream so its thread ends
        super().shutdown()


MEGA_SUBS = ("events", "actions", "transport", "source", "keep", "project", "resample", "regrid", "reanalyze", "sweep", "loadkit")
# actions that can leave a sample file of the mega bank without an override (a pad released, kept again as something else, ...)
SWEEP_AFTER = frozenset({"slice.clear", "slice.keep", "slice.set", "slice.setMany", "slice.src", "slice.stem", "slice.stretch", "kit.load"})
LIBRARY = "*"  # hub key of the library-level stream (GET /api/events): events that belong to no song yet


def job_event(j):
    """A job document as the `job` event sent on GET /api/events: {job, state, step, progress, song?, error?}."""
    ev = {"job": j["job"], "state": j["state"], "step": j.get("stage"), "progress": j.get("progress", 0.0)}
    if j.get("id"):
        ev["song"] = j["id"]
    if j.get("error"):
        ev["error"] = j["error"]
    return ev


class Jobs:
    def __init__(self):
        self.lock = threading.Lock()
        self.jobs = {}
        self.on_change = None  # fn(job document) after every change; make_server wires it to the library stream

    def get(self, jid):
        with self.lock:
            return dict(self.jobs.get(jid) or {})

    def snapshot(self, limit=50):
        """The newest jobs, oldest first (what a new library stream is told first, so it cannot miss a job that moved on)."""
        with self.lock:
            return [dict(j) for j in list(self.jobs.values())[-limit:]]

    def _changed(self, jid):
        if self.on_change:
            try:
                self.on_change(self.get(jid))
            except Exception:  # noqa: BLE001 - a listener must not break an import
                pass

    def update(self, jid, **kw):
        with self.lock:
            self.jobs[jid].update(kw)
        self._changed(jid)

    def start(self, media_root, url, pipeline):
        jid = uuid.uuid4().hex[:12]
        with self.lock:
            self.jobs[jid] = {"job": jid, "state": "queued", "stage": "queued", "progress": 0.0, "url": url}
        self._changed(jid)
        threading.Thread(target=self._run, args=(jid, media_root, url, pipeline), daemon=True).start()
        return jid

    def _run(self, jid, media_root, url, pipeline):
        try:
            def stage(name, p):
                self.update(jid, state="running", stage=name, progress=p)
            media_dir = pipeline(url, media_root, on_stage=stage)
            stage("waveform", 0.95)
            compute_peaks(media_dir)
            self.update(jid, state="done", stage="done", progress=1.0, id=os.path.basename(media_dir))
        except Exception as e:  # noqa: BLE001 - surfaced to the UI
            # a ValueError is a reason the person can act on (too long, a playlist link): show the sentence as it is
            self.update(jid, state="error", stage="error",
                        error=str(e) if isinstance(e, ValueError) and str(e) else f"{type(e).__name__}: {e}")


class TokenBucket:
    """Allows `rate` events a second with bursts up to `burst`; thread-safe, never blocks."""
    def __init__(self, rate, burst):
        self.rate, self.burst, self.tokens, self.t = float(rate), float(burst), float(burst), time.monotonic()
        self.lock = threading.Lock()

    def take(self):
        with self.lock:
            now = time.monotonic()
            self.tokens = min(self.burst, self.tokens + (now - self.t) * self.rate)
            self.t = now
            if self.tokens >= 1:
                self.tokens -= 1
                return True
            return False


LOCAL_RATE = (80, 40)  # a tab's own pad hits and play/stop handed to a DAW bridge: events per second, burst


def make_server(media_root: str, port: int = 8960, *, pipeline=None, regrid=None, reanalyze=None, actions=None,
                host: str = "127.0.0.1", on_transport=None, daw_state=None, mega_actions=None):
    actions = actions or {}  # name -> fn(media_dir, body) -> json-able; POST /api/songs/<id>/<name>
    # CHOP-13 step D: name -> fn(mega.Target, body) -> json-able; POST /api/mega/<mid>/<name>, and the song-keyed route of the same name
    # (which resolves the song's mega bank and calls the same function). megaops.make_ops builds the real table.
    mega_actions = mega_actions or {}
    media_root = os.path.abspath(media_root)
    jobs = Jobs()
    hub = Hub()
    jobs.on_change = lambda j: hub.publish(LIBRARY, "job", job_event(j))
    view_state = {}  # mega id -> {bank, selected, settings, sections}: what the player looks at (never saved)
    local_buckets, buckets_guard = {}, threading.Lock()  # mega id -> TokenBucket for `local` transport

    def daw_of(key):
        """{open, daw, bank} of the DAW bridge holding `key` (W2 DawManager.state_for), or "closed"."""
        return daw_state(key) if daw_state else {"open": False, "daw": None, "bank": None}

    def holder(mid, src=None):
        """The key a DAW bridge holds for mega bank `mid`: the mega id (step D); a manager keyed by song (the plain hook of the song-keyed
        tests) is found through the source song."""
        if not daw_state:
            return src or mid   # no state hook: the plain song-keyed mirroring of the song-keyed tests
        if not daw_of(mid).get("open") and src and daw_of(src).get("open"):
            return src
        return mid

    def daw_for(mid, src=None):
        return daw_of(holder(mid, src))
    locks, locks_guard = {}, threading.Lock()

    def mega_lock(mid):
        """One lock per mega bank: held across every read-modify-write of its mega.json."""
        with locks_guard:
            return locks.setdefault(mid, threading.Lock())

    def mega_doc_of(sid):
        """The song's mega bank (made on first use; the library is told `mega created`). KeyError for an unknown song."""
        existed = X.latest_mega_doc(media_root, sid) is not None
        doc = M.mega_for_song(media_root, sid)
        if not existed:
            mega_event("created", doc)
        return doc

    def mega_of(sid):
        """Id of the song's mega bank (made on first use). KeyError for an unknown song."""
        return mega_doc_of(sid)["id"]

    def mega_event(change, doc):
        """Library-level `mega` event {change: created|renamed|removed|source, id, name, source}: other tabs refresh their list."""
        hub.publish(LIBRARY, "mega", {"change": change, "id": doc["id"], "name": doc.get("name"), "source": doc.get("source")})

    def source_chops_event(sid, origin, chops):
        """A regrid or reanalyze of song `sid`: every mega bank that has it as its source is told."""
        for mid in M.megas_with_source(media_root, sid):
            hub.publish(mid, "chops", {"origin": origin, "chops": chops})

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):  # quiet
            pass

        # ---------- helpers ----------
        def _send(self, code, body: bytes, ctype, extra=None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, obj, code=200):
            self._send(code, json.dumps(obj).encode(), "application/json")

        def _err(self, code, msg):
            self._json({"error": msg}, code)

        def handle_one_request(self):
            self._body_cache = None
            super().handle_one_request()

        def _body(self):
            """The JSON body, read once per request (a second call returns the same document)."""
            if getattr(self, "_body_cache", None) is None:
                n = int(self.headers.get("Content-Length") or 0)
                self._body_cache = json.loads(self.rfile.read(n) or b"{}")
            return self._body_cache

        def _static(self, path):
            if not os.path.isfile(path):
                return self._err(404, "not found")
            ext = os.path.splitext(path)[1].lower()
            self._send(200, open(path, "rb").read(), MIME.get(ext, "application/octet-stream"))

        def _media(self, path):
            if not os.path.isfile(path):
                return self._err(404, "not found")
            size = os.path.getsize(path)
            ctype = MIME.get(os.path.splitext(path)[1].lower(), "application/octet-stream")
            rng = self.headers.get("Range")
            start, end, code = 0, size - 1, 200
            if rng:
                m = re.match(r"bytes=(\d*)-(\d*)$", rng.strip())
                if not m or (not m.group(1) and not m.group(2)):
                    return self._err(416, "bad range")
                if m.group(1):
                    start = int(m.group(1))
                    end = int(m.group(2)) if m.group(2) else min(size - 1, start + MAX_RANGE - 1)
                else:  # suffix range: last N bytes
                    start = max(0, size - int(m.group(2)))
                end = min(end, size - 1)
                if start > end:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                code = 206
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(end - start + 1))
            if code == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            if self.command == "HEAD":
                return
            with open(path, "rb") as f:
                f.seek(start)
                left = end - start + 1
                while left > 0:
                    buf = f.read(min(CHUNK, left))
                    if not buf:
                        break
                    self.wfile.write(buf)
                    left -= len(buf)

        def _origin(self):
            return (self.headers.get("X-ChopPad-Origin") or "")[:64]

        def _peer_gone(self):
            """True when the client closed its end of this connection (readable with EOF)."""
            try:
                r, _, _ = select.select([self.connection], [], [], 0)
                return bool(r) and self.connection.recv(1, socket.MSG_PEEK) == b""
            except (OSError, ValueError):
                return True

        def _events(self, sid, dawsid=None):
            """Server-sent events: stays open until the client goes away or the server shuts down.
            sid is a mega bank id, or LIBRARY for the library-level stream (`job` events of imports); `dawsid` = the song a
            DAW bridge would hold for it (the source)."""
            origin = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("origin", [""])[0][:64]
            client = hub.subscribe(sid, origin, roles=urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("role") == ["1"])
            self.close_connection = True
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "close")
                self.send_header("X-Accel-Buffering", "no")
                self.end_headers()
                self.wfile.write(b"retry: 1500\n: connected\n\n")
                if sid == LIBRARY:  # CHOP-11: told about every recent job first, so a job that moved on cannot be missed
                    for j in jobs.snapshot():
                        self.wfile.write(f"event: job\ndata: {json.dumps(job_event(j), separators=(',', ':'))}\n\n".encode())
                elif daw_for(sid, dawsid).get("open"):  # CHOP-11: a tab that connects while a bridge is open is told at once
                    self.wfile.write(f"event: daw\ndata: {json.dumps(daw_for(sid, dawsid), separators=(',', ':'))}\n\n".encode())
                self.wfile.flush()
                last = time.monotonic()
                while True:
                    try:
                        msg = client.q.get(timeout=1.0)
                    except queue.Empty:
                        # a closed tab is noticed within a second (its socket reads EOF), so the next tab is promoted
                        # quickly; the ping is only to keep proxies and idle timers awake
                        if self._peer_gone():
                            break
                        if time.monotonic() - last < KEEPALIVE_S:
                            continue
                        msg = ": ping\n\n"
                    last = time.monotonic()
                    if msg is None:
                        break
                    self.wfile.write(msg.encode())
                    self.wfile.flush()
            except OSError:
                pass  # BrokenPipe, ConnectionReset: the tab closed
            finally:
                hub.unsubscribe(sid, client)

        def _mega_state(self, doc, chops=None):
            """The reducer state of mega bank `doc`: its project, the chops of its source, and what the player looks at."""
            mid = doc["id"]
            view = view_state.get(mid) or {}
            state = A.initial_state(doc["project"], chops if chops is not None else M.chops_of(media_root, doc), view.get("sections"),
                                    source=doc.get("source"), mega=mid)
            state.update({k: view[k] for k in ("bank", "selected", "settings") if k in view})
            state["bank"] = max(0, min(state["bank"], A.bank_count(state["project"], state["chops"]) - 1))
            return state

        def _commit(self, doc, state, taken, before):
            """Under the mega bank's lock: remember the view, save the mega bank when its project or source changed, tell the
            other streams. `taken` = the actions that changed something (sent as one `actions` batch)."""
            mid = doc["id"]
            view_state[mid] = {k: state[k] for k in ("bank", "selected", "settings", "sections")}
            if state["project"] is not before or state.get("source") != doc.get("source"):
                doc = dict(doc, project=A.project_doc(state), source=state.get("source"))
                M.touch(doc)
                M.write(media_root, doc)
            if taken:
                hub.publish(mid, "actions", {"origin": self._origin(), "actions": taken})
            return doc

        def _apply_actions(self, mid, body=None):
            body = self._body() if body is None else body
            acts = body.get("actions") if isinstance(body, dict) else None
            if not isinstance(acts, list) or not all(isinstance(a, dict) for a in acts):
                return self._err(400, "body must be {actions: [action, ...]}")
            with mega_lock(mid):
                try:
                    doc = M.read(media_root, mid)
                except KeyError:
                    return self._err(404, "unknown mega bank")
                state = self._mega_state(doc)
                before = state["project"]
                taken = []
                for a in acts:
                    if a.get("type") not in A.ACTION_TYPES or a.get("type") in ("chops.replace", "source.set"):
                        continue  # unknown; chops change through /regrid, the source through /source
                    nxt = A.reduce(state, a)
                    if nxt is not state or a["type"] in A.VIEW_ACTIONS:
                        taken.append(a)
                    state = nxt
                self._commit(doc, state, taken, before)
                if any(a.get("type") in SWEEP_AFTER for a in taken):
                    M.sweep_samples(media_root, mid)   # under the lock: the document it reads is the one just written
                listeners = hub.listeners(mid)
            return self._json({"project": A.project_doc(state), "applied": len(taken), "ignored": len(acts) - len(taken),
                               "view": {"bank": state["bank"], "selected": state["selected"]}, "listeners": listeners, "mega": mid})

        def _run_batch(self, doc, state, acts):
            """Apply `acts` to `state` with the reducer; returns (state, the actions that changed something)."""
            taken = []
            for a in acts:
                nxt = A.reduce(state, a)
                if nxt is not state:
                    taken.append(a)
                state = nxt
            return state, taken

        def _keep_actions(self, doc, state, cells, *, skip_empty):
            """`slice.keep` actions for the pads `cells` [(bank, pad)] that do not own their audio yet: renders each pad as played
            (without its sound params and the master fx) into the mega bank's samples/. Returns (actions, kept, already, failed)."""
            project, mid = state["project"], doc["id"]
            acts, kept, already, failed = [], [], [], []
            for bank, pad in cells:
                o = project["overrides"].get(f"{bank}:{pad}")
                if isinstance(o, dict) and o.get("kept") is True:
                    already.append({"bank": bank, "pad": pad})
                    continue
                try:
                    r = M.render_keep(media_root, mid, doc.get("source"), project, state["chops"], bank, pad)
                except M.EmptyPad as e:
                    if not skip_empty:
                        failed.append({"bank": bank, "pad": pad, "reason": str(e)})
                    continue
                except ValueError as e:
                    failed.append({"bank": bank, "pad": pad, "reason": str(e)})
                    continue
                # the pad's sound as it is now, frozen into the override (the same cleaning the reducer applies)
                acts.append({"type": "slice.keep", "bank": bank, "pad": pad, **r, "params": A.clean_pad(project["pads"][pad])})
                kept.append({"bank": bank, "pad": pad})
            return acts, kept, already, failed

        def _source(self, mid):
            """POST /api/mega/<mid>/source {song}: option (b) "keep current drum pad" (contracts §15)."""
            body = self._body()
            song = body.get("song") if isinstance(body, dict) else None
            clearing = isinstance(body, dict) and "song" in body and song is None   # step D: leave the mega bank with no source
            if not clearing and (not isinstance(song, str) or not M.song_dir(media_root, song)):
                return self._err(404, "unknown song")
            with mega_lock(mid):
                try:
                    doc = M.read(media_root, mid)
                except KeyError:
                    return self._err(404, "unknown mega bank")
                if doc.get("source") == song:  # already the source (or already none): nothing to do
                    return self._json({"mega": M.summary(media_root, doc), "kept": [], "released": 0, "failed": [], "actions": []})
                new_chops = None
                if not clearing:
                    try:
                        new_chops = read_json(os.path.join(M.song_dir(media_root, song), "chops.json"))
                    except (OSError, ValueError):
                        return self._err(400, "the song has no chop table")
                state = self._mega_state(doc)
                before, old_chops = state["project"], state["chops"]
                acts = []
                tempo = before.get("tempo")
                # the user's working tempo must not jump to the new song's: while none is set, freeze the old source's own
                if doc.get("source") and (not tempo or tempo.get("bpm") is None) and A._is_num(old_chops.get("bpm")):
                    acts.append({"type": "tempo.set", "bpm": old_chops["bpm"], "follow": False})
                keeps, kept, _already, failed = self._keep_actions(doc, state, A.used_pads(before), skip_empty=True)
                acts += keeps + [{"type": "source.set", "song": song, "chops": new_chops}]   # song/chops null = no source
                state, taken = self._run_batch(doc, state, acts)
                cells = {(b, p) for b, idx in enumerate(old_chops.get("banks") or []) for p in range(min(16, len(idx)))}
                owned = {A._override_key(k) for k in state["project"]["overrides"]}
                doc = self._commit(doc, state, taken, before)
                mega_event("source", doc)
                return self._json({"mega": M.summary(media_root, doc), "kept": kept, "released": len(cells - owned), "failed": failed,
                                   "view": {"bank": state["bank"]}, "actions": taken})   # `actions`: the batch broadcast to the other streams

        def _keep(self, mid):
            """POST /api/mega/<mid>/keep {bank, pad}: keep one pad now (the KEEP key)."""
            body = self._body()
            ok = lambda v, hi: isinstance(v, int) and not isinstance(v, bool) and 0 <= v < hi  # noqa: E731
            bank, pad = (body.get("bank"), body.get("pad")) if isinstance(body, dict) else (None, None)
            if not ok(bank, 10**6) or not ok(pad, 16):
                return self._err(400, "keep needs bank (0 or more) and pad (0..15)")
            with mega_lock(mid):
                try:
                    doc = M.read(media_root, mid)
                except KeyError:
                    return self._err(404, "unknown mega bank")
                state = self._mega_state(doc)
                before = state["project"]
                acts, kept, already, failed = self._keep_actions(doc, state, [(bank, pad)], skip_empty=False)
                state, taken = self._run_batch(doc, state, acts)
                doc = self._commit(doc, state, taken, before)
                if taken:
                    M.sweep_samples(media_root, mid)
                return self._json({"mega": M.summary(media_root, doc), "kept": kept, "already": already, "failed": failed, "actions": taken})

        def _loadkit(self, mid, body):
            """POST /api/mega/<mid>/loadkit {kit: id, bank}: a kit on a bank of this mega bank. Kept pads of the kit are COPIED into the mega
            bank (media/_mega/<mid>/samples/) and become `slice.keep`; the rest is one `kit.load`; the kit's master fx are applied.
            One batch is saved and broadcast; it is also returned as `actions` (the calling tab applies it, like /keep)."""
            kid = body.get("kit") if isinstance(body, dict) else None
            bank = body.get("bank", 0) if isinstance(body, dict) else 0
            if not isinstance(bank, int) or isinstance(bank, bool) or bank < 0:
                return self._err(400, "bank must be 0 or more")
            try:
                kit = kits.read_kit(media_root, kid if isinstance(kid, str) else "")
            except KeyError:
                return self._err(404, "unknown kit")
            with mega_lock(mid):
                try:
                    doc = M.read(media_root, mid)
                except KeyError:
                    return self._err(404, "unknown mega bank")
                try:
                    acts, copied = kits.kept_actions(media_root, mid, kit, bank, doc.get("source"))
                except OSError as e:
                    return self._err(500, f"a sound of the kit could not be copied: {e}")
                if kit.get("fx"):
                    acts.append({"type": "fx.set", "params": dict(kit["fx"])})
                state = self._mega_state(doc)
                before = state["project"]
                state, taken = self._run_batch(doc, state, acts)
                doc = self._commit(doc, state, taken, before)
                if any(a.get("type") in SWEEP_AFTER for a in taken):
                    M.sweep_samples(media_root, mid)
                return self._json({"ok": True, "loaded": sum(1 for p in kit["pads"] if p), "name": kit.get("name"), "kept": copied, "actions": taken,
                                   "mega": M.summary(media_root, doc)})

        def _sweep(self, mid):
            """POST /api/mega/<mid>/sweep: move the samples no override uses to media/_mega/<mid>/.trash/ (never deletes)."""
            with mega_lock(mid):
                try:
                    return self._json(M.sweep_samples(media_root, mid))
                except KeyError:
                    return self._err(404, "unknown mega bank")

        def _transport(self, mid, src):
            """`src`: the source song of the mega bank (a DAW manager keyed by song is found through it), or None."""
            dawsid = holder(mid, src)
            body = self._body()
            cmd = body.get("command") if isinstance(body, dict) else None
            if cmd not in TRANSPORT_COMMANDS:
                return self._err(400, f"command must be one of {', '.join(TRANSPORT_COMMANDS)}")
            msg = {"origin": self._origin(), "command": cmd}
            if cmd == "hit":
                pad = body.get("pad")
                vel = body.get("velocity", 1)
                if not isinstance(pad, int) or isinstance(pad, bool) or not 0 <= pad < 16:
                    return self._err(400, "hit needs pad 0..15")
                if not isinstance(vel, (int, float)) or isinstance(vel, bool) or not 0 <= vel <= 1:
                    return self._err(400, "velocity must be 0..1")
                msg.update(pad=pad, velocity=vel)
                if isinstance(body.get("bank"), int) and not isinstance(body["bank"], bool):
                    msg["bank"] = body["bank"]
            if cmd == "play-song" and isinstance(body.get("from"), (int, float)):
                msg["from"] = body["from"]
            if cmd == "resample":  # W3b: the open browser renders or records, uploads and puts the result on a pad
                num = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0  # noqa: E731
                if body.get("source", "pattern") not in ("pattern", "master"):
                    return self._err(400, "source must be pattern or master")
                if not (num(body.get("bars")) or num(body.get("seconds"))):
                    return self._err(400, "resample needs bars or seconds")
                if not (isinstance(body.get("pad"), int) and not isinstance(body["pad"], bool) and 0 <= body["pad"] < 16):
                    return self._err(400, "resample needs the pad (0..15) to put the result on")
                msg.update({k: body[k] for k in ("source", "bars", "seconds", "pad", "bank") if body.get(k) is not None})
            if body.get("local") is True:
                return self._local_transport(mid, dawsid, msg)
            listeners = hub.publish(mid, "transport", msg)
            if on_transport:  # W2: mirror to open DAW bridges (never lets a DAW problem fail the request)
                on_transport(dawsid, msg)
            return self._json({"ok": True, "listeners": listeners})

        # ---- CHOP-11: what a tab played itself (pad hit, pattern play/stop) goes to the open DAW bridge only ----
        def _local_transport(self, mid, dawsid, msg):
            """`local: true`: the tab already did it. NOT published to the other tabs (they would play it too); handed to
            the DAW bridge when one is open, rate limited per mega bank. Answers at once; never waits on the DAW."""
            if not daw_of(dawsid).get("open"):
                return self._json({"ok": True, "delivered": False, "reason": "no bridge open"})
            with buckets_guard:
                bucket = local_buckets.setdefault(mid, TokenBucket(*LOCAL_RATE))
            if not bucket.take():
                return self._json({"ok": True, "delivered": False, "reason": "rate limited"}, 429)
            if on_transport:
                on_transport(dawsid, {**msg, "local": True})
            return self._json({"ok": True, "delivered": True})

        def _put_project(self, mid, proj):
            """Whole-document save into mega bank `mid` (PUT .../project): validated like before, other streams learn the difference
            as ordinary §8 actions."""
            if not isinstance(proj, dict) or not isinstance(proj.get("pads"), list) or not isinstance(proj.get("overrides"), dict):
                return self._err(400, "project needs pads[] and overrides{}")
            doc_ = {"pads": proj["pads"][:16], "overrides": proj["overrides"]}
            if isinstance(proj.get("pattern"), dict):
                doc_["pattern"] = proj["pattern"]
            if isinstance(proj.get("fx"), dict):
                doc_["fx"] = {k: float(v) for k, v in proj["fx"].items() if isinstance(v, (int, float))}
            for k in ("tempo", "song"):  # W3b: optional project keys
                if isinstance(proj.get(k), dict):
                    doc_[k] = proj[k]
            with mega_lock(mid):
                try:
                    doc = M.read(media_root, mid)
                except KeyError:
                    return self._err(404, "unknown mega bank")
                old = A.initial_state(doc["project"])["project"]
                M.write(media_root, M.touch(dict(doc, project=doc_)))
                # other tabs learn about a whole-document save as ordinary §8 actions
                changes = A.diff_project(old, A.initial_state(doc_)["project"])
                if changes:
                    hub.publish(mid, "actions", {"origin": self._origin(), "actions": changes})
            return self._json({"ok": True})

        def _mega_doc_or_404(self, mid):
            try:
                return M.read(media_root, mid)
            except KeyError:
                self._err(404, "unknown mega bank")
                return None

        def _mega_view(self, doc):
            """GET /api/mega/<mid>: {mega, project, chops (of the source, or an empty table), view}."""
            view = view_state.get(doc["id"]) or {}
            src = doc.get("source")
            return {"mega": M.summary(media_root, doc), "project": doc["project"], "chops": M.chops_of(media_root, doc),
                    "view": {k: view.get(k) for k in ("bank", "selected")}, "daw": daw_for(doc["id"], src)}

        def _mega_routes(self, method, p):
            """True when `p` is under /api/mega: it has then been answered."""
            if p != "/api/mega" and not p.startswith("/api/mega/"):
                return False
            self._mega_dispatch(method, p)
            return True

        def _mega_dispatch(self, method, p):
            if p == "/api/mega":
                if method == "GET":
                    out = [M.summary(media_root, d) for d in M.list_docs(media_root)]
                    return self._json(out) or True
                if method == "POST":
                    body = self._body()
                    body = body if isinstance(body, dict) else {}
                    src = body.get("source")
                    if src is not None and not M.song_dir(media_root, src if isinstance(src, str) else ""):
                        return self._err(404, "unknown song") or True
                    try:
                        doc = M.create(media_root, body.get("name"), src)
                    except ValueError as e:
                        return self._err(400, str(e)) or True
                    mega_event("created", doc)
                    return self._json(self._mega_view(doc), 201) or True
                return self._err(405, "method not allowed") or True
            m = re.match(r"^/api/mega/([^/]+)(?:/([a-z]+))?$", p)
            if not m or (m.group(2) and m.group(2) not in MEGA_SUBS and m.group(2) not in mega_actions):
                return self._err(404, "not found")
            mid, sub = m.group(1), m.group(2)
            if method == "POST" and sub == "resample":  # a raw WAV body, not JSON
                return self._mega_resample(mid) or True
            body = self._body() if method in ("POST", "PUT") else None  # read now: an error answer must not leave it on the connection
            if method == "GET" and sub is None:
                doc = self._mega_doc_or_404(mid)
                return self._json(self._mega_view(doc)) if doc else True
            if method == "GET" and sub == "events":
                doc = self._mega_doc_or_404(mid)
                if not doc:
                    return True
                if self.command == "HEAD":
                    return self._err(405, "GET only") or True
                return self._events(mid, doc.get("source")) or True
            if method == "PUT" and sub is None:
                doc = self._mega_doc_or_404(mid)
                if not doc:
                    return True
                try:
                    with mega_lock(mid):
                        doc = M.rename(media_root, mid, body.get("name") if isinstance(body, dict) else None)
                except ValueError as e:
                    return self._err(400, str(e)) or True
                mega_event("renamed", doc)
                return self._json({"mega": M.summary(media_root, doc)}) or True
            if method == "PUT" and sub == "project":
                return self._put_project(mid, body) or True
            if method == "DELETE" and sub is None:
                doc = self._mega_doc_or_404(mid)
                if not doc:
                    return True
                with mega_lock(mid):
                    # allowed while a tab has it open: that tab (and any other on its stream) is told first and leaves cleanly; it must
                    # not write again (a write would answer 404). The DAW bridge holding it is closed.
                    hub.publish(mid, "mega", {"change": "removed", "id": mid, "name": doc.get("name")})
                    if daw_for(mid, doc.get("source")).get("open") and "daw" in mega_actions:
                        try:
                            mega_actions["daw"](M.Target(media_root, doc), {"daw": daw_for(mid, doc.get("source")).get("daw"), "command": "close"})
                        except Exception:  # noqa: BLE001 - a bridge that does not close must not keep a mega bank alive
                            pass
                    M.delete(media_root, mid)
                view_state.pop(mid, None)
                with buckets_guard:
                    local_buckets.pop(mid, None)
                mega_event("removed", doc)
                return self._json({"ok": True}) or True
            if method == "POST" and sub in ("actions", "transport", "source", "keep", "regrid", "reanalyze", "sweep", "loadkit") or (method == "POST" and sub in mega_actions):
                doc = self._mega_doc_or_404(mid)
                if not doc:
                    return True
                if sub == "actions":
                    return self._apply_actions(mid, body) or True
                if sub == "transport":
                    return self._transport(mid, doc.get("source")) or True
                if sub in ("regrid", "reanalyze"):
                    return self._source_regrid(doc, sub, body) or True
                if sub == "sweep":
                    return self._sweep(mid) or True
                if sub == "loadkit":
                    return self._loadkit(mid, body) or True
                if sub in mega_actions:
                    return self._mega_action(doc, sub, body) or True
                return (self._source(mid) if sub == "source" else self._keep(mid)) or True
            return self._err(405, "method not allowed") or True

        def _mega_action(self, doc, name, body):
            """Run mega-keyed operation `name` (megaops) on exactly this mega bank; errors as the song-keyed routes answer them, a
            missing source song as 409 with a plain sentence."""
            t = M.Target(media_root, doc, (view_state.get(doc["id"]) or {}).get("settings"))
            try:
                return self._json(mega_actions[name](t, body if isinstance(body, dict) else {}))
            except ValueError as e:  # bad input for the action (stretch, export of a mixed kit, ...)
                return self._err(400, str(e))
            except Exception as e:  # noqa: BLE001 - shown in the UI
                return self._err(getattr(e, "http_status", None) or (502 if name == "advise" else 500),
                                 str(e) if hasattr(e, "http_status") else f"{type(e).__name__}: {e}")

        def _source_regrid(self, doc, op, body):
            """/api/mega/<mid>/regrid | reanalyze: these belong to the mega bank's SOURCE song (409 when there is none)."""
            if not doc.get("source") or not M.song_dir(media_root, doc["source"]):
                return self._err(409, M.NO_SOURCE_MSG.format(what="re-grid" if op == "regrid" else "re-analyse"))
            body = body if isinstance(body, dict) else {}
            return self._regrid_song(doc["source"], body, reanalyze_only=op == "reanalyze")

        def _mega_resample(self, mid):
            """POST /api/mega/<mid>/resample (raw 16-bit WAV body) -> the sample is stored under the mega bank, so it survives the song."""
            n = int(self.headers.get("Content-Length") or 0)
            if n > rendered.MAX_BYTES:
                self.close_connection = True
                return self._err(413, f"the WAV must be at most {rendered.MAX_BYTES // 2**20} MB")
            data = self.rfile.read(n)
            doc = self._mega_doc_or_404(mid)
            if not doc:
                return True
            try:
                up = rendered.save_mega_sample(os.path.join(media_root, M.ROOT, mid, "samples"), mid, data)
            except ValueError as e:
                return self._err(400, str(e))
            M.pin_for(mid, [up["hash"] + ".wav"])   # not an override's yet: the sweeper leaves it alone for a while
            return self._json(up)

        def _song_dir(self, sid):
            if not ID_RE.match(sid or ""):
                return None
            d = os.path.join(media_root, sid)
            return d if os.path.isfile(os.path.join(d, "meta.json")) else None

        # ---------- routes ----------
        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            try:
                self._route_get()
            except (BrokenPipeError, ConnectionResetError):
                pass  # browser cancelled (normal for <video> seeking)

        # ---- W3: kits (all logic in kits.py) ----
        def _kits(self, method):
            body = self._body() if method in ("POST", "PUT") else {}
            code, obj = kits.handle(media_root, method, self.path.split("?", 1)[0], body)
            self._json(obj, code)

        def do_DELETE(self):
            try:
                if self.path.split("?", 1)[0].startswith("/api/kits/"):
                    return self._kits("DELETE")
                if self._mega_routes("DELETE", self.path.split("?", 1)[0]):
                    return
                self._err(404, "not found")
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_PUT(self):
            try:
                if self.path.split("?", 1)[0].startswith("/api/kits/"):
                    return self._kits("PUT")
                if self._mega_routes("PUT", self.path.split("?", 1)[0]):
                    return
                m = re.match(r"^/api/songs/([^/]+)/project$", self.path.split("?", 1)[0])
                d = self._song_dir(m.group(1)) if m else None
                if not d:
                    return self._err(404, "not found")
                return self._put_project(mega_of(m.group(1)), self._body())
            except (BrokenPipeError, ConnectionResetError):
                pass
            except json.JSONDecodeError:
                self._err(400, "invalid JSON")

        def do_POST(self):
            try:
                self._route_post()
            except (BrokenPipeError, ConnectionResetError):
                pass
            except json.JSONDecodeError:
                self._err(400, "invalid JSON")

        def _route_get(self):
            p = self.path.split("?", 1)[0]
            if p in ("/", "/index.html"):
                return self._static(os.path.join(UI_DIR, "index.html"))
            if p == "/midi.js":
                return self._static(MIDI_JS)
            if p == "/capture-worklet.js":
                return self._static(CAPTURE_WORKLET_JS)
            if p.startswith("/media/"):
                parts = p[len("/media/"):].split("/")
                if parts[0] == M.ROOT:  # CHOP-13: only the samples (and, step D, the exports) of a mega bank are served from _mega
                    if len(parts) == 4 and parts[2] == "exports" and M.ID_RE.match(parts[1]) \
                            and re.match(r"^[A-Za-z0-9_-]{1,80}-bank\d{1,4}\.(zip|sfz)$", parts[3]):
                        return self._media(os.path.join(media_root, M.ROOT, parts[1], "exports", parts[3]))
                    if len(parts) != 4 or parts[2] != "samples" or not M.ID_RE.match(parts[1]) \
                            or not re.match(r"^[0-9a-f]{16}\.wav$", parts[3]):
                        return self._err(404, "not found")
                    return self._media(os.path.join(media_root, M.ROOT, parts[1], "samples", parts[3]))
                if len(parts) == 3 and parts[1] == "resample":  # W3b: rendered or recorded takes
                    if not ID_RE.match(parts[0]) or not re.match(r"^[0-9a-f]{16}\.wav$", parts[2]):
                        return self._err(404, "not found")
                    return self._media(os.path.join(media_root, parts[0], "resample", parts[2]))
                if len(parts) == 3 and parts[1] == "stems":  # W3b: stems of a song
                    if not ID_RE.match(parts[0]) or parts[2] not in tuple(f"{n}.wav" for n in rendered.STEMS):
                        return self._err(404, "not found")
                    return self._media(os.path.join(media_root, parts[0], "stems", parts[2]))
                if len(parts) == 3 and parts[1] == "stretch":  # W3: rendered time-stretched slices
                    if not ID_RE.match(parts[0]) or not re.match(r"^[0-9a-f]{16}\.wav$", parts[2]):
                        return self._err(404, "not found")
                    return self._media(os.path.join(media_root, parts[0], "stretch", parts[2]))
                if len(parts) != 2 or not ID_RE.match(parts[0]) or not re.match(r"^[\w.-]+$", parts[1]):
                    return self._err(404, "not found")
                return self._media(os.path.join(media_root, parts[0], parts[1]))
            if p == "/api/kits" or p.startswith("/api/kits/"):
                return self._kits("GET")
            if p == "/api/events":  # CHOP-11: library-level stream: `job` events {job, state, step, progress, song?, error?}
                if self.command == "HEAD":
                    return self._err(405, "GET only")
                return self._events(LIBRARY)
            if p == "/api/songs":
                return self._json(self._library())
            if self._mega_routes("GET", p):
                return
            m = re.match(r"^/api/songs/([^/]+)/events$", p)
            if m:
                if not self._song_dir(m.group(1)):
                    return self._err(404, "unknown song")
                if self.command == "HEAD":
                    return self._err(405, "GET only")
                return self._events(mega_of(m.group(1)), m.group(1))
            m = re.match(r"^/api/songs/([^/]+)$", p)
            if m:
                d = self._song_dir(m.group(1))
                if not d:
                    return self._err(404, "unknown song")
                load = lambda n: read_json(os.path.join(d, n))  # noqa: E731
                mdoc = mega_doc_of(m.group(1))  # CHOP-13: the project is the mega bank's (made on first use)
                return self._json({"meta": load("meta.json"), "analysis": load("analysis.json"),
                                   "chops": load("chops.json"), "peaks": compute_peaks(d), "project": mdoc["project"],
                                   "mega": mdoc["id"],
                                   "view": {k: (view_state.get(mdoc["id"]) or {}).get(k) for k in ("bank", "selected")},
                                   "daw": daw_for(mdoc["id"], m.group(1))})
            m = re.match(r"^/api/jobs/([0-9a-f]+)$", p)
            if m:
                j = jobs.get(m.group(1))
                return self._json(j) if j else self._err(404, "unknown job")
            # app files (no directory traversal)
            rel = os.path.normpath(p.lstrip("/"))
            if rel.startswith("..") or os.path.isabs(rel):
                return self._err(404, "not found")
            return self._static(os.path.join(UI_DIR, rel))

        def _route_post(self):
            p = self.path.split("?", 1)[0]
            if p == "/api/songs":
                if pipeline is None:
                    return self._err(501, "import disabled")
                url = (self._body().get("url") or "").strip()
                if not url:
                    return self._err(400, "url required")
                return self._json({"job": jobs.start(media_root, url, pipeline)}, 202)
            if p == "/api/kits" or p.startswith("/api/kits/"):
                return self._kits("POST")
            if self._mega_routes("POST", p):
                return
            if p == "/api/upload":
                return self._upload()
            m = re.match(r"^/api/songs/([^/]+)/resample$", p)   # W3b: raw WAV body -> media/<id>/resample/<hash>.wav
            if m:
                n = int(self.headers.get("Content-Length") or 0)
                if n > rendered.MAX_BYTES:
                    self.close_connection = True
                    return self._err(413, f"the WAV must be at most {rendered.MAX_BYTES // 2**20} MB")
                data = self.rfile.read(n)
                d = self._song_dir(m.group(1))
                if not d:
                    return self._err(404, "unknown song")
                try:
                    return self._json(rendered.save_resample(d, data))
                except ValueError as e:
                    return self._err(400, str(e))
            m = re.match(r"^/api/songs/([^/]+)/(actions|transport)$", p)
            if m:
                d = self._song_dir(m.group(1))
                if not d:
                    self.rfile.read(int(self.headers.get("Content-Length") or 0))  # keep the connection in step
                    return self._err(404, "unknown song")
                mid = mega_of(m.group(1))
                return self._apply_actions(mid) if m.group(2) == "actions" else self._transport(mid, m.group(1))
            m = re.match(r"^/api/songs/([^/]+)/([a-z]+)$", p)   # step D: the song's mega bank does it (the same code as /api/mega/<mid>/<name>)
            if m and m.group(2) in mega_actions:
                if not self._song_dir(m.group(1)):
                    self._body()
                    return self._err(404, "unknown song")
                return self._mega_action(mega_doc_of(m.group(1)), m.group(2), self._body())
            m = re.match(r"^/api/songs/([^/]+)/regrid$", p)
            if p == "/api/regrid" or m:
                body = self._body()
                sid = m.group(1) if m else body.get("id")
                if not self._song_dir(sid):
                    return self._err(404, "unknown song")
                return self._regrid_song(sid, body)
            m = re.match(r"^/api/songs/([^/]+)/([a-z]+)$", p)
            if m and m.group(2) in actions:
                d = self._song_dir(m.group(1))
                if not d:
                    return self._err(404, "unknown song")
                try:
                    abody = self._body()
                    if m.group(2) == "daw" and isinstance(abody, dict):  # swing/quant/choke: what the player is set to
                        abody = {**abody, "view_settings": (view_state.get(mega_of(m.group(1))) or {}).get("settings") or {}}
                    return self._json(actions[m.group(2)](d, abody))
                except ValueError as e:  # bad input for the action (W3: stretch, export of a mixed kit, ...)
                    return self._err(400, str(e))
                except Exception as e:  # noqa: BLE001 - shown in the UI
                    return self._err(getattr(e, "http_status", None) or (502 if m.group(2) == "advise" else 500),
                                     str(e) if hasattr(e, "http_status") else f"{type(e).__name__}: {e}")
            m = re.match(r"^/api/songs/([^/]+)/reanalyze$", p)
            if m and reanalyze:
                if not self._song_dir(m.group(1)):
                    return self._err(404, "unknown song")
                return self._regrid_song(m.group(1), self._body(), reanalyze_only=True)
            return self._err(404, "not found")

        def _regrid_song(self, sid, body, reanalyze_only=False):
            """Regrid (or reanalyze) song `sid` and tell every mega bank that has it as its source (the `chops` event)."""
            d = self._song_dir(sid)
            if reanalyze_only:
                if not reanalyze:
                    return self._err(404, "not found")
                bpm = body.get("bpm")
                analysis, chops = reanalyze(d, bpm=float(bpm) if bpm else None, meter=body.get("meter"))
                source_chops_event(sid, self._origin(), chops)
                return self._json({"analysis": analysis, "chops": chops})
            try:
                chops = regrid(d, grid=body.get("grid") or "1/4", meter=body.get("meter"),
                               beat_offset=int(body.get("beat_offset") or 0), bpm_mult=body.get("bpm_mult") or 1,
                               **({"mode": body["mode"], "sensitivity": body.get("sensitivity")} if body.get("mode") else {}))
            except (ValueError, KeyError) as e:
                return self._err(400, str(e))
            source_chops_event(sid, self._origin(), chops)
            return self._json(chops)

        def _upload(self):
            if pipeline is None:
                return self._err(501, "import disabled")
            name = os.path.basename(urllib.parse.unquote(self.headers.get("X-Filename") or "")).strip()
            ext = os.path.splitext(name)[1].lower()
            if not name or ext not in AUDIO_EXT:
                return self._err(400, f"unsupported file type {ext or '?'} (audio or video files only)")
            n = int(self.headers.get("Content-Length") or 0)
            if not 0 < n <= MAX_UPLOAD:
                return self._err(413, f"file must be under {MAX_UPLOAD // 2**20} MB")
            # stable path per content+name, so dropping the same file again reopens the same song
            h = hashlib.sha1()
            tmp = tempfile.NamedTemporaryFile(delete=False, dir=tempfile.gettempdir(), prefix="choppad-up-")
            with tmp:
                left = n
                while left:
                    chunk = self.rfile.read(min(CHUNK, left))
                    if not chunk:
                        break
                    h.update(chunk)
                    tmp.write(chunk)
                    left -= len(chunk)
            folder = os.path.join(tempfile.gettempdir(), "choppad-uploads", h.hexdigest()[:16])
            os.makedirs(folder, exist_ok=True)
            path = os.path.join(folder, name)
            shutil.move(tmp.name, path)

            def run(src, root, on_stage=None):
                try:
                    return pipeline(src, root, on_stage=on_stage)
                finally:
                    shutil.rmtree(folder, ignore_errors=True)  # media/<id>/ has its own copy now
            return self._json({"job": jobs.start(media_root, path, run)}, 202)

        def _library(self):
            out = []
            for sid in sorted(os.listdir(media_root)) if os.path.isdir(media_root) else []:
                d = self._song_dir(sid)
                if not d or not os.path.isfile(os.path.join(d, "chops.json")):
                    continue
                meta = read_json(os.path.join(d, "meta.json"))
                ch = read_json(os.path.join(d, "chops.json"))
                try:  # W3: key, when the song has been analysed for it
                    key = json.load(open(os.path.join(d, "analysis.json"))).get("key")
                except (OSError, ValueError):
                    key = None
                out.append({"key": key, "id": sid, "title": meta.get("title"), "duration_s": meta.get("duration_s"),
                            "bpm": ch.get("bpm"), "meter": f"{ch.get('beats_per_bar')}/4", "chops": len(ch["chops"]),
                            "banks": len(ch["banks"]), "added": os.path.getmtime(os.path.join(d, "meta.json"))})
            return sorted(out, key=lambda s: -s["added"])

    srv = ChopPadServer((host, port), Handler, hub)

    def announce_daw(key):
        """A bridge opened or closed for `key` (a mega bank id; a song id from a song-keyed manager): tell every tab on it
        (`daw` event {open, daw, bank})."""
        try:
            doc = M.read(media_root, key)
            hub.publish(key, "daw", daw_for(key, doc.get("source")))
        except KeyError:
            for mid in M.megas_with_source(media_root, key):
                hub.publish(mid, "daw", daw_for(mid, key))
    srv.announce_daw = announce_daw
    return srv
