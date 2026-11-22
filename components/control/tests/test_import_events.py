"""CHOP-11 task 4: import progress as `job` events on the library stream (GET /api/events), and the client, CLI and MCP tool
that wait on it and return the new song id. A fake pipeline stands in for ingest/rhythm/chop; nothing is downloaded."""
import http.client, json, os, shutil, sys, threading, time, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from support import CHOPS, jwrite, make_song_root, start_server  # noqa: E402
import choppad_client as cc  # noqa: E402
import choppad_mcp as mcp  # noqa: E402
import choppad  # noqa: E402

STEPS = ["download + transcode", "tempo + beats", "chopping"]


def fake_pipeline(delay=0.05, fail=False):
    def run(url, media_root, on_stage=None):
        for i, name in enumerate(STEPS):
            on_stage(name, 0.05 + 0.3 * i)
            time.sleep(delay)
        if fail:
            raise RuntimeError("yt-dlp: video unavailable")
        d = os.path.join(media_root, "newsong")
        os.makedirs(d, exist_ok=True)
        jwrite(os.path.join(d, "meta.json"), {"id": "newsong", "title": "New", "duration_s": 5.0})
        jwrite(os.path.join(d, "chops.json"), CHOPS)
        jwrite(os.path.join(d, "peaks.json"), {"duration_s": 5.0, "peaks": [0.5] * 10})   # compute_peaks finds it cached
        return d
    return run


