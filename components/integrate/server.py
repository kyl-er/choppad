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
  GET  /api/songs/<id>/events    server-sent events: actions | chops | transport (contracts §10)
  POST /api/songs/<id>/transport {command: play-pattern|stop|play-song|hit, pad?, velocity?, bank?, from?} -> events
  POST /api/songs/<id>/daw       {daw: hydrogen|ardour, command: open|sync|play|stop, bank?} -> DAW bridge (contracts §11)
Writes are atomic (temp file + rename). Mutating endpoints read X-ChopPad-Origin and echo it in the event they cause.
"""
import hashlib
import json
import os
import queue
import re
import shutil
import sys
import tempfile
import threading
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import kits  # W3: /api/kits (kits.py next to this file)

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "components", "control"))
import choppad_actions as A  # noqa: E402  (server-side twin of components/ui/actions.js)
UI_DIR = os.path.join(REPO, "components", "ui")
MIDI_JS = os.path.join(REPO, "components", "midi", "midi.js")
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
    json.dump(doc, open(path, "w"))
    return doc


def load_project(media_dir: str) -> dict:
    path = os.path.join(media_dir, "project.json")
    if os.path.isfile(path):
        return read_json(path)
    return {"pads": [{} for _ in range(16)], "overrides": {}}


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


TRANSPORT_COMMANDS = ("play-pattern", "stop", "play-song", "hit")
KEEPALIVE_S = 10.0


class Client:
    """One open event stream."""
    def __init__(self, origin: str = ""):
        self.origin = origin
        self.q = queue.Queue(maxsize=1000)


class Hub:
    """Fan-out of server-sent events, per song. publish() never blocks: a client whose queue is full is dropped."""
    def __init__(self):
        self.lock = threading.Lock()
        self.subs = {}  # song id -> set[Client]
        self.closed = False

    def subscribe(self, sid, origin=""):
        c = Client(origin)
        with self.lock:
            if self.closed:
                c.q.put(None)
            self.subs.setdefault(sid, set()).add(c)
        return c

    def unsubscribe(self, sid, c):
        with self.lock:
            self.subs.get(sid, set()).discard(c)

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


class Jobs:
    def __init__(self):
        self.lock = threading.Lock()
        self.jobs = {}

    def get(self, jid):
        with self.lock:
            return dict(self.jobs.get(jid) or {})

    def update(self, jid, **kw):
        with self.lock:
            self.jobs[jid].update(kw)

    def start(self, media_root, url, pipeline):
        jid = uuid.uuid4().hex[:12]
        with self.lock:
            self.jobs[jid] = {"job": jid, "state": "queued", "stage": "queued", "progress": 0.0, "url": url}
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
            self.update(jid, state="error", stage="error", error=f"{type(e).__name__}: {e}")


def make_server(media_root: str, port: int = 8960, *, pipeline=None, regrid=None, reanalyze=None, actions=None,
                host: str = "127.0.0.1", on_transport=None):
    actions = actions or {}  # name -> fn(media_dir, body) -> json-able; POST /api/songs/<id>/<name>
    media_root = os.path.abspath(media_root)
    jobs = Jobs()
    hub = Hub()
    view_state = {}  # song id -> {bank, selected, settings, sections}: what the player looks at (never saved)
    locks, locks_guard = {}, threading.Lock()

    def song_lock(sid):
        with locks_guard:
            return locks.setdefault(sid, threading.Lock())

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

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n) or b"{}")

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

        def _events(self, sid):
            """Server-sent events: stays open until the client goes away or the server shuts down."""
            origin = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("origin", [""])[0][:64]
            client = hub.subscribe(sid, origin)
            self.close_connection = True
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "close")
                self.send_header("X-Accel-Buffering", "no")
                self.end_headers()
                self.wfile.write(b"retry: 1500\n: connected\n\n")
                self.wfile.flush()
                while True:
                    try:
                        msg = client.q.get(timeout=KEEPALIVE_S)
                    except queue.Empty:
                        msg = ": ping\n\n"  # also how a vanished client is noticed
                    if msg is None:
                        break
                    self.wfile.write(msg.encode())
                    self.wfile.flush()
            except OSError:
                pass  # BrokenPipe, ConnectionReset: the tab closed
            finally:
                hub.unsubscribe(sid, client)

        def _apply_actions(self, sid, d):
            body = self._body()
            acts = body.get("actions") if isinstance(body, dict) else None
            if not isinstance(acts, list) or not all(isinstance(a, dict) for a in acts):
                return self._err(400, "body must be {actions: [action, ...]}")
            with song_lock(sid):
                with open(os.path.join(d, "chops.json")) as f:
                    chops = json.load(f)
                view = view_state.get(sid) or {}
                state = A.initial_state(load_project(d), chops, view.get("sections"))
                state.update({k: view[k] for k in ("bank", "selected", "settings") if k in view})
                before = state["project"]
                taken = []
                for a in acts:
                    if a.get("type") not in A.ACTION_TYPES or a.get("type") == "chops.replace":
                        continue  # unknown; chops change through /regrid
                    nxt = A.reduce(state, a)
                    if nxt is not state or a["type"] in A.VIEW_ACTIONS:
                        taken.append(a)
                    state = nxt
                view_state[sid] = {k: state[k] for k in ("bank", "selected", "settings", "sections")}
                if state["project"] is not before:
                    write_json_atomic(os.path.join(d, "project.json"), A.project_doc(state))
                if taken:
                    hub.publish(sid, "actions", {"origin": self._origin(), "actions": taken})
                listeners = hub.listeners(sid)
            return self._json({"project": A.project_doc(state), "applied": len(taken), "ignored": len(acts) - len(taken),
                               "view": {"bank": state["bank"], "selected": state["selected"]}, "listeners": listeners})

        def _transport(self, sid):
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
            listeners = hub.publish(sid, "transport", msg)
            if on_transport:  # W2: mirror to open DAW bridges (never lets a DAW problem fail the request)
                on_transport(sid, msg)
            return self._json({"ok": True, "listeners": listeners})

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
                self._err(404, "not found")
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_PUT(self):
            try:
                if self.path.split("?", 1)[0].startswith("/api/kits/"):
                    return self._kits("PUT")
                m = re.match(r"^/api/songs/([^/]+)/project$", self.path.split("?", 1)[0])
                d = self._song_dir(m.group(1)) if m else None
                if not d:
                    return self._err(404, "not found")
                proj = self._body()
                if not isinstance(proj.get("pads"), list) or not isinstance(proj.get("overrides"), dict):
                    return self._err(400, "project needs pads[] and overrides{}")
                doc = {"pads": proj["pads"][:16], "overrides": proj["overrides"]}
                if isinstance(proj.get("pattern"), dict):
                    doc["pattern"] = proj["pattern"]
                if isinstance(proj.get("fx"), dict):
                    doc["fx"] = {k: float(v) for k, v in proj["fx"].items() if isinstance(v, (int, float))}
                with song_lock(m.group(1)):
                    old = A.initial_state(load_project(d))["project"]
                    write_json_atomic(os.path.join(d, "project.json"), doc)
                    # other tabs learn about a whole-document save as ordinary §8 actions
                    changes = A.diff_project(old, A.initial_state(doc)["project"])
                    if changes:
                        hub.publish(m.group(1), "actions", {"origin": self._origin(), "actions": changes})
                return self._json({"ok": True})
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
            if p.startswith("/media/"):
                parts = p[len("/media/"):].split("/")
                if len(parts) == 3 and parts[1] == "stretch":  # W3: rendered time-stretched slices
                    if not ID_RE.match(parts[0]) or not re.match(r"^[0-9a-f]{16}\.wav$", parts[2]):
                        return self._err(404, "not found")
                    return self._media(os.path.join(media_root, parts[0], "stretch", parts[2]))
                if len(parts) != 2 or not ID_RE.match(parts[0]) or not re.match(r"^[\w.-]+$", parts[1]):
                    return self._err(404, "not found")
                return self._media(os.path.join(media_root, parts[0], parts[1]))
            if p == "/api/kits" or p.startswith("/api/kits/"):
                return self._kits("GET")
            if p == "/api/songs":
                return self._json(self._library())
            m = re.match(r"^/api/songs/([^/]+)/events$", p)
            if m:
                if not self._song_dir(m.group(1)):
                    return self._err(404, "unknown song")
                if self.command == "HEAD":
                    return self._err(405, "GET only")
                return self._events(m.group(1))
            m = re.match(r"^/api/songs/([^/]+)$", p)
            if m:
                d = self._song_dir(m.group(1))
                if not d:
                    return self._err(404, "unknown song")
                load = lambda n: read_json(os.path.join(d, n))  # noqa: E731
                return self._json({"meta": load("meta.json"), "analysis": load("analysis.json"),
                                   "chops": load("chops.json"), "peaks": compute_peaks(d), "project": load_project(d),
                                   "view": {k: (view_state.get(m.group(1)) or {}).get(k) for k in ("bank", "selected")}})
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
            if p == "/api/upload":
                return self._upload()
            m = re.match(r"^/api/songs/([^/]+)/(actions|transport)$", p)
            if m:
                d = self._song_dir(m.group(1))
                if not d:
                    self.rfile.read(int(self.headers.get("Content-Length") or 0))  # keep the connection in step
                    return self._err(404, "unknown song")
                return self._apply_actions(m.group(1), d) if m.group(2) == "actions" else self._transport(m.group(1))
            m = re.match(r"^/api/songs/([^/]+)/regrid$", p)
            if p == "/api/regrid" or m:
                body = self._body()
                sid = m.group(1) if m else body.get("id")
                d = self._song_dir(sid)
                if not d:
                    return self._err(404, "unknown song")
                try:
                    chops = regrid(d, grid=body.get("grid") or "1/4", meter=body.get("meter"),
                                   beat_offset=int(body.get("beat_offset") or 0), bpm_mult=body.get("bpm_mult") or 1,
                                   **({"mode": body["mode"], "sensitivity": body.get("sensitivity")} if body.get("mode") else {}))
                except (ValueError, KeyError) as e:
                    return self._err(400, str(e))
                hub.publish(sid, "chops", {"origin": self._origin(), "chops": chops})
                return self._json(chops)
            m = re.match(r"^/api/songs/([^/]+)/([a-z]+)$", p)
            if m and m.group(2) in actions:
                d = self._song_dir(m.group(1))
                if not d:
                    return self._err(404, "unknown song")
                try:
                    return self._json(actions[m.group(2)](d, self._body()))
                except ValueError as e:  # bad input for the action (W3: stretch, export of a mixed kit, ...)
                    return self._err(400, str(e))
                except Exception as e:  # noqa: BLE001 - shown in the UI
                    return self._err(getattr(e, "http_status", None) or (502 if m.group(2) == "advise" else 500),
                                     str(e) if hasattr(e, "http_status") else f"{type(e).__name__}: {e}")
            m = re.match(r"^/api/songs/([^/]+)/reanalyze$", p)
            if m and reanalyze:
                d = self._song_dir(m.group(1))
                if not d:
                    return self._err(404, "unknown song")
                body = self._body()
                bpm = body.get("bpm")
                analysis, chops = reanalyze(d, bpm=float(bpm) if bpm else None, meter=body.get("meter"))
                hub.publish(m.group(1), "chops", {"origin": self._origin(), "chops": chops})
                return self._json({"analysis": analysis, "chops": chops})
            return self._err(404, "not found")

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

    return ChopPadServer((host, port), Handler, hub)
