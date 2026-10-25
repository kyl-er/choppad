#!/usr/bin/env python3
"""Dump the tail of a Claude Code session transcript (.jsonl) as readable markdown.

  tools/claude_context.py <session.jsonl> [--last 60] [--out FILE]

Keeps user/assistant text and tool-call one-liners; drops tool output bodies.
"""
import json, sys


def lines(path):
    for raw in open(path, encoding="utf-8", errors="replace"):
        try:
            yield json.loads(raw)
        except json.JSONDecodeError:
            continue


def render(entry):
    msg = entry.get("message") or {}
    role = msg.get("role") or entry.get("type")
    content = msg.get("content")
    out = []
    if isinstance(content, str):
        out.append(content)
    elif isinstance(content, list):
        for c in content:
            t = c.get("type")
            if t == "text" and c.get("text", "").strip():
                out.append(c["text"])
            elif t == "tool_use":
                inp = c.get("input") or {}
                desc = inp.get("description") or inp.get("file_path") or inp.get("command", "")[:120]
                out.append(f"`[tool {c.get('name')}] {desc}`")
    if not out or role not in ("user", "assistant"):
        return None
    return f"### {role}\n" + "\n".join(out)


if __name__ == "__main__":
    path = sys.argv[1]
    n = int(sys.argv[sys.argv.index("--last") + 1]) if "--last" in sys.argv else 60
    blocks = [b for b in (render(e) for e in lines(path)) if b]
    text = "# Claude session context (tail)\n\n" + "\n\n".join(blocks[-n:]) + "\n"
    if "--out" in sys.argv:
        open(sys.argv[sys.argv.index("--out") + 1], "w").write(text)
    else:
        print(text)
