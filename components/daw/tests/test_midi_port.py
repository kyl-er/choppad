"""Virtual MIDI port: messages, clock scheduling on a fake clock, and (when ALSA is present) a real port."""
import os, sys, threading, time, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from support import FakeBackend  # noqa: E402
import midi_port as M  # noqa: E402


def seq_clients():
    with open("/proc/asound/seq/clients") as f:
        return f.read()


class FakeTime:
    """now() and sleep() on a virtual timeline. sleep overshoots by `late` seconds (a loaded machine)."""
    def __init__(self, late=0.0, jitter=()):
        self.t, self.late, self.jitter, self.i = 1000.0, late, list(jitter), 0

    def now(self):
        return self.t

    def sleep(self, dt):
        extra = self.late + (self.jitter[self.i % len(self.jitter)] if self.jitter else 0.0)
        self.i += 1
        self.t += dt + extra


def run_clock(bpm, n_ticks, ft, spin=0.0, on_tick=None):
    """Drive MidiClock._run synchronously on the fake timeline; returns [(tick time, message)]."""
    sent = []
    clk = M.MidiClock(None, clock_fn=ft.now, sleep_fn=ft.sleep, spin_s=spin)

    def send(msg):
        sent.append((ft.now(), msg))
        if on_tick:
            on_tick(clk, len(sent))
        if len(sent) >= n_ticks:
            clk._stop.set()
    clk._send = send
    clk.set_bpm(bpm)
    clk._run()
    return sent, clk


class Messages(unittest.TestCase):
    def setUp(self):
        self.b = FakeBackend()
        self.p = M.Port(self.b)

    def test_velocity_scaling(self):
        self.assertEqual(M.to_velocity(1.0), 127)
        self.assertEqual(M.to_velocity(0.5), 64)
        self.assertEqual(M.to_velocity(0.0), 1)        # a float zero is still a hit
        self.assertEqual(M.to_velocity(100), 100)      # an int is a raw MIDI value
        self.assertEqual(M.to_velocity(500), 127)
        with self.assertRaises(TypeError):
            M.to_velocity(True)

    def test_note_messages(self):
        self.p.note_on(36, 1.0)
        self.p.note_on(51, 0.5, channel=9)
        self.p.note_off(36)
        self.p.all_notes_off()
        self.assertEqual(self.b.msgs, [bytes([0x90, 36, 127]), bytes([0x99, 51, 64]), bytes([0x80, 36, 0]), bytes([0xB0, 123, 0])])

    def test_bad_note(self):
        for bad in (-1, 128, 36.0, "36", None, True):
            with self.subTest(bad), self.assertRaises(ValueError):
                self.p.note_on(bad)

    def test_start_stop_order_and_closed_port_is_silent(self):
        self.p.start()                      # no tempo known: Start only, no clock
        self.assertEqual(self.b.msgs, [bytes([M.START])])
        self.assertFalse(self.p.clock_gen.running)
        self.p.stop()
        self.assertEqual(self.b.msgs[-1], bytes([M.STOP]))
        self.p.close()
        n = len(self.b.msgs)
        self.p.note_on(40)
        self.assertEqual(len(self.b.msgs), n)
        self.assertTrue(self.b.closed)

    def test_start_with_tempo_starts_clock_after_start_byte(self):
        self.p.start(600.0)                 # 600 BPM = 240 ticks a second: a few real ticks arrive quickly
        time.sleep(0.15)
        self.p.stop()
        self.p.close()
        msgs = self.b.msgs
        self.assertEqual(msgs[0], bytes([M.START]))
        self.assertGreater(msgs.count(bytes([M.CLOCK])), 5)
        self.assertEqual(msgs[-1], bytes([M.STOP]))
        self.assertLess(msgs.index(bytes([M.CLOCK])), len(msgs))