def read_events(port, path, until, timeout=5.0):
    """(event, data) pairs from an SSE path until `until(events)` is true."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    conn.request("GET", path)
    r = conn.getresponse()
    out, ev, data = [], None, []
    end = time.time() + timeout
    try:
        while time.time() < end:
            line = r.readline().decode().rstrip("\r\n")
            if line.startswith("event:"):
                ev = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].strip())
            elif line == "" and ev:
                out.append((ev, json.loads("".join(data))))
                ev, data = None, []
                if until(out):
                    break
    finally:
        conn.close()
    return out


class Base(unittest.TestCase):
    def serve(self, **kw):
        self.root = make_song_root()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.srv, self.port = start_server(self.root, pipeline=fake_pipeline(**kw))
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        self.url = f"http://127.0.0.1:{self.port}"
        return cc.Client(self.url)


class LibraryStream(Base):
    def test_job_events_in_order_with_the_song_id_at_the_end(self):
        c = self.serve(delay=0.15)
        got = []
        t = threading.Thread(target=lambda: got.extend(read_events(
            self.port, "/api/events", lambda ev: any(d.get("state") in ("done", "error") for _, d in ev))))
        t.start()
        time.sleep(0.2)                                                  # the stream is open before the import starts
        job = c.post("/api/songs", {"url": "https://example.com/x"})["job"]
        t.join(10)
        evs = [d for n, d in got if n == "job" and d["job"] == job]
        self.assertEqual({n for n, _ in got}, {"job"})
        self.assertEqual(evs[0]["state"], "queued")
        steps = [e["step"] for e in evs]
        for a, b in zip(STEPS, STEPS[1:]):
            self.assertLess(steps.index(a), steps.index(b))
        self.assertIn("waveform", steps)
        self.assertEqual(set(evs[-1]), {"job", "state", "step", "progress", "song"})
        self.assertEqual((evs[-1]["state"], evs[-1]["step"], evs[-1]["song"], evs[-1]["progress"]), ("done", "done", "newsong", 1.0))
        progress = [e["progress"] for e in evs]
        self.assertEqual(progress, sorted(progress))
        self.assertTrue(all("song" not in e for e in evs[:-1]))

    def test_a_late_stream_is_told_the_recent_jobs_first(self):
        c = self.serve(delay=0.01)
        job = c.post("/api/songs", {"url": "https://example.com/x"})["job"]
        for _ in range(100):
            if c.get(f"/api/jobs/{job}")["state"] == "done":
                break
            time.sleep(0.05)
        got = read_events(self.port, "/api/events", lambda ev: len(ev) >= 1, timeout=3)
        self.assertEqual((got[0][0], got[0][1]["job"], got[0][1]["state"], got[0][1]["song"]), ("job", job, "done", "newsong"))

    def test_error_event(self):
        c = self.serve(delay=0.01, fail=True)
        job = c.post("/api/songs", {"url": "https://example.com/x"})["job"]
        got = read_events(self.port, "/api/events", lambda ev: any(d["state"] == "error" for _, d in ev), timeout=5)
        err = [d for _, d in got if d["state"] == "error"][0]
        self.assertEqual((err["job"], err["step"]), (job, "error"))
        self.assertIn("video unavailable", err["error"])

    def test_song_stream_is_unaffected(self):
        c = self.serve(delay=0.01)
        got = read_events(self.port, "/api/songs/song1/events", lambda ev: len(ev) >= 1, timeout=3)
        self.assertEqual(got[0][0], "daw")        # not job events


class WaitingClients(Base):
    def test_client_waits_reports_each_change_once_and_returns_the_song_id(self):
        c = self.serve(delay=0.1)
        seen = []
        r = c.import_source("https://example.com/x", wait_s=20, progress=seen.append)
        self.assertEqual(r["id"], "newsong")
        self.assertEqual(r["steps"][:3], STEPS)
        self.assertEqual(r["steps"][-1], "done")
        keys = [(e["state"], e["step"], round(e["progress"], 2)) for e in seen]
        self.assertEqual(len(keys), len(set(keys)))           # no repeated lines
        self.assertEqual(seen[-1]["song"], "newsong")

    def test_client_falls_back_to_polling_when_there_is_no_stream(self):
        c = self.serve(delay=0.05)

        def no_stream(*a, **k):
            raise OSError("no stream")
        c._follow_stream = no_stream
        self.assertEqual(c.import_source("https://example.com/x", wait_s=20)["id"], "newsong")

    def test_failure_and_timeout_are_errors_with_the_reason(self):
        c = self.serve(delay=0.01, fail=True)
        with self.assertRaisesRegex(cc.ControlError, "video unavailable"):
            c.import_source("https://example.com/x", wait_s=20)
        c2 = self.serve(delay=0.5)
        with self.assertRaisesRegex(cc.ControlError, "still running"):
            c2.import_source("https://example.com/x", wait_s=0.2)

    def test_cli_prints_progress_on_stderr_and_the_song_id_on_stdout(self):
        self.serve(delay=0.05)
        out, err = [], []
        rc = choppad.run_verb(["import", "https://example.com/x", "--server", self.url, "--wait", "20"], out=out.append, err=err.append)
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(out[-1])["id"], "newsong")
        self.assertTrue(any("tempo + beats" in l for l in err), err)
        self.assertEqual(len(err), len(set(err)))

    def test_mcp_tool_sends_progress_notifications_and_returns_the_id(self):
        c = self.serve(delay=0.05)
        sent = []
        s = mcp.McpServer(c, notify=sent.append)
        resp = s.handle({"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {
            "name": "import_song", "arguments": {"source": "https://example.com/x"}, "_meta": {"progressToken": "tok1"}}})
        res = resp["result"]
        self.assertFalse(res["isError"], res)
        body = json.loads(res["content"][0]["text"])
        self.assertEqual(body["id"], "newsong")
        self.assertEqual(body["steps"][:3], STEPS)
        self.assertTrue(sent)
        self.assertTrue(all(m["method"] == "notifications/progress" and m["params"]["progressToken"] == "tok1" for m in sent))
        self.assertEqual(sent[-1]["params"]["progress"], 1.0)
        self.assertTrue(any("tempo + beats" in m["params"]["message"] for m in sent))
        # without a progress token nothing is sent
        sent.clear()
        s.handle({"jsonrpc": "2.0", "id": 8, "method": "tools/call", "params": {
            "name": "import_song", "arguments": {"source": "https://example.com/y"}}})
        self.assertEqual(sent, [])


if __name__ == "__main__":
    unittest.main()
