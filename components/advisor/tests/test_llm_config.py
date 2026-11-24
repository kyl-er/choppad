"""The advisor's endpoint, model and key come from the environment. Nothing here touches the network:
the HTTP layer is replaced by a fake urlopen and keys are made-up strings."""
import json, os, sys, tempfile, unittest
from unittest import mock
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from _common import *  # noqa: E402,F403
import choppad_advisor as adv  # noqa: E402

ENV_KEYS = ("CHOPPAD_LLM_URL", "CHOPPAD_LLM_MODEL", "CHOPPAD_LLM_KEY", "OPENROUTER_API_KEY")


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return json.dumps(self.payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class LlmConfig(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, {}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        for k in ENV_KEYS:
            os.environ.pop(k, None)

    def test_defaults_stay_on_openrouter(self):
        self.assertEqual(adv.llm_settings(), (adv.OPENROUTER_URL, adv.DEFAULT_MODEL, None))

    def test_env_overrides_and_key_fallback(self):
        env = {"CHOPPAD_LLM_URL": "http://127.0.0.1:4000/v1/chat/completions", "CHOPPAD_LLM_MODEL": "router/audio",
               "OPENROUTER_API_KEY": "or-key"}
        self.assertEqual(adv.llm_settings(env), ("http://127.0.0.1:4000/v1/chat/completions", "router/audio", "or-key"))
        self.assertEqual(adv.llm_settings({**env, "CHOPPAD_LLM_KEY": "router-key"})[2], "router-key")

    def test_request_goes_to_the_configured_endpoint(self):
        os.environ.update(CHOPPAD_LLM_URL="http://127.0.0.1:4000/v1/chat/completions", CHOPPAD_LLM_KEY="test-key-123")
        seen = {}

        def fake_urlopen(req, timeout=None):
            seen.update(url=req.full_url, auth=req.get_header("Authorization"), body=json.loads(req.data))
            return FakeResponse({"choices": [{"message": {"content": "{}"}}]})
        with mock.patch("urllib.request.urlopen", fake_urlopen):
            self.assertEqual(adv._call_llm("p", b"mp3", "router/audio"), "{}")
        self.assertEqual(seen["url"], "http://127.0.0.1:4000/v1/chat/completions")
        self.assertEqual(seen["auth"], "Bearer test-key-123")
        self.assertEqual(seen["body"]["model"], "router/audio")

    def test_local_router_needs_no_key_but_openrouter_does(self):
        with self.assertRaises(RuntimeError) as cm:
            adv._call_llm("p", b"mp3", "m")
        self.assertIn("CHOPPAD_LLM_KEY", str(cm.exception))
        os.environ["CHOPPAD_LLM_URL"] = "http://127.0.0.1:4000/v1/chat/completions"
        seen = {}

        def fake_urlopen(req, timeout=None):
            seen["auth"] = req.get_header("Authorization")
            return FakeResponse({"choices": [{"message": {"content": "{}"}}]})
        with mock.patch("urllib.request.urlopen", fake_urlopen):
            adv._call_llm("p", b"mp3", "m")
        self.assertIsNone(seen["auth"])

    def test_advise_uses_env_model_with_injected_client(self):
        os.environ["CHOPPAD_LLM_MODEL"] = "router/audio"
        tmp = tempfile.mkdtemp(prefix="choppad-advisor-cfg-")
        self.addCleanup(__import__("shutil").rmtree, tmp, ignore_errors=True)
        chops = load_fixture("chops")
        click_track(os.path.join(tmp, "audio.wav"), 120)
        for name, doc in (("analysis", load_fixture("analysis")), ("chops", chops)):
            with open(os.path.join(tmp, f"{name}.json"), "w") as f:
                json.dump(doc, f)
        models = []
        out = adv.advise(tmp, client=lambda prompt, audio, model: models.append(model) or load_fixture("advisor"), force=True)
        self.assertEqual(models, ["router/audio"])
        self.assertEqual(out["model"], "router/audio")
        explicit = []
        adv.advise(tmp, client=lambda prompt, audio, model: explicit.append(model) or load_fixture("advisor"), model="other/m", force=True)
        self.assertEqual(explicit, ["other/m"])


if __name__ == "__main__":
    unittest.main()
