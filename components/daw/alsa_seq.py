"""Virtual ALSA-sequencer MIDI output port through ctypes on libasound.so.2: no compiler, no wheel, no pip dependency.

The port shows up in `aconnect -l`, in PipeWire's MIDI bridge (`pw-link`), and to every ALSA-seq client (Hydrogen,
Ardour, Chrome's Web MIDI). `AlsaSeqOut.send(bytes)` takes one raw MIDI message; `snd_midi_event_encode` turns it into a
sequencer event, so notes, clock (0xF8), start/continue/stop (0xFA/0xFB/0xFC) and sysex all work the same way.
"""
import ctypes
import ctypes.util

SND_SEQ_OPEN_OUTPUT = 1
SND_SEQ_PORT_CAP_READ = 1 << 0
SND_SEQ_PORT_CAP_SUBS_READ = 1 << 5
SND_SEQ_PORT_TYPE_MIDI_GENERIC = 1 << 1
SND_SEQ_PORT_TYPE_SOFTWARE = 1 << 4
SND_SEQ_PORT_TYPE_APPLICATION = 1 << 20
SND_SEQ_ADDRESS_SUBSCRIBERS = 254
SND_SEQ_ADDRESS_UNKNOWN = 253
SND_SEQ_QUEUE_DIRECT = 253


class AlsaError(RuntimeError):
    pass


class _Addr(ctypes.Structure):
    _fields_ = [("client", ctypes.c_ubyte), ("port", ctypes.c_ubyte)]


class _Event(ctypes.Structure):
    """snd_seq_event_t (28 bytes): type, flags, tag, queue, time[8], source, dest, data[12]."""
    _fields_ = [("type", ctypes.c_ubyte), ("flags", ctypes.c_ubyte), ("tag", ctypes.c_ubyte), ("queue", ctypes.c_ubyte),
                ("time", ctypes.c_ubyte * 8), ("source", _Addr), ("dest", _Addr), ("data", ctypes.c_ubyte * 12)]


def _lib():
    for name in ("libasound.so.2", ctypes.util.find_library("asound")):
        if name:
            try:
                return ctypes.CDLL(name)
            except OSError:
                continue
    raise AlsaError("libasound.so.2 not found (install alsa-lib)")


class AlsaSeqOut:
    """One ALSA sequencer client with one readable port; subscribers (aconnect, DAWs) receive what `send` writes."""

    def __init__(self, client_name="ChopPad", port_name="ChopPad", lib=None):
        self.lib = lib or _lib()
        L = self.lib
        L.snd_seq_open.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_char_p, ctypes.c_int, ctypes.c_int]
        L.snd_seq_set_client_name.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        L.snd_seq_create_simple_port.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint, ctypes.c_uint]
        L.snd_seq_client_id.argtypes = [ctypes.c_void_p]
        L.snd_seq_event_output_direct.argtypes = [ctypes.c_void_p, ctypes.POINTER(_Event)]
        L.snd_midi_event_new.argtypes = [ctypes.c_size_t, ctypes.POINTER(ctypes.c_void_p)]
        L.snd_midi_event_encode.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.POINTER(_Event)]
        L.snd_midi_event_encode.restype = ctypes.c_long
        L.snd_midi_event_no_status.argtypes = [ctypes.c_void_p, ctypes.c_int]
        L.snd_midi_event_free.argtypes = [ctypes.c_void_p]
        L.snd_seq_close.argtypes = [ctypes.c_void_p]
        self.seq = ctypes.c_void_p()
        self._check(L.snd_seq_open(ctypes.byref(self.seq), b"default", SND_SEQ_OPEN_OUTPUT, 0), "snd_seq_open")
        self._check(L.snd_seq_set_client_name(self.seq, client_name.encode()), "set_client_name")
        self.port = L.snd_seq_create_simple_port(
            self.seq, port_name.encode(), SND_SEQ_PORT_CAP_READ | SND_SEQ_PORT_CAP_SUBS_READ,
            SND_SEQ_PORT_TYPE_MIDI_GENERIC | SND_SEQ_PORT_TYPE_SOFTWARE | SND_SEQ_PORT_TYPE_APPLICATION)
        self._check(self.port, "create_simple_port")
        self.client_id = L.snd_seq_client_id(self.seq)
        self.parser = ctypes.c_void_p()
        self._check(L.snd_midi_event_new(256, ctypes.byref(self.parser)), "midi_event_new")
        L.snd_midi_event_no_status(self.parser, 1)  # always write full messages
        self.name, self.closed = f"{client_name}:{port_name}", False

    def _check(self, rc, what):
        if rc < 0:
            raise AlsaError(f"{what} failed ({rc})")

    @property
    def address(self):
        return f"{self.client_id}:{self.port}"

    def send(self, msg: bytes):
        if self.closed:
            return
        raw = bytes(msg)
        ev = _Event()
        n = self.lib.snd_midi_event_encode(self.parser, raw, len(raw), ctypes.byref(ev))
        if n < 0:
            raise AlsaError(f"snd_midi_event_encode failed ({n})")
        if n == 0:
            return  # incomplete message: nothing to send
        ev.source.port = self.port
        ev.dest.client = SND_SEQ_ADDRESS_SUBSCRIBERS
        ev.dest.port = SND_SEQ_ADDRESS_UNKNOWN
        ev.queue = SND_SEQ_QUEUE_DIRECT
        self._check(self.lib.snd_seq_event_output_direct(self.seq, ctypes.byref(ev)), "event_output_direct")

    def close(self):
        if not self.closed:
            self.closed = True
            self.lib.snd_midi_event_free(self.parser)
            self.lib.snd_seq_close(self.seq)
