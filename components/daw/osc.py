"""Minimal OSC 1.0 client: encode messages (int32, float32, string) and send them over UDP. Stdlib only.
Hydrogen's OSC server (liblo) takes plain UDP datagrams; no bundles or replies are needed here."""
import socket
import struct


def _pad(b: bytes) -> bytes:
    return b + b"\x00" * (4 - len(b) % 4)  # always at least one NUL, then pad to a multiple of 4


def encode(address: str, *args) -> bytes:
    """OSC message bytes. Ints become int32 (`i`), floats float32 (`f`), strings `s`; bool is rejected (ambiguous)."""
    if not address.startswith("/"):
        raise ValueError(f"OSC address must start with '/': {address!r}")
    tags, data = ",", b""
    for a in args:
        if isinstance(a, bool):
            raise TypeError("OSC args must be int, float or str, not bool")
        if isinstance(a, int):
            tags, data = tags + "i", data + struct.pack(">i", a)
        elif isinstance(a, float):
            tags, data = tags + "f", data + struct.pack(">f", a)
        elif isinstance(a, str):
            tags, data = tags + "s", data + _pad(a.encode("utf-8"))
        else:
            raise TypeError(f"unsupported OSC arg {a!r}")
    return _pad(address.encode("utf-8")) + _pad(tags.encode("ascii")) + data


def decode(packet: bytes):
    """Inverse of encode (tests and the fake server): returns (address, [args])."""
    def cstr(off):
        end = packet.index(b"\x00", off)
        return packet[off:end].decode("utf-8"), (end + 4) & ~3

    addr, off = cstr(0)
    tags, off = cstr(off)
    out = []
    for t in tags[1:]:
        if t == "i":
            out.append(struct.unpack_from(">i", packet, off)[0])
            off += 4
        elif t == "f":
            out.append(struct.unpack_from(">f", packet, off)[0])
            off += 4
        elif t == "s":
            s, off = cstr(off)
            out.append(s)
        else:
            raise ValueError(f"unsupported OSC type tag {t!r}")
    return addr, out


class OscClient:
    """Fire-and-forget UDP sender. `send(address, *args)`; `sock` is injectable for tests."""

    def __init__(self, host="127.0.0.1", port=9000, sock=None):
        self.addr = (host, port)
        self.sock = sock or socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def send(self, address, *args):
        self.sock.sendto(encode(address, *args), self.addr)

    def close(self):
        self.sock.close()
