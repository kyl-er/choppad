---
title: choppad
type: project
status: production          # idea | active | production | paused | archived  (lifecycle is a field, never a move)
goal: "Browser MPC sampler for YouTube songs, growing into an AI-controllable, DAW-connected workstation (v4: ChopPad Station)"
next: "CHOP-13 drum pad mega banks built on v4 (steps A-D merged, 13/13 green). WAITING on neo: restart the :8960 server on this commit and try it; phone acceptance (CHOP-9); say when to merge v4 to master"
kaneo: CHOP
runs-as: "web :8960 (choppad --serve); launcher 04-Apps/media-tools/choppad"          # cli | daemon :port | web :port | mcp | none
repo: ""
zone: 03-Projects/media
date: 2026-11-02
---
# choppad

Browser MPC sampler for YouTube songs. Paste a URL, get tempo-aligned chops on a 4x4 pad grid, play them from MIDI/keyboard, sequence, apply FX, export to Hydrogen/Ardour.

## Notes
- Board: Kaneo `CHOP`. The old dotpm board in `~/09-Archive/vaults/MegaVault/Projects/ChopPad/` is retired (read-only history; key docs copied to `docs/history/`).
- Current plan: `docs/PRD-v4.md`. Branch `v4`; builders work in git worktrees, one per workstream.

## Log (append-only — session-end hooks add one line per session)
- 2026-11-02 created by `atlas new`
- 2026-11-21 [claude:4be15e40-c601-4521-bd53-0fd11f6e79a1] rename this meida ops clean up my messy input chaos keep all request and word <linked_cont
