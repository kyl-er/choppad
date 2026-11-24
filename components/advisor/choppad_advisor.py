"""advisor: optional audio-LLM section map, best-16 chop bank & BPM/meter check (OpenRouter by default, any
OpenAI-compatible chat endpoint such as the local LiteLLM router via CHOPPAD_LLM_URL / CHOPPAD_LLM_MODEL / CHOPPAD_LLM_KEY).
See contracts/README.md §6 (v2.1). Off the critical path; mocked in unit tests.

Three public functions:
  estimate_cost_usd(duration_s, model=DEFAULT_MODEL) -> float   # hard $0.05/song cap before any network call
  build_prompt(analysis, chops) -> str                          # hybrid bar/chop-index prompt
  advise(media_dir, *, client=None, ...) -> advisor             # request, sanitize, validate, cache advisor.json

Stdlib only. Tests run 100% offline: pass `client(prompt, audio_mp3_bytes, model) -> dict|str`.
"""

import json
import os
import shutil
import subprocess
import tempfile

from validate import ContractError, validate_advisor

DEFAULT_MODEL = "qwen/qwen3.8-omni-flash"

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


def llm_settings(env=None) -> tuple[str, str, str | None]:
    """(url, model, key) for the audio LLM. Env: CHOPPAD_LLM_URL (default OpenRouter), CHOPPAD_LLM_MODEL (default
    DEFAULT_MODEL), CHOPPAD_LLM_KEY (falls back to OPENROUTER_API_KEY). Only reads the environment; keys are never printed."""
    env = os.environ if env is None else env
    return (env.get("CHOPPAD_LLM_URL") or OPENROUTER_URL,
            env.get("CHOPPAD_LLM_MODEL") or DEFAULT_MODEL,
            env.get("CHOPPAD_LLM_KEY") or env.get("OPENROUTER_API_KEY") or None)

# Audio is transcoded to 16 kHz mono 32 kbps MP3 (~960 KB for a 4-minute song) before the request.
AUDIO_SR = 16000
AUDIO_CHANNELS = 1
AUDIO_BITRATE = "32k"

# Call budget in seconds of audio: past this the request is refused before any network call (cost guard).
MAX_AUDIO_S = 7 * 60

# USD per second of audio + per-request text overhead. Tuned so a 4-minute song lands well under $0.05.
_COST_TABLE = {
    "qwen/qwen3.8-omni-flash": (0.0000025, 0.0003),
    "google/gemini-3.5-flash-lite": (0.000015, 0.001),
}
_FALLBACK_COST = (0.0000333333333333333, 0.0)
_COST_FLOOR_USD = 0.000001

# Deterministic, model-independent truncation bounds so the prompt stays cheap.
MAX_CHOPS_IN_PROMPT = 256
MAX_DOWNBEATS_IN_PROMPT = 64


def _cost_rates(model: str) -> tuple[float, float]:
    """(usd_per_audio_second, usd_per_request_text_overhead) for `model`."""
    if not isinstance(model, str) or not model:
        return _FALLBACK_COST
    return _COST_TABLE.get(model.strip().lower(), _FALLBACK_COST)


def estimate_cost_usd(duration_s: float, model: str = DEFAULT_MODEL) -> float:
    """Estimate OpenRouter audio + text request cost in USD for `duration_s` seconds of audio."""
    if not isinstance(duration_s, (int, float)) or isinstance(duration_s, bool):
        raise ValueError("duration_s must be a number")
    if duration_s < 0:
        raise ValueError("duration_s must be >= 0")
    per_s, overhead = _cost_rates(model)
    return max(per_s * float(duration_s) + overhead, _COST_FLOOR_USD)


def _fmt(x: float) -> str:
    """Stable, compact seconds formatting (2.000 -> '2.0', 1.500 -> '1.5') so prompts are reproducible."""
    return f"{round(float(x), 3):g}"


