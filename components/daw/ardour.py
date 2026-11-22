"""ArdourBridge (contracts §11): builds an Ardour 9 session from a ChopPad bank + pattern, without Ardour's GUI.

What the spikes proved (docs/daw-bridge-spikes.md), and therefore what this does:
  * `ardour9-new_session` + headless `ardour9-lua` (load_session, new_audio_track, Temporal.TempoMap, set_session_extents,
    save_state) create the session, tracks, tempo and meter.
  * Ardour's Lua has no audio-import binding (Editor:do_import is GUI-only) and neither the OSC nor the MCP HTTP surface
    can import audio, so the chops go in by writing the session XML: one <Source> per channel of each chop WAV (external
    file, referenced by absolute path) and one <Region> per pattern hit in the pad's playlist. `ardour9-export` renders
    such a session headless to the right samples at the right times.
  * Ardour's sessions run at the engine rate (48 kHz here), so the chops are resampled to 48 kHz first.
  * Headless `ardour9-lua` does not start control surfaces, and a GUI Ardour starts with a first-run wizard when it has
    no config, so OSC / the MCP HTTP surface / MIDI-clock sync could not be proven without Preferences clicks. Remote
    transport therefore raises NotImplementedError with the manual steps instead of guessing.
All headless Ardour runs use a private XDG_CONFIG_HOME/XDG_CACHE_HOME, so the owner's ~/.config/ardour9 is never touched.
The pattern is placed as audio regions (PRD decision 4: no sampler plugin installed), repeated `loops` times.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import wave
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hydrogen import state_root  # noqa: E402

SUPERCLOCK = 282240000  # Temporal::superclock_ticks_per_second in Ardour 9 (5880 ticks per sample at 48 kHz)
SESSION_RATE = 48000
TRANSPORT_MANUAL_STEPS = (
    "Ardour transport is not wired: control surfaces cannot be switched on without Ardour's GUI. One-time steps: start Ardour, "
    "finish the first-run wizard, Window > Preferences > Control Surfaces, enable 'Open Sound Control (OSC)' (port 3819) "
    "and/or 'MCP HTTP Server (Experimental)' (note: it listens on all interfaces); to follow ChopPad's MIDI clock, set "
    "Window > Preferences > Sync > Transport Masters to MIDI Clock and connect the 'ChopPad' MIDI port to Ardour's "
    "'MIDI Clock in'. None of that is verified by this bridge.")


def _clean(text, limit=40):
    return re.sub(r"[^\w -]+", "", text or "").strip()[:limit].strip() or "Song"


def superclock(seconds):
    return int(round(seconds * SUPERCLOCK))


def resample_wav(src, dst, rate=SESSION_RATE):
    """Copy `src` WAV to `dst` at `rate` (polyphase), keeping channels; returns frame count."""
    import numpy as np
    import soundfile as sf
    from math import gcd
    from scipy.signal import resample_poly

    data, sr = sf.read(src, dtype="float32", always_2d=True)
    if sr != rate:
        g = gcd(int(sr), int(rate))
        data = resample_poly(data, rate // g, int(sr) // g, axis=0).astype("float32")
        data = np.clip(data, -1.0, 1.0)
    sf.write(dst, data, rate, subtype="PCM_16")
    return len(data)


def hit_layout(project, loops):
    """Where every pad's hits land: {pad: [(seconds, gain)]}. An empty pattern lays the 16 chops out back to back."""
    bpm, bpb = float(project.bpm), int(project.beats_per_bar)
    sec_per_beat = 60.0 / bpm
    pat = project.pattern or {}
    bars = float(pat.get("bars") or 2)
    loop_s = bars * bpb * sec_per_beat
    out = {}
    for e in pat.get("events") or []:
        try:
            pad, beat = int(e["pad"]), float(e["beat"])
        except (KeyError, TypeError, ValueError):
            continue
        if int(e.get("bank", project.bank)) != project.bank or not 0 <= pad < 16 or project.chops[pad] is None:
            continue
        if not 0 <= beat < bars * bpb:
            continue
        vel = max(0.05, min(1.0, float(e.get("vel", 1.0))))
        for k in range(loops):
            out.setdefault(pad, []).append((k * loop_s + beat * sec_per_beat, vel))
    return out, loop_s * loops