class ClockScheduling(unittest.TestCase):
    def test_period(self):
        self.assertAlmostEqual(M.MidiClock.period(120), 60 / (120 * 24))
        with self.assertRaises(ValueError):
            M.MidiClock.period(0)

    def test_ticks_land_on_an_absolute_grid_even_when_sleeps_run_late(self):
        ft = FakeTime(late=0.0007)          # every sleep overshoots by 0.7 ms
        sent, _ = run_clock(101.0, 242, ft)
        period = 60 / (101.0 * 24)
        t0 = sent[0][0]
        # no drift: tick k is within one overshoot of t0 + k*period, even at k = 241
        errs = [t - (t0 + k * period) for k, (t, _) in enumerate(sent)]
        self.assertLess(max(abs(e) for e in errs), 0.0007 + 1e-9)
        self.assertAlmostEqual(sent[-1][0] - t0, 241 * period, delta=0.0008)  # 10 beats worth
        self.assertTrue(all(m == bytes([M.CLOCK]) for _, m in sent))

    def test_random_jitter_does_not_accumulate(self):
        ft = FakeTime(jitter=[0.0, 0.002, 0.0005, 0.0013, 0.0001])
        sent, clk = run_clock(136.0, 400, ft)
        period = 60 / (136.0 * 24)
        t0 = sent[0][0]
        worst = max(abs(t - (t0 + k * period)) for k, (t, _) in enumerate(sent))
        self.assertLess(worst, 0.0021)

    def test_tempo_change_keeps_phase_and_changes_spacing(self):
        ft = FakeTime()

        def on_tick(clk, n):
            if n == 48:
                clk.set_bpm(60.0)           # half speed from here on
        sent, _ = run_clock(120.0, 96, ft, on_tick=on_tick)
        gaps = [b[0] - a[0] for a, b in zip(sent, sent[1:])]
        self.assertAlmostEqual(gaps[10], 60 / (120 * 24), places=6)
        self.assertAlmostEqual(gaps[60], 60 / (60 * 24), places=6)

    def test_error_recording(self):
        ft = FakeTime(late=0.001)
        clk = M.MidiClock(None, clock_fn=ft.now, sleep_fn=ft.sleep)
        clk.record_errors = True
        n = []

        def send(msg):
            n.append(msg)
            if len(n) >= 5:
                clk._stop.set()
        clk._send = send
        clk.set_bpm(120)
        clk._run()
        self.assertEqual(len(clk.errors), 5)
        self.assertAlmostEqual(clk.errors[0], 0.0, places=9)   # the first tick is the anchor
        self.assertAlmostEqual(clk.errors[3], 0.001, places=6)

    def test_stall_reanchors_instead_of_bursting(self):
        ft = FakeTime()
        sent = []
        clk = M.MidiClock(None, clock_fn=ft.now, sleep_fn=ft.sleep)

        def send(msg):
            sent.append(ft.now())
            if len(sent) == 3:
                ft.t += 30.0                # the process was frozen for 30 s
            if len(sent) >= 6:
                clk._stop.set()
        clk._send = send
        clk.set_bpm(120)
        clk._run()
        gaps = [b - a for a, b in zip(sent, sent[1:])]
        self.assertGreater(gaps[2], 29.0)
        self.assertLess(gaps[3], 0.1)       # not hundreds of catch-up ticks

    def test_set_bpm_validates(self):
        clk = M.MidiClock(lambda m: None)
        with self.assertRaises(ValueError):
            clk.set_bpm(-5)
        with self.assertRaises(ValueError):
            clk.start()                     # no tempo yet


@unittest.skipUnless(os.path.isdir("/proc/asound/seq") and os.path.exists("/usr/lib/libasound.so.2"), "no ALSA sequencer")
class RealAlsaPort(unittest.TestCase):
    def test_virtual_port_appears_and_goes_away(self):
        port = M.open_port("ChopPadUnitTest")
        try:
            clients = seq_clients()
            self.assertIn("ChopPadUnitTest", clients)
            self.assertRegex(port.backend.address, r"^\d+:\d+$")
            port.note_on(36, 0.8)
            port.note_off(36)
            port.start(120.0)
            time.sleep(0.05)
            port.stop()
        finally:
            port.close()
        self.assertNotIn("ChopPadUnitTest", seq_clients())


if __name__ == "__main__":
    unittest.main()
