"""HydrogenBridge (contracts §11): runs the bridge's OWN Hydrogen instance and drives it over OSC.

Proven against Hydrogen 1.2.7 (docs/daw-bridge-spikes.md):
  * `hydrogen --config <copy> --user-data <dir> -O <port>` starts a second instance that never touches the owner's
    `~/.hydrogen/hydrogen.conf` (every Hydrogen and h2cli run rewrites the config file it is given, so the bridge always
    passes its own copy, even for h2cli).
  * `QT_QPA_PLATFORM=offscreen` runs the full app with no window, no focus steal, OSC and audio still working.
  * OSC: /Hydrogen/OPEN_SONG <abs path>, /BPM <float>, /PLAY, /STOP, /NOTE_ON <note 36+pad> <velocity 0..1>,
    /SELECT_AND_PLAY_PATTERN <0-based index in patternList>, /MASTER_VOLUME_ABSOLUTE <float>.
  * Hydrogen follows MIDI Start/Stop but ignores MIDI clock (and logs an error per tick), so ChopPad leads over OSC
    and the MIDI port is not connected to this instance.
  * The OSC server (liblo) listens on all interfaces: anything on the network can send OSC while the bridge runs.
"""
import os
import shutil
import socket
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import h2song  # noqa: E402
from osc import OscClient  # noqa: E402

DEFAULT_PORT = 9731
PORT_RANGE = 40
DEFAULT_CONF = "/usr/share/hydrogen/data/hydrogen.default.conf"
OWNER_CONF = os.path.expanduser("~/.hydrogen/hydrogen.conf")  # only READ, to inherit the audio setup that works here


def state_root():
    return os.environ.get("CHOPPAD_DAW_DIR") or os.path.join(os.path.expanduser("~/.local/state"), "choppad", "daw")


def udp_port_free(port, host="0.0.0.0"):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def patch_config(src_path, dst_path, *, osc_port):
    """Copy a Hydrogen config with the settings the bridge needs. Never writes `src_path`."""
    tree = ET.parse(src_path)
    root = tree.getroot()

    def setv(path, value):
        el = root.find(path)
        if el is None:
            parent = root
            for part in path.split("/"):
                nxt = parent.find(part)
                parent = nxt if nxt is not None else ET.SubElement(parent, part)
            el = parent
        el.text = value

    setv("restoreLastSong", "false")
    setv("restoreLastPlaylist", "false")
    setv("audio_engine/osc_configuration/oscEnabled", "true")
    setv("audio_engine/osc_configuration/oscServerPort", str(osc_port))
    setv("audio_engine/osc_configuration/oscFeedbackEnabled", "false")  # no state dumps to every client that ever sent
    # a second Hydrogen on JACK transport would start and stop together with the owner's own Hydrogen
    setv("audio_engine/jack_driver/jack_transport_mode", "NO_JACK_TRANSPORT")
    setv("audio_engine/jack_driver/jack_transport_mode_master", "NO_JACK_TIME_MASTER")
    ET.indent(tree, space=" ")
    tmp = dst_path + ".tmp"
    tree.write(tmp, encoding="UTF-8", xml_declaration=True)
    os.replace(tmp, dst_path)