def _chop_lines(chops: dict) -> list[str]:
    rows = chops.get("chops") or []
    lines = []
    for ch in rows[:MAX_CHOPS_IN_PROMPT]:
        lines.append(f"{ch['index']} | bar {ch['bar']} | beat {ch['beat']} | "
                     f"{_fmt(ch['start'])} | {_fmt(ch['end'])}")
    if len(rows) > MAX_CHOPS_IN_PROMPT:
        lines.append(f"... ({len(rows) - MAX_CHOPS_IN_PROMPT} more chops omitted)")
    return lines


def build_prompt(analysis: dict, chops: dict) -> str:
    """Hybrid bar/chop-index prompt: the LLM selects *local chop indices*, never raw timestamps,
    because raw-timestamp hallucination is the failure mode this component exists to avoid (PRD §7)."""
    bpm = analysis.get("bpm")
    meter = analysis.get("meter") or {}
    bpb = meter.get("beats_per_bar")
    beat_unit = meter.get("beat_unit", 4)
    meter_str = f"{bpb}/{beat_unit}"
    duration_s = analysis.get("duration_s")
    downbeats = list(analysis.get("downbeats") or [])
    shown_downbeats = downbeats[:MAX_DOWNBEATS_IN_PROMPT]
    all_chops = chops.get("chops") or []
    n_chops = len(all_chops)
    banks = chops.get("banks") or []
    pad_max = n_chops - 1 if n_chops else -1
    confidence = analysis.get("confidence", 0.0)
    meter_confidence = analysis.get("meter_confidence", 0.0)

    lines = [
        "You are a music-arrangement assistant for a 4x4 sample-pad instrument.",
        "Below is a *local* analysis of one song produced by a beat tracker. The bars and chops are already",
        "indexed on disk: your job is to CURATE, never to invent timestamps.",
        "",
        "<song_analysis>",
        f"duration_s: {_fmt(duration_s) if isinstance(duration_s, (int, float)) else duration_s}",
        f"bpm: {_fmt(bpm) if isinstance(bpm, (int, float)) else bpm}",
        f"meter: {meter_str}",
        f"bpm_confidence: {confidence}",
        f"meter_confidence: {meter_confidence}",
        f"beats per bar: {bpb}",
        f"total_chops: {n_chops}",
        "",
        "downbeats_s (bar starts, in seconds): " + (", ".join(_fmt(b) for b in shown_downbeats) or "(none)"),
    ]
    if len(downbeats) > len(shown_downbeats):
        lines.append(f"... ({len(downbeats) - len(shown_downbeats)} more downbeats omitted)")
    lines += [
        "</song_analysis>",
        "",
        "<chop_table>",
        "Every row: index | bar | beat | start_s | end_s. These indices are the ONLY valid values for suggested_bank.",
        "index | bar | beat | start_s | end_s",
    ]
    lines += _chop_lines(chops) or ["(no chops)"]
    lines += [
        "</chop_table>",
        "",
        f"<existing_banks>pads_per_bank=16, bank_size={chops.get('bank_size')}, banks={len(banks)}</existing_banks>",
        "",
        "<task>",
        "1. `sections`: array of song sections (intro / verse / hook / break / outro ...), each a dict with",
        "   `label` (non-empty string), `start_bar`, `end_bar` (ints, 0 <= start_bar < end_bar), `start_s`, `end_s`",
        "   (numbers, 0 <= start_s < end_s). Derive bars from the downbeat list; cover the song from its first",
        "   downbeat, sections contiguous and non-overlapping.",
        f"2. `suggested_bank`: array of 1..16 UNIQUE chop indices selecting the most useful 16 pads. Every value",
        f"   MUST be an int in 0..{pad_max}. Prefer downbeat-starting chops (beat == 0) and across-section variety",
        "   of kick/snare/hat/bass/one-shot material. Pad 1 = index 0 of your array (bottom-left of the grid).",
        "3. `pad_labels`: array of strings, SAME LENGTH as `suggested_bank`, in the same order (e.g. \"Kick 1\",",
        "   \"Snare 2\", \"Hook vox\"). Non-empty, short (<= 12 chars).",
        "4. `bpm_meter_check`: dict with `felt_bpm` (number in [30,300]), `bpm_mult_hint` (0.5, 1 or 2),",
        "   `meter_hint` (\"2/4\", \"3/4\" or \"4/4\"), and `notes` (string). Use this to flag a half/double-time",
        "   or wrong-downbeat analysis; if the detected grid is correct, echo it back.",
        "</task>",
        "",
        "Return ONLY a single JSON object, no prose and no markdown fences, with exactly these keys:",
        '{"sections": [...], "suggested_bank": [...], "pad_labels": [...], "bpm_meter_check": {...}}',
    ]
    return "\n".join(lines)


