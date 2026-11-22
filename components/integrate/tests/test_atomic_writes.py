"""A request may read chops.json, analysis.json or peaks.json while another request rewrites it (a regrid racing a
pad edit). The pipeline's JSON writes must therefore be atomic: a reader sees the old file or the new one, never half."""
import os, sys, tempfile, threading, unittest
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from _common import *  # noqa: E402,F403

import inspect, json, shutil  # noqa: E402
import choppad  # noqa: E402
import server  # noqa: E402


class AtomicWrites(unittest.TestCase):
    def hammer(self, write):
        tmp = tempfile.mkdtemp(prefix="choppad-atomic-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        path = os.path.join(tmp, "chops.json")
        doc = {"chops": [{"index": i, "start": i * 0.5, "end": i * 0.5 + 0.5} for i in range(4000)]}
        write(path, doc)
        bad, stop = [], threading.Event()

        def reader():
            while not stop.is_set():
                try:
                    with open(path) as f:
                        json.load(f)
                except ValueError as e:
                    bad.append(str(e))

        t = threading.Thread(target=reader)
        t.start()
        try:
            for _ in range(150):
                write(path, doc)
        finally:
            stop.set()
            t.join()
        self.assertEqual(bad, [], "a reader saw a half-written file")
        self.assertEqual(os.listdir(tmp), ["chops.json"], "temp files were left behind")

    def test_pipeline_writer_is_atomic(self):
        self.hammer(lambda p, d: choppad._write_json(p, d, indent=2))

    def test_server_writer_is_atomic(self):
        self.hammer(server.write_json_atomic)

    def test_no_direct_json_writes_left_in_the_request_path(self):
        for mod in (choppad, server):
            src = inspect.getsource(mod)
            helpers = src.count("def _write_json") + src.count("def write_json_atomic")
            self.assertEqual(src.count("json.dump("), helpers, f"{mod.__name__}: write JSON files through the atomic helper")


if __name__ == "__main__":
    unittest.main()
