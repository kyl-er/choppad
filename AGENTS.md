# ChopPad — agent entry point

Browser MPC sampler for YouTube songs (`choppad --serve` → http://127.0.0.1:8960). Read in this order:

1. `_atlas.md` — status, goal, next.
2. `docs/PRD-v4.md` — the current plan and workstreams. Older PRDs and research are in `docs/history/` (history only).
3. Your task on the Kaneo board `CHOP` (`kq list CHOP`, `kq show CHOP-n`). The task, not chat, is the source of truth.
4. `contracts/README.md` — data contracts; `contracts/validate.py` is the executable definition.

Rules:
- Work on branch `v4` or a worktree branched from it; one workstream per worktree. Write only inside the folders your task names.
- Never edit tests to make them pass. Contracts are orchestrator-owned: ask for a change in a task comment.
- Tests: `PATH="$PWD/.venv/bin:$PATH" python3 tools/status.py` runs every suite in dependency order (`--vault` writes `docs/STATUS.md`). The Python suites need the repo `.venv`; the browser suite (`tests/e2e/`) runs on `/usr/bin/python3`, which has Playwright.
- A milestone is done only with green suites **and** a real-browser pass on a real song (Funkorama, Faster Does It, Mellowtron are in `media/`).
- Finish with a task comment: SUMMARY / CHANGES / TESTS / FOLLOWUPS, then set the task to `in-review`. Stuck on a decision: comment `BLOCKED: <decision needed>` and stop.
- Kill any server you start. Find it by port (`ss -ltnp | grep 8960`), not `pkill -f`. No network in default test runs. `media/` stays gitignored.
- The old dotpm board (`~/09-Archive/vaults/MegaVault/Projects/ChopPad/`) is retired and read-only. `tools/heartbeat.sh` and `tools/agbridge.py` belong to that old setup.
