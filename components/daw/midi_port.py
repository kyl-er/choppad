"""Virtual MIDI output port for ChopPad (contracts §11): notes, MIDI clock (24 pulses per quarter note), start/stop.

    port = open_port("ChopPad")          # a readable ALSA sequencer port; aconnect/pw-link/DAWs see it
    port.note_on(36, 0.8); port.note_off(36)
    port.start()                         # MIDI Start (0xFA) + clock at the last bpm
    port.clock(101.0)                    # (re)start the clock thread at 101 BPM, or change tempo while it runs
    port.stop()                          # MIDI Stop (0xFC) and stop the clock

The backend is injectable (`send(bytes)`, `close()`); the default is the ctypes ALSA one (alsa_seq.py, no pip dependency).
Clock ticks are scheduled against absolute deadlines (`next += period`), never relative sleeps, so a late wake-up does not
shift the following ticks. `clock_fn` / `sleep_fn` are injectable so tests run on a fake clock.
"""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # sibling modules are flat, like the other components

PPQN = 24
CLOCK, START, CONTINUE, STOP = 0xF8, 0xFA, 0xFB, 0xFC
DEFAULT_PORT_NAME = "ChopPad"


def to_velocity(vel):
    """ChopPad velocity -> MIDI 1..127. A float is 0..1 (pad velocity), an int is a raw MIDI value (0..127)."""
    if isinstance(vel, bool):
        raise TypeError("velocity must be int or float")
    if isinstance(vel, int):
        return max(0, min(127, vel))
    return max(1, min(127, int(round(float(vel) * 127))))


class MidiClock:
    """Background thread sending 0xF8 every 60 / (bpm * 24) seconds from absolute deadlines."""

    def __init__(self, send, *, clock_fn=time.perf_counter, sleep_fn=time.sleep, spin_s=0.0):
        self._send, self._now, self._sleep, self._spin = send, clock_fn, sleep_fn, spin_s
        self._lock = threading.Lock()
        self._bpm = None
        self._thread = None
        self._stop = threading.Event()
        self.ticks = 0
        self.errors = []  # (actual - deadline) seconds per tick while `record_errors` is on
        self.record_errors = False

    @property
    def bpm(self):
        return self._bpm

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    @staticmethod
    def period(bpm):
        if not bpm or bpm <= 0:
            raise ValueError("bpm must be > 0")
        return 60.0 / (float(bpm) * PPQN)

    def set_bpm(self, bpm):
        self.period(bpm)
        with self._lock:
            self._bpm = float(bpm)  # the loop reads this for the next deadline: phase is kept, only the spacing changes

    def start(self, bpm=None):
        if bpm is not None:
            self.set_bpm(bpm)
        if self._bpm is None:
            raise ValueError("no bpm set")
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="choppad-midi-clock", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        t = self._thread
        if t and t is not threading.current_thread():
            t.join(timeout=2)
        self._thread = None

    def _run(self):
        deadline = self._now()
        while not self._stop.is_set():
            wait = deadline - self._now() - self._spin
            if wait > 0:
                self._sleep(wait)
            while self._spin and self._now() < deadline and not self._stop.is_set():
                pass  # spin the last fraction: trades a little CPU for less wake-up jitter
            if self._stop.is_set():
                break
            actual = self._now()
            self._send(bytes([CLOCK]))
            self.ticks += 1
            if self.record_errors:
                self.errors.append(actual - deadline)
            with self._lock:
                period = self.period(self._bpm)
            deadline += period
            if deadline < self._now() - 1.0:  # stalled for over a second (suspend, SIGSTOP): re-anchor, don't burst
                deadline = self._now() + period


class Port:
    """One virtual output port. All methods are safe to call from any thread."""

    def __init__(self, backend, *, name=DEFAULT_PORT_NAME, channel=0, clock_fn=time.perf_counter, sleep_fn=time.sleep,
                 spin_s=0.0):
        self.backend, self.name, self.channel = backend, name, channel & 0x0F
        self._send_lock = threading.Lock()
        self.clock_gen = MidiClock(self._send, clock_fn=clock_fn, sleep_fn=sleep_fn, spin_s=spin_s)
        self.closed = False

    def _send(self, msg: bytes):
        if self.closed:
            return
        with self._send_lock:
            self.backend.send(msg)

    @staticmethod
    def _note(n):
        if isinstance(n, bool) or not isinstance(n, int) or not 0 <= n <= 127:
            raise ValueError(f"MIDI note must be an int 0..127, got {n!r}")
        return n

    def note_on(self, note, vel=1.0, channel=None):
        self._send(bytes([0x90 | (self.channel if channel is None else channel & 0x0F), self._note(note), max(1, to_velocity(vel))]))

    def note_off(self, note, channel=None):
        self._send(bytes([0x80 | (self.channel if channel is None else channel & 0x0F), self._note(note), 0]))

    def all_notes_off(self, channel=None):
        self._send(bytes([0xB0 | (self.channel if channel is None else channel & 0x0F), 123, 0]))

    def clock(self, bpm):
        """Start the clock thread at `bpm`, or change its tempo if it is already running. `clock(None)` stops it."""
        if bpm is None:
            self.clock_gen.stop()
        elif self.clock_gen.running:
            self.clock_gen.set_bpm(bpm)
        else:
            self.clock_gen.start(bpm)

    def start(self, bpm=None):
        """MIDI Start, then clock at `bpm` (or the last tempo). Start goes out first so the DAW arms before tick 1."""
        if bpm is not None:
            self.clock_gen.set_bpm(bpm)
        self._send(bytes([START]))
        if self.clock_gen.bpm:
            self.clock_gen.start()

    def stop(self):
        self.clock_gen.stop()
        self._send(bytes([STOP]))

    def close(self):
        if self.closed:
            return
        self.clock_gen.stop()
        self.closed = True
        self.backend.close()


def open_port(name=DEFAULT_PORT_NAME, backend=None, **kw):
    """Open a virtual output port called `name`. `backend` defaults to the ALSA sequencer (ctypes).
    The clock spins the last 2 ms before each tick (about 10% of one core while it runs): measured over 30 s at 101 and
    136 BPM that cut the mean wake error from 0.07-0.19 ms to 0.002-0.005 ms and the worst case from 3.4-14.8 ms to
    0.5-1.8 ms. Set CHOPPAD_CLOCK_SPIN_MS=0 to trade accuracy for CPU."""
    kw.setdefault("spin_s", float(os.environ.get("CHOPPAD_CLOCK_SPIN_MS", "2")) / 1000.0)
    if backend is None:
        from alsa_seq import AlsaSeqOut
        backend = AlsaSeqOut(client_name=name, port_name=name)
    return Port(backend, name=name, **kw)