class HydrogenBridge:
    name = "hydrogen"

    def __init__(self, state_dir=None, *, osc_port=None, headless=None, popen=subprocess.Popen, which=shutil.which,
                 osc_factory=OscClient, base_conf=None, volume=None, wait_s=25.0, sleep=time.sleep, clock=time.monotonic,
                 port_free=udp_port_free, env=None):
        self.dir = os.path.join(state_dir or state_root(), "hydrogen")
        self.conf = os.path.join(self.dir, "hydrogen.conf")
        self.userdata = os.path.join(self.dir, "userdata")
        self.songs = os.path.join(self.dir, "songs")
        self.kits = os.path.join(self.userdata, "drumkits")
        self.log = os.path.join(self.dir, "hydrogen.log")
        self.want_port = osc_port or int(os.environ.get("CHOPPAD_HYDROGEN_OSC_PORT") or 0) or None
        env_head = os.environ.get("CHOPPAD_DAW_HEADLESS")
        self.headless = headless if headless is not None else (env_head not in (None, "", "0", "false"))
        env_vol = os.environ.get("CHOPPAD_HYDROGEN_VOLUME")  # master volume 0..1.5 sent after every song load
        self.volume = volume if volume is not None else (float(env_vol) if env_vol else None)
        self.base_conf = base_conf
        self._popen, self._which, self._osc_factory = popen, which, osc_factory
        self._wait_s, self._sleep, self._clock, self._port_free = wait_s, sleep, clock, port_free
        self._env = env if env is not None else os.environ
        self.proc = None
        self.osc = None
        self.port = None
        self.attached = False
        self.song_path = None
        self.playing = False

    # ---- contract ----
    def available(self) -> bool:
        return self._which("hydrogen") is not None

    def launch(self, project):
        """Start (or attach to) the bridge's Hydrogen, then load the project's song. Returns details for the API."""
        if not self.available():
            raise RuntimeError("hydrogen is not installed (pacman -S hydrogen)")
        self._ensure_running()
        return self.push(project)

    def _write_song(self, project):
        """Install the kit under --user-data and write the project's .h2song; returns (path, info). No Hydrogen needed."""
        os.makedirs(self.songs, exist_ok=True)
        os.makedirs(self.kits, exist_ok=True)
        self._install_kit(project)
        path = os.path.join(self.songs, f"{project.song_id}.h2song")
        info = h2song.write_song(path, title=project.title, bpm=project.bpm, beats_per_bar=project.beats_per_bar,
                                 wavs=[os.path.basename(w) if w else None for w in project.wavs()], pattern=project.pattern,
                                 bank=project.bank, kit_name=project.kit_name, empty_wav=self._silence(project),
                                 choke=getattr(project, "choke", True),
                                 gains_db=[c.gain_db if c else 0.0 for c in project.chops],
                                 pitches=[c.pitch if c else 0.0 for c in project.chops],
                                 swing=getattr(project, "swing", 0.0), quant=getattr(project, "quant", "1/16"))
        return path, info

    def render(self, project, out_wav, *, timeout=120):
        """Render the project's song to `out_wav` offline with `h2cli` (no window, no audio device, any tempo)."""
        if not self._which("h2cli"):
            raise RuntimeError("h2cli is not installed (it ships with hydrogen)")
        os.makedirs(self.dir, exist_ok=True)
        if not os.path.isfile(self.conf):  # h2cli rewrites the config it is given: always the bridge's copy
            patch_config(self.base_conf or (OWNER_CONF if os.path.isfile(OWNER_CONF) else DEFAULT_CONF), self.conf,
                         osc_port=self.want_port or DEFAULT_PORT)
        path, _ = self._write_song(project)
        p = subprocess.run(["h2cli", "-s", path, "-o", out_wav, "--config", self.conf, "--user-data", self.userdata,
                            "-L", os.path.join(self.dir, "h2cli.log")], capture_output=True, text=True, timeout=timeout,
                           env=dict(self._env, QT_QPA_PLATFORM="offscreen"))
        if p.returncode != 0 or not os.path.isfile(out_wav):
            raise RuntimeError(f"h2cli failed (exit {p.returncode}): {(p.stdout + p.stderr)[-300:]}")
        return out_wav

    def push(self, project):
        """Write the song for `project` (kit + pattern + tempo) and make Hydrogen open it again."""
        if not self._running():
            self._ensure_running()
        path, info = self._write_song(project)
        was_playing = self.playing
        self.osc.send("/Hydrogen/STOP")
        self.osc.send("/Hydrogen/OPEN_SONG", os.path.abspath(path))
        self.osc.send("/Hydrogen/BPM", float(project.bpm))
        if self.volume is not None:
            self.osc.send("/Hydrogen/MASTER_VOLUME_ABSOLUTE", float(self.volume))
        self.song_path = path
        self.playing = False
        if was_playing:
            self._sleep(0.5)  # let the song finish loading before play resumes
            self.transport("play")
        return {"song": path, "kit": project.kit_name, "osc_port": self.port, "headless": self.headless, **info}

    def transport(self, command, *, bpm=None):
        if command not in ("play", "stop"):
            raise ValueError("command must be play or stop")
        if not self._running():
            raise RuntimeError("Hydrogen is not running; open it first")
        if bpm is not None:
            self.osc.send("/Hydrogen/BPM", float(bpm))
        if command == "play":
            self.osc.send("/Hydrogen/PLAY")
            self.playing = True
        else:
            self.osc.send("/Hydrogen/STOP")
            self.playing = False

    def note(self, pad, velocity=1.0):
        """Trigger pad `pad` (instrument 36+pad) once; used to mirror live hits."""
        if self._running():
            self.osc.send("/Hydrogen/NOTE_ON", 36 + int(pad), max(0.0, min(1.0, float(velocity))))

    def close(self):
        """Stop the instance this bridge started (an attached instance is left running)."""
        proc, self.proc = self.proc, None
        if proc is not None:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=6)
                except Exception:  # noqa: BLE001
                    proc.kill()
                    proc.wait(timeout=3)
        if self.osc is not None:
            try:
                self.osc.close()
            except OSError:
                pass
            self.osc = None
        self._drop_state()
        self.playing = False

    # ---- internals ----
    def _running(self):
        if self.attached:
            return self.osc is not None
        return self.proc is not None and self.proc.poll() is None and self.osc is not None

    def _pidfile(self):
        return os.path.join(self.dir, "state")

    def _drop_state(self):
        try:
            os.unlink(self._pidfile())
        except OSError:
            pass

    def _write_state(self, pid):
        with open(self._pidfile(), "w") as f:
            f.write(f"{pid} {self.port}\n")

    def _attachable(self):
        """(pid, port) of a bridge Hydrogen left running by an earlier process, if it is still ours and alive."""
        try:
            with open(self._pidfile()) as f:
                pid, port = f.read().split()
            pid, port = int(pid), int(port)
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                cmd = f.read().split(b"\0")
        except (OSError, ValueError):
            return None
        return (pid, port) if self.conf.encode() in cmd else None

    def _pick_port(self):
        start = self.want_port or DEFAULT_PORT
        for p in range(start, start + (1 if self.want_port else PORT_RANGE)):
            if self._port_free(p):
                return p
        raise RuntimeError(f"no free UDP port for Hydrogen OSC from {start}")

    def _ensure_running(self):
        if self._running():
            return
        os.makedirs(self.dir, exist_ok=True)
        old = self._attachable()
        if old:
            self.port, self.attached = old[1], True
            self.osc = self._osc_factory("127.0.0.1", self.port)
            return
        self.port = self._pick_port()
        base = self.base_conf or (OWNER_CONF if os.path.isfile(OWNER_CONF) else DEFAULT_CONF)
        patch_config(base, self.conf, osc_port=self.port)
        for sub in (self.userdata, self.songs):
            os.makedirs(sub, exist_ok=True)
        cmd = ["hydrogen", "--child", "-n", "--config", self.conf, "--user-data", self.userdata, "-O", str(self.port),
               "-V", "Info", "-L", self.log]
        env = dict(self._env)
        if self.headless:
            env["QT_QPA_PLATFORM"] = "offscreen"
        if os.path.exists(self.log):
            os.unlink(self.log)
        self.proc = self._popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        self.attached = False
        self.osc = self._osc_factory("127.0.0.1", self.port)
        self._wait_ready()
        self._write_state(self.proc.pid)

    def _wait_ready(self):
        deadline = self._clock() + self._wait_s
        while self._clock() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"Hydrogen exited at start (code {self.proc.returncode}); see {self.log}")
            try:
                with open(self.log, errors="replace") as f:
                    if "Osc server started" in f.read():
                        return
            except OSError:
                pass
            self._sleep(0.1)
        self.close()
        raise RuntimeError(f"Hydrogen did not start its OSC server in {self._wait_s:.0f}s; see {self.log}")

    def _install_kit(self, project):
        """Put the kit folder where `--user-data` looks for kits (so song instruments resolve by drumkit name)."""
        dst = os.path.join(self.kits, project.kit_name)
        if os.path.abspath(project.kit_dir) != os.path.abspath(dst) and not os.path.isfile(os.path.join(dst, "drumkit.xml")):
            shutil.copytree(project.kit_dir, dst + ".part")
            os.replace(dst + ".part", dst)
        # keep only the newest few versions of this song's kit
        prefix = project.kit_name.rsplit(" ", 1)[0] + " "
        mine = sorted((d for d in os.listdir(self.kits) if d.startswith(prefix)),
                      key=lambda d: os.path.getmtime(os.path.join(self.kits, d)), reverse=True)
        for d in mine[3:]:
            if d != project.kit_name:
                shutil.rmtree(os.path.join(self.kits, d), ignore_errors=True)

    def _silence(self, project):
        """A tiny silent WAV (inside the kit folder) for empty pads, so the song always has 16 instruments."""
        import wave
        name = "empty.wav"
        path = os.path.join(self.kits, project.kit_name, name)
        if not os.path.isfile(path):
            with wave.open(path, "wb") as w:
                w.setnchannels(2)
                w.setsampwidth(2)
                w.setframerate(44100)
                w.writeframes(b"\x00\x00" * 2 * 64)
        return name
