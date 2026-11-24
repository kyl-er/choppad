# ChopPad

A browser sampler for chopping songs onto drum pads: paste a YouTube link, get tempo-aligned chops on a 4×4 pad grid, play them from the keyboard or a MIDI controller, sequence them and export to Hydrogen or SFZ. It runs entirely on your own machine.

This branch (`master`) is the earlier version 3. The current workstation — new interface, drum pad mega banks, agent control (CLI and MCP), Hydrogen bridge — is on the [`v4`](https://github.com/kyl-er/choppad/tree/v4) branch and has not been merged here yet. Start there:

```bash
git clone -b v4 https://github.com/kyl-er/choppad && cd choppad
uv sync
./choppad --serve            # then open http://127.0.0.1:8960
```

To run this older version: `uv sync`, then `.venv/bin/python components/integrate/choppad.py --serve`. Needs `ffmpeg` and `node` on your PATH.

Songs you import are stored in `media/`, which is never committed.
