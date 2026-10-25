"""advisor: optional OpenRouter audio-LLM section map, best-16 chop bank & BPM/meter check.
See contracts/README.md §6 (v2.1). Off the critical path; mocked in unit tests."""

DEFAULT_MODEL = "qwen/qwen3.8-omni-flash"


def estimate_cost_usd(duration_s: float, model: str = DEFAULT_MODEL) -> float:
    """Estimate OpenRouter audio + text request cost in USD for `duration_s` seconds of audio."""
    raise NotImplementedError("advisor.estimate_cost_usd")


def build_prompt(analysis: dict, chops: dict) -> str:
    """Build the hybrid bar/chop-index prompt listing local bar/chop timestamps so the LLM picks valid chop indices."""
    raise NotImplementedError("advisor.build_prompt")


def advise(media_dir: str, *, client=None, model: str = DEFAULT_MODEL,
           max_cost_usd: float = 0.05, force: bool = False) -> dict:
    """Produce and cache <media_dir>/advisor.json (validated against choppad.advisor/2).
    Raises ValueError if estimated cost > max_cost_usd.
    If `client` is given, calls `client(prompt, audio_mp3_bytes, model) -> dict|str` instead of OpenRouter HTTP."""
    raise NotImplementedError("advisor")
