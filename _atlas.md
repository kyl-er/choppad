---
title: choppad
type: project
status: production          # idea | active | production | paused | archived  (lifecycle is a field, never a move)
goal: "Browser MPC sampler for YouTube songs, growing into an AI-controllable, DAW-connected workstation (v4: ChopPad Station)"
next: "WAITING on neo: pick a W4 design direction (docs/design/, CHOP-8). W1 merged; W2 DAW bridge and W3 sampler core builders running in worktrees"
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