def _mp3_bytes(audio_path: str) -> bytes:
    """16 kHz mono 32 kbps MP3 of the whole track via one ffmpeg pass (subprocess; no Python deps)."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is required to transcode audio for OpenRouter")
    fd, out = tempfile.mkstemp(suffix=".mp3", prefix="choppad-advisor-")
    os.close(fd)
    try:
        cmd = [ffmpeg, "-loglevel", "error", "-y", "-i", audio_path,
               "-vn", "-ac", str(AUDIO_CHANNELS), "-ar", str(AUDIO_SR),
               "-codec:a", "libmp3lame", "-b:a", AUDIO_BITRATE, out]
        subprocess.run(cmd, check=True, capture_output=True)
        with open(out, "rb") as fh:
            return fh.read()
    finally:
        try:
            os.unlink(out)
        except OSError:
            pass


def _call_llm(prompt: str, mp3: bytes, model: str) -> dict:
    """Real HTTP path; only reached when no injectable client is supplied. Endpoint and key come from llm_settings().
    A custom endpoint (a local router) may need no key; the default OpenRouter endpoint always does."""
    import base64
    import urllib.error
    import urllib.request

    url, _, key = llm_settings()
    if not key and url == OPENROUTER_URL:
        raise RuntimeError("no API key: set CHOPPAD_LLM_KEY (or OPENROUTER_API_KEY), or point CHOPPAD_LLM_URL at a local "
                           "router; pass client=... for offline use")
    audio_b64 = base64.b64encode(mp3).decode("ascii")
    body = {
        "model": model,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "input_audio",
                 "input_audio": {"data": audio_b64, "format": "mp3"}},
            ],
        }],
        "response_format": {"type": "json_object"},
        "temperature": 0.2,
    }
    headers = {"Content-Type": "application/json", "X-Title": "ChopPad advisor"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"LLM HTTP {e.code}: {e.read()[:500].decode('utf-8', 'replace')}") from e
    try:
        return payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise RuntimeError(f"unexpected LLM payload: {str(payload)[:500]}") from e


def _coerce(raw) -> dict:
    """Accept a mapping or a JSON string (possibly wrapped in markdown fences) and return a dict."""
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", "replace")
    if not isinstance(raw, str):
        raise ValueError(f"client returned {type(raw).__name__}; expected dict or JSON str")
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
        text = text.strip()
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"client did not return valid JSON: {e}") from e
    if not isinstance(doc, dict):
        raise ValueError(f"client returned JSON {type(doc).__name__}; expected an object")
    return doc


def _sanitize(doc: dict, chops: dict, model: str) -> dict:
    """Clamp/filter chop indices out of range and keep pad_labels aligned. Sets schema + model."""
    rows = chops["chops"]
    n = len(rows)
    bank = doc.get("suggested_bank")
    labels = doc.get("pad_labels")
    if not isinstance(bank, list):
        bank = []
    if not isinstance(labels, list):
        labels = []

    clean_bank, clean_labels = [], []
    for i, idx in enumerate(bank):
        if isinstance(idx, bool) or not isinstance(idx, int):
            if isinstance(idx, float) and idx.is_integer():
                idx = int(idx)
            else:
                continue
        if idx < 0 or idx >= n:
            continue  # filter out-of-range instead of clamping onto a real neighbouring chop
        clean_bank.append(idx)
        label = labels[i] if i < len(labels) else None
        if not isinstance(label, str) or not label.strip():
            label = f"Chop {idx}"
        clean_labels.append(label.strip())
        if len(clean_bank) == 16:
            break

    if not clean_bank and n:  # model returned nothing usable: fall back to the first bank, every pad real
        clean_bank = list(chops["banks"][0])[:16]
        clean_labels = [f"Chop {i}" for i in clean_bank]

    doc["suggested_bank"] = clean_bank
    doc["pad_labels"] = clean_labels
    doc["schema"] = "choppad.advisor/2"
    doc["model"] = model
    if not isinstance(doc.get("sections"), list) or not doc["sections"]:
        doc["sections"] = [{
            "label": "full song",
            "start_bar": 0,
            "end_bar": max(1, (rows[-1]["bar"] + 1) if rows else 1),
            "start_s": rows[0]["start"] if rows else 0.0,
            "end_s": rows[-1]["end"] if rows else 0.0,
        }]
    chk = doc.get("bpm_meter_check")
    if not isinstance(chk, dict):
        chk = {}
    chk.setdefault("felt_bpm", float(chops.get("bpm") or 120.0))
    chk.setdefault("bpm_mult_hint", chops.get("bpm_mult", 1))
    bpb = chops.get("beats_per_bar", 4)
    chk.setdefault("meter_hint", f"{bpb}/4")
    chk.setdefault("notes", "advisor output normalized")
    doc["bpm_meter_check"] = chk
    return doc


def _read_json(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def advise(media_dir: str, *, client=None, model: str | None = None,
           max_cost_usd: float = 0.05, force: bool = False) -> dict:
    """Produce and cache <media_dir>/advisor.json (validated against choppad.advisor/2).
    Raises ValueError if estimated cost > max_cost_usd. `model` defaults to $CHOPPAD_LLM_MODEL, then DEFAULT_MODEL.
    If `client` is given, calls `client(prompt, audio_mp3_bytes, model) -> dict|str` instead of the HTTP endpoint."""
    model = model or llm_settings()[1]
    out_path = os.path.join(media_dir, "advisor.json")
    if os.path.isfile(out_path) and not force:
        return _read_json(out_path)

    analysis = _read_json(os.path.join(media_dir, "analysis.json"))
    chops = _read_json(os.path.join(media_dir, "chops.json"))

    duration_s = analysis.get("duration_s")
    if not isinstance(duration_s, (int, float)) or isinstance(duration_s, bool) or duration_s <= 0:
        raise ValueError("analysis.duration_s must be a positive number")
    if duration_s > MAX_AUDIO_S:
        raise ValueError(f"audio is {duration_s:.0f}s, past the {MAX_AUDIO_S}s budget; refusing to send it")
    if estimate_cost_usd(duration_s, model) > max_cost_usd:
        raise ValueError(f"estimated cost exceeds max_cost_usd={max_cost_usd}")

    prompt = build_prompt(analysis, chops)
    audio_path = os.path.join(media_dir, "audio.wav")
    audio = _mp3_bytes(audio_path)

    raw = client(prompt, audio, model) if client is not None else _call_llm(prompt, audio, model)
    doc = _sanitize(_coerce(raw), chops, model)
    try:
        validate_advisor(doc, chops=chops)
    except ContractError as e:
        raise ValueError(f"advisor output failed choppad.advisor/2 validation: {e}") from e

    tmp = out_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1, sort_keys=False)
        fh.write("\n")
    os.replace(tmp, out_path)
    return doc


_call_openrouter = _call_llm  # old name