def inject_audio(session_file, tracks):
    """Add chop sources and regions to a saved session XML.

    tracks: [{name, wav, frames, channels, hits: [(seconds, gain)]}]; every track must exist in the session as an audio
    route. Returns the number of regions written. Timeline values are Ardour 9 superclock strings: `a<ticks>`, and
    lengths as `a<ticks>@a<position>`.
    """
    tree = ET.parse(session_file)
    root = tree.getroot()
    counter = int(root.get("id-counter", "0"))

    def nid():
        nonlocal counter
        counter += 1
        return str(counter)

    sources, regions = root.find("Sources"), root.find("Regions")
    n_regions = 0
    for t in tracks:
        route = next((r for r in root.iter("Route") if r.get("name") == t["name"]), None)
        if route is None or not route.get("audio-playlist"):
            raise RuntimeError(f"track {t['name']!r} is not an audio track in {session_file}")
        playlist = next(p for p in root.iter("Playlist") if p.get("id") == route.get("audio-playlist"))
        length = int(t["frames"]) * (SUPERCLOCK // SESSION_RATE)
        src_ids = []
        for ch in range(int(t["channels"])):
            sid = nid()
            src_ids.append(sid)
            ET.SubElement(sources, "Source", {"name": t["wav"], "type": "audio", "flags": "", "id": sid,
                                               "captured-for": "", "channel": str(ch), "origin": t["wav"], "gain": "1"})
        base = {"muted": "0", "opaque": "1", "locked": "0", "video-locked": "0", "automatic": "0", "import": "0",
                "external": "1", "sync-marked": "0", "left-of-split": "0", "right-of-split": "0", "hidden": "0",
                "position-locked": "0", "valid-transients": "0", "start": "a0", "sync-position": "a0",
                "ancestral-start": "a0", "ancestral-length": f"a{length}@a0", "stretch": "1", "shift": "1",
                "positional-lock-style": "AudioTime", "envelope-active": "0", "default-fade-in": "1",
                "default-fade-out": "1", "fade-in-active": "1", "fade-out-active": "1", "type": "audio",
                "first-edit": "nothing", "channels": str(t["channels"])}
        for i, sid in enumerate(src_ids):
            base[f"source-{i}"], base[f"master-source-{i}"] = sid, sid
        name = os.path.splitext(os.path.basename(t["wav"]))[0]
        ET.SubElement(regions, "Region", {**base, "name": name, "whole-file": "1", "position": "a0",
                                          "length": f"a{length}@a0", "layering-index": "0", "scale-amplitude": "1",
                                          "id": nid()})
        for k, (sec, gain) in enumerate(sorted(t["hits"])):
            pos = superclock(sec)
            ET.SubElement(playlist, "Region", {**base, "name": f"{name}.{k + 1}", "whole-file": "0", "position": f"a{pos}",
                                               "length": f"a{length}@a{pos}", "layering-index": str(k),
                                               "scale-amplitude": repr(round(float(gain), 4)), "id": nid()})
            n_regions += 1
    root.set("id-counter", str(counter + 10))
    ET.indent(tree, space="  ")
    tmp = session_file + ".tmp"
    tree.write(tmp, encoding="UTF-8", xml_declaration=True)
    os.replace(tmp, session_file)
    return n_regions


def lua_setup(track_names, bpm, beats_per_bar):
    """Headless script pass 1: tracks, tempo, meter. Args (via `ardour9-lua script dir name`): session dir and name."""
    names = ", ".join('"%s"' % _clean(n, 60).replace('"', "") for n in track_names)
    return f'''local function p(...) print(...) io.stdout:flush() end
local s = load_session(arg[1], arg[2])
if not s then p("ERROR: load_session failed") os.exit(3) end
for _, n in ipairs({{{names}}}) do
  s:new_audio_track(2, 2, ARDOUR.RouteGroup(), 1, n, ARDOUR.PresentationInfo.max_order, ARDOUR.TrackMode.Normal, true)
end
local tm = Temporal.TempoMap.write_copy()
tm:set_tempo(Temporal.Tempo({float(bpm)!r}, {float(bpm)!r}, 4), Temporal.timepos_t(0))
tm:set_meter(Temporal.Meter({int(beats_per_bar)}, 4), Temporal.timepos_t(0))
Temporal.TempoMap.update(tm)
s:save_state("")
p("setup ok", s:get_tracks():size())
close_session()
'''


def lua_finish(end_seconds):
    """Headless script pass 2: session range (export and the GUI need one), save."""
    return f'''local function p(...) print(...) io.stdout:flush() end
local s = load_session(arg[1], arg[2])
if not s then p("ERROR: load_session failed") os.exit(3) end
local n = 0
for t in s:get_tracks():iter() do n = n + t:to_track():playlist():region_list():size() end
s:set_session_extents(Temporal.timepos_t(0), Temporal.timepos_t(math.floor({float(end_seconds)!r} * s:nominal_sample_rate())))
s:save_state("")
p("finish ok regions", n)
close_session()
'''


class ArdourBridge:
    name = "ardour"

    def __init__(self, state_dir=None, *, loops=4, run=subprocess.run, popen=subprocess.Popen, which=shutil.which,
                 resample=resample_wav, timeout=180):
        self.dir = os.path.join(state_dir or state_root(), "ardour")
        self.xdg = os.path.join(self.dir, "xdg")
        self.loops = loops
        self._run, self._popen, self._which, self._resample, self._timeout = run, popen, which, resample, timeout
        self.proc = None
        self.session = None  # dict from build_session

    def available(self) -> bool:
        return all(self._which(b) for b in ("ardour9", "ardour9-new_session", "ardour9-lua"))

    # ---- headless pieces ----
    def _env(self):
        os.makedirs(os.path.join(self.xdg, "config"), exist_ok=True)
        os.makedirs(os.path.join(self.xdg, "cache"), exist_ok=True)
        env = dict(os.environ)
        env["XDG_CONFIG_HOME"] = os.path.join(self.xdg, "config")
        env["XDG_CACHE_HOME"] = os.path.join(self.xdg, "cache")
        return env

    def _tool(self, argv, what):
        p = self._run(argv, env=self._env(), capture_output=True, text=True, timeout=self._timeout)
        out = (p.stdout or "") + (p.stderr or "")
        # these tools abort at exit even when they worked ("FATAL: exception not rethrown"), so judge by output
        if p.returncode not in (0, 134, -6) or "ERROR: load_session failed" in out:
            raise RuntimeError(f"{what} failed (exit {p.returncode}): {out[-400:]}")
        return out

    def session_key(self, project):
        blob = json.dumps([project.key, project.bpm, project.beats_per_bar, project.pattern, self.loops], sort_keys=True)
        return hashlib.sha1(blob.encode()).hexdigest()[:8]

    def build_session(self, project, loops=None):
        """Create the Ardour session for `project`; returns {dir, name, file, tracks, regions, seconds}."""
        loops = loops or self.loops
        if not self.available():
            raise RuntimeError("Ardour 9 is not installed (need ardour9, ardour9-new_session, ardour9-lua)")
        name = f"{_clean(project.song_id, 24)} B{project.bank + 1} {self.session_key(project)}"
        sdir = os.path.join(self.dir, "sessions", name)
        sfile = os.path.join(sdir, name + ".ardour")
        hits, total_s = hit_layout(project, loops)
        if not hits:  # nothing sequenced yet: lay the bank's chops out back to back so the session is not empty
            t, bpm = 0.0, float(project.bpm)
            for c in project.chops:
                if c is not None:
                    hits.setdefault(c.pad, []).append((t, 1.0))
                    t += 60.0 / bpm  # one beat per chop (grid 1/4); finer grids overlap, which is fine for a sketch
            total_s = max(t, 60.0 / bpm)
        if os.path.isfile(sfile):  # same content: reuse
            return {"dir": sdir, "name": name, "file": sfile, "tracks": len(hits), "regions": sum(map(len, hits.values())),
                    "seconds": total_s, "reused": True}
        os.makedirs(os.path.dirname(sdir), exist_ok=True)
        stage = tempfile.mkdtemp(prefix="choppad-ardour-", dir=os.path.dirname(sdir))
        stage_dir = os.path.join(stage, name)
        try:
            self._tool(["ardour9-new_session", "-s", str(SESSION_RATE), stage_dir, name], "ardour9-new_session")
            if not os.path.isfile(os.path.join(stage_dir, name + ".ardour")):
                raise RuntimeError("ardour9-new_session did not create the session file")
            chop_dir = os.path.join(stage_dir, "chops")
            os.makedirs(chop_dir)
            tracks = []
            for pad in sorted(hits):
                dst = os.path.join(chop_dir, f"chop_{pad:02d}.wav")
                frames = self._resample(project.chops[pad].path, dst)
                tracks.append({"name": f"{pad + 1:02d} Pad", "wav": os.path.join(sdir, "chops", f"chop_{pad:02d}.wav"),
                               "frames": frames, "channels": 2, "hits": hits[pad], "_src": dst})
            script = os.path.join(stage, "setup.lua")
            with open(script, "w") as f:
                f.write(lua_setup([t["name"] for t in tracks], project.bpm, project.beats_per_bar))
            self._tool(["ardour9-lua", script, stage_dir, name], "session setup (ardour9-lua)")
            n = inject_audio(os.path.join(stage_dir, name + ".ardour"), tracks)
            script2 = os.path.join(stage, "finish.lua")
            with open(script2, "w") as f:
                f.write(lua_finish(total_s))
            self._tool(["ardour9-lua", script2, stage_dir, name], "session finish (ardour9-lua)")
            shutil.rmtree(sdir, ignore_errors=True)
            os.replace(stage_dir, sdir)
        finally:
            shutil.rmtree(stage, ignore_errors=True)
        # sources point at the final folder (written that way above); verify nothing references the staging path
        with open(sfile, encoding="utf-8") as f:
            if stage in f.read():
                raise RuntimeError("session still references its staging folder")
        return {"dir": sdir, "name": name, "file": sfile, "tracks": len(tracks), "regions": n, "seconds": total_s,
                "reused": False}

    def render(self, project, out_wav, loops=None):
        """Headless export of the project's session to `out_wav` (16-bit, session rate) with ardour9-export."""
        info = self.build_session(project, loops)
        self._tool(["ardour9-export", "-o", out_wav, info["dir"], info["name"]], "ardour9-export")
        return out_wav

    # ---- contract ----
    def launch(self, project):
        """Build the session and open it in Ardour's GUI (your normal Ardour config; first run shows its wizard)."""
        info = self.build_session(project)
        if self.proc is not None and self.proc.poll() is None:
            raise NotImplementedError("Ardour is already open; it cannot load a new session without a control surface. "
                                      "Close it and run `daw open` again. " + TRANSPORT_MANUAL_STEPS)
        self.proc = self._popen(["ardour9", "-n", "-a", info["file"]], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                start_new_session=True)
        self.session = info
        return {**info, "pid": self.proc.pid, "note": "opened in Ardour's GUI; transport control is not wired "
                "(see ArdourBridge.transport)"}

    def push(self, project):
        info = self.build_session(project)
        if self.proc is not None and self.proc.poll() is None:
            self.session = info
            raise NotImplementedError(f"the updated session is ready at {info['file']} but the open Ardour cannot be told to "
                                      "reload it (no control surface). Close Ardour and run `daw open`, or File > Open it. "
                                      + TRANSPORT_MANUAL_STEPS)
        return self.launch(project)

    def transport(self, command, *, bpm=None):
        raise NotImplementedError(TRANSPORT_MANUAL_STEPS)

    def close(self):
        proc, self.proc = self.proc, None
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=8)
            except Exception:  # noqa: BLE001
                proc.kill()
