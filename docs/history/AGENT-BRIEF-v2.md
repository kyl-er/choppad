---
title: ChopPad — Agent brief
type: protocol
date: 2026-10-25
tags: [choppad, agents, protocol]
---

# Agent brief (read fully before touching anything)

You are one of several agents building ChopPad. Read, in order:
1. [[PRD]] — what we are building and why (v2.1).
2. `~/03-Projects/choppad/contracts/README.md` — the data contracts. **Your only interface to other agents.**
3. Your task note in `_tasks/` (assignee = you) — scope, folder, tests, definition of done.
4. `Inbox/` — every message with `to:` you or `all`, newest last.

## Where you may write
| You | Code folder (write) | Vault (write) |
|---|---|---|
| agent-ingest | `components/ingest/` | own task note, `Inbox/` |
| agent-rhythm | `components/rhythm/` | own task note, `Inbox/` |
| agent-chop | `components/chop/` | own task note, `Inbox/` |
| agent-midi | `components/midi/` | own task note, `Inbox/` |
| agent-ui | `components/ui/` | own task note, `Inbox/` |
| agent-advisor | `components/advisor/` | own task note, `Inbox/` |
| agent-export | `components/export/` | own task note, `Inbox/` |
| agent-integrator | `components/integrate/`, `components/ui/main.js`, `components/ui/index.html` (wiring only, coordinate with agent-ui), repo root `pyproject.toml` | own task note, `Inbox/` |
| agent-research | `docs/` | own task note, `Inbox/` |

Everything else is read-only: `contracts/`, `tools/`, `deps.json`, other components, other agents' tasks, [[PRD]].
**Do not edit tests to make them pass.** If a test is wrong, send a `contract-change` message and stop on that point.

## Work loop
1. Set your task `status: in-progress`, `progress: 0`, append a line to its `## Log`.
2. Run `tools/status.py <your-component>` from the repo root — see your RED baseline and who depends on you.
3. Implement until your suite is GREEN. Python deps: add to repo-root `pyproject.toml` via `uv add` (or use the existing repo `.venv` / system packages). Keep deps small (PRD §2).
4. Run `tools/status.py --vault` (refreshes [[STATUS]] for everyone).
5. Commit only your folder: `git add components/<you> && git commit -m "<component>: …"`.
6. Set task `status: review`, `progress: 100`, and post a **report** in `Inbox/`.

## Task note fields (dotpm — edit frontmatter only, keep YAML valid)
`status`: `todo | in-progress | blocked | review | done | cancelled` · `progress`: 0–100 · `updatedAt`: ISO time.
Write prose above the `Project:` line in the body; the plugin regenerates everything below it.
Only the orchestrator/neo moves a task to `done`.

## Inbox = messaging
One file per message: `Inbox/YYYYMMDD-HHMM-<from>-to-<to>-<slug>.md`
```yaml
---
from: agent-rhythm
to: agent-chop            # agent name, orchestrator, neo, or all
type: report              # report | question | blocker | handoff | contract-change | ack
task: "[[rhythm-tempo-beats-and-meter]]"
status: unread            # recipient flips to read
date: 2026-10-25T14:02
---
```
`report` body = **SUMMARY / CHANGES / TESTS / FOLLOWUPS** (TESTS = paste the `tools/status.py` row).
`blocker` body = `BLOCKED: <decision needed>` + what you tried. Then set task `status: blocked` and stop that line of work — never guess past a blocker.
Never delete messages; reply with a new file.

## Hygiene
- No long-running processes left behind (servers, watchers). Kill what you start.
- No network in default test runs (YouTube test is opt-in via `CHOPPAD_NET=1`).
- `media/` and generated audio/video are gitignored — keep it that way.
