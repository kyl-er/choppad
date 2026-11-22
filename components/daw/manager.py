"""DawManager: the server-side owner of the DAW bridges and the virtual MIDI port (contracts §11).

    POST /api/songs/<id>/daw {daw: "hydrogen"|"ardour", command: "open"|"sync"|"play"|"stop", bank?}
is `DawManager.command(song_id, media_dir, body)`. Bridges are created on first use and live until `close()` (server
shutdown). `on_transport` mirrors agent-driven pad hits and play/stop to every open bridge and to the MIDI port.
"""
import json
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import daw_project  # noqa: E402
from ardour import ArdourBridge  # noqa: E402
from hydrogen import HydrogenBridge, state_root  # noqa: E402

DAWS = ("hydrogen", "ardour")
COMMANDS = ("open", "sync", "play", "stop")
NOTE_LENGTH_S = 0.15  # mirrored hits are one-shots: note-off this long after note-on


class DawError(Exception):
    """A request problem the HTTP layer should answer with `http_status` (400 bad input, 501 not installed ...)."""

    def __init__(self, message, http_status=400):
        super().__init__(message)
        self.http_status = http_status


class DawManager:
    def __init__(self, state_dir=None, *, bridges=None, midi_factory=None, project_builder=None, on_change=None):
        self.state_dir = state_dir or state_root()
        self._factories = bridges or {"hydrogen": lambda: HydrogenBridge(self.state_dir),
                                      "ardour": lambda: ArdourBridge(self.state_dir)}
        self._midi_factory = midi_factory
        self._build = project_builder or daw_project.build_project
        self.bridges = {}    # daw name -> bridge (once created)
        self.open = {}       # daw name -> song id currently loaded in it
        self.projects = {}   # daw name -> DawProject last pushed
        self.midi = None
        self.midi_error = None
        self.on_change = on_change   # fn(song_id): called after a bridge opened or closed for that song (server -> `daw` event)
        self._lock = threading.RLock()

    # ---- state the browser needs (GET /api/songs/<id> `daw`, the `daw` event) ----
    def state_for(self, sid):
        """{open, daw, bank}: is a bridge holding this song, which one, and which bank its kit holds. No lock: a plain
        read, so the transport path never waits behind a slow `open`."""
        for daw, held in list(self.open.items()):
            project = self.projects.get(daw)
            if held == sid and project is not None:
                return {"open": True, "daw": daw, "bank": project.bank}
        return {"open": False, "daw": None, "bank": None}

    def _changed(self, *sids):
        if self.on_change:
            for sid in dict.fromkeys(sids):
                try:
                    self.on_change(sid)
                except Exception:  # noqa: BLE001 - a broken listener must not break the DAW command
                    pass

    # ---- helpers ----
    def _bridge(self, daw):
        if daw not in self._factories:
            raise DawError(f"daw must be one of {', '.join(DAWS)}")
        if daw not in self.bridges:
            b = self._factories[daw]()
            if not b.available():
                raise DawError(f"{daw} is not installed on this machine", 501)
            self.bridges[daw] = b
        return self.bridges[daw]

    def _port(self):
        if self.midi is None and self.midi_error is None:
            try:
                if self._midi_factory:
                    self.midi = self._midi_factory()
                else:
                    from midi_port import open_port
                    self.midi = open_port("ChopPad")
            except Exception as e:  # noqa: BLE001 - MIDI is an extra; the DAW bridge still works without it
                self.midi_error = f"{type(e).__name__}: {e}"
        return self.midi

    def _midi_info(self):
        if self.midi is not None:
            return {"port": self.midi.name, "address": getattr(self.midi.backend, "address", None)}
        return {"port": None, "error": self.midi_error}

    def _project(self, media_dir, body):
        out_root = os.path.join(self.state_dir, "kits")
        # player settings that are not saved with the project: swing, quant, choke. The request wins; else what the
        # server knows of the player's view (`view_settings`, filled in by the HTTP layer).
        view = body.get("view_settings") if isinstance(body.get("view_settings"), dict) else {}
        extra = {k: body.get(k, view.get(k)) for k in ("swing", "quant", "choke") if body.get(k, view.get(k)) is not None}
        try:
            return self._build(media_dir, out_root, bank=body.get("bank"), **extra)
        except FileNotFoundError as e:
            raise DawError(f"song is missing {os.path.basename(str(e.filename))}; import it again", 409)

    # ---- the endpoint ----
    def command(self, sid, media_dir, body):
        if not isinstance(body, dict):
            raise DawError("body must be {daw, command}")
        daw, cmd = body.get("daw"), body.get("command")
        if daw not in DAWS:
            raise DawError(f"daw must be one of {', '.join(DAWS)}")
        if cmd not in COMMANDS:
            raise DawError(f"command must be one of {', '.join(COMMANDS)}")
        with self._lock:
            bridge = self._bridge(daw)
            out = {"ok": True, "daw": daw, "command": cmd, "song": sid}
            if cmd in ("open", "sync"):
                project = self._project(media_dir, body)
                before = self.open.get(daw)
                try:
                    if cmd == "open" or daw not in self.open:
                        out["detail"] = bridge.launch(project)
                    else:
                        out["detail"] = bridge.push(project)
                except NotImplementedError as e:
                    out.update(ok=False, not_implemented=str(e))
                    self.open[daw], self.projects[daw] = sid, project
                    self._port()
                    out["midi"] = self._midi_info()
                    self._changed(sid, before)
                    return out
                except RuntimeError as e:
                    raise DawError(str(e), 502)
                self.open[daw], self.projects[daw] = sid, project
                self._port()
                out["midi"] = self._midi_info()
                out.update(bpm=project.bpm, bank=project.bank, kit=project.kit_name)
                self._changed(sid, before)
                return out
            if daw not in self.open:
                raise DawError(f"{daw} is not open; run the open command first", 409)
            project = self.projects[daw]
            out.update(self._transport(daw, cmd, project))
            return out

    def _transport(self, daw, cmd, project):
        """play/stop: the DAW's own transport (may be unavailable) and, when a MIDI port exists, MIDI start/clock/stop."""
        out = {}
        try:
            self.bridges[daw].transport(cmd, bpm=project.bpm)
            out["daw_transport"] = "ok"
        except NotImplementedError as e:
            out["daw_transport"] = f"not implemented: {e}"
        except RuntimeError as e:
            raise DawError(str(e), 502)
        port = self._port()
        if port is not None:
            if cmd == "play":
                port.start(project.bpm)
            else:
                port.stop()
            out["midi"] = "start+clock" if cmd == "play" else "stop"
        return out

    # ---- server hook: agent-driven transport ----
    def on_transport(self, sid, msg):
        """Called by the server after it publishes a `transport` event (agent-driven), or directly for a browser's own
        action (`msg["local"]`: a pad hit or pattern play/stop made in a tab). Mirrors to open bridges holding this song.
        A local hit never waits for the lock: while an `open` is in progress it is dropped rather than queued."""
        local = bool(msg.get("local"))
        if not self._lock.acquire(blocking=not local):
            return
        try:
            for daw, held in list(self.open.items()):
                if held != sid:
                    continue
                project = self.projects.get(daw)
                cmd = msg.get("command")
                if cmd == "hit" and project is not None:
                    if "bank" in msg and msg["bank"] != project.bank:
                        continue
                    pad, vel = int(msg["pad"]), float(msg.get("velocity", 1.0))
                    note = getattr(self.bridges[daw], "note", None)
                    if note:
                        note(pad, vel)
                    port = self.midi
                    if port is not None:
                        port.note_on(36 + pad, vel)
                        t = threading.Timer(NOTE_LENGTH_S, port.note_off, args=(36 + pad,))
                        t.daemon = True
                        t.start()
                elif cmd in ("play-pattern", "play-song") and project is not None:
                    self._transport(daw, "play", project)
                elif cmd == "stop" and project is not None:
                    self._transport(daw, "stop", project)
        except Exception:  # noqa: BLE001 - a DAW problem must never break the browser transport
            pass
        finally:
            self._lock.release()

    def status(self):
        with self._lock:
            return {"open": dict(self.open), "midi": self._midi_info()}

    def close(self):
        with self._lock:
            held = list(self.open.values())
            for b in self.bridges.values():
                try:
                    b.close()
                except Exception:  # noqa: BLE001
                    pass
            if self.midi is not None:
                try:
                    self.midi.close()
                except Exception:  # noqa: BLE001
                    pass
            self.bridges, self.open, self.projects, self.midi = {}, {}, {}, None
        self._changed(*held)
