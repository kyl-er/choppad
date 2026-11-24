"""An import that fails for a reason the person can act on shows that sentence, not a Python exception name."""
import os, sys, unittest
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from _common import *  # noqa: E402,F403

import time  # noqa: E402
import server  # noqa: E402
from choppad_ingest import ImportProblem  # noqa: E402


def run_job(pipeline):
    jobs = server.Jobs()
    jid = jobs.start("/nonexistent", "https://example.invalid/x", pipeline)
    for _ in range(200):
        j = jobs.get(jid) if hasattr(jobs, "get") else jobs.jobs[jid]
        if j["state"] in ("done", "error"):
            return j
        time.sleep(0.01)
    raise AssertionError("job did not finish")


class JobErrors(unittest.TestCase):
    def test_a_problem_the_person_can_fix_is_shown_as_its_sentence(self):
        def pipeline(url, root, on_stage=None):
            raise ImportProblem('"Long Song" is 7:45 long; ChopPad imports up to 7:00. (abc)')
        j = run_job(pipeline)
        self.assertEqual(j["state"], "error")
        self.assertEqual(j["error"], '"Long Song" is 7:45 long; ChopPad imports up to 7:00. (abc)')

    def test_an_unexpected_failure_keeps_its_exception_name(self):
        def pipeline(url, root, on_stage=None):
            raise RuntimeError("yt-dlp metadata failed: boom")
        self.assertEqual(run_job(pipeline)["error"], "RuntimeError: yt-dlp metadata failed: boom")


if __name__ == "__main__":
    unittest.main()
