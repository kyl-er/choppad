#!/usr/bin/env python3
"""Bridge to Antigravity CLI (agy) for multi-model planning debates.

  tools/agbridge.py ask <session> <prompt.md|->      # send one turn, print the reply
  tools/agbridge.py sessions                         # list sessions + conversation ids

Each session (e.g. ag1, ag2) is an independent agy conversation with its own empty workspace,
so sessions never see each other or this repo. Every turn is logged to the vault:
  <vault>/Planning/debate/NN-<session>-{prompt,reply}.md
"""
import json, os, subprocess, sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
VAULT = os.path.expanduser(json.load(open(os.path.join(ROOT, "deps.json")))["vault"])
DEBATE = os.path.join(VAULT, "Planning", "debate")
STATE = os.path.join(DEBATE, ".sessions.json")
WORK = os.path.expanduser("~/.cache/choppad-agbridge")
MODEL = os.environ.get("AGBRIDGE_MODEL", "gemini-4-argon-high")


def load():
    return json.load(open(STATE)) if os.path.exists(STATE) else {}


def log(name, body):
    n = len([f for f in os.listdir(DEBATE) if f[:2].isdigit()]) + 1
    path = os.path.join(DEBATE, f"{n:02d}-{name}.md")
    open(path, "w").write(body)
    return path


def ask(session, prompt):
    os.makedirs(DEBATE, exist_ok=True)
    ws = os.path.join(WORK, session)
    os.makedirs(ws, exist_ok=True)
    st = load()
    cmd = ["agy", "-p", prompt, "--model", MODEL, "--output-format", "json", "--print-timeout", "20m"]
    if session in st:
        cmd += ["--conversation", st[session]]
    log(f"{session}-prompt", f"---\nsession: {session}\nmodel: {MODEL}\n---\n\n{prompt}\n")
    p = subprocess.run(cmd, cwd=ws, capture_output=True, text=True)
    try:
        out = json.loads(p.stdout)
    except json.JSONDecodeError:
        sys.exit(f"agy failed (exit {p.returncode}): {p.stderr[-2000:]}")
    if out.get("status") != "SUCCESS":
        sys.exit(f"agy {out.get('status')}: {out.get('error')}")
    st = load()  # re-read: parallel sessions may have written meanwhile
    st[session] = out["conversation_id"]
    json.dump(st, open(STATE, "w"), indent=1)
    path = log(f"{session}-reply", f"---\nsession: {session}\nmodel: {MODEL}\nconversation: {out['conversation_id']}\n"
                                   f"tokens: {out.get('usage', {}).get('total_tokens')}\n---\n\n{out['response']}\n")
    print(out["response"])
    print(f"\n[logged: {path}]", file=sys.stderr)


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "sessions":
        print(json.dumps(load(), indent=1))
    elif len(sys.argv) == 4 and sys.argv[1] == "ask":
        src = sys.argv[3]
        ask(sys.argv[2], sys.stdin.read() if src == "-" else open(src).read())
    else:
        sys.exit(__doc__)
