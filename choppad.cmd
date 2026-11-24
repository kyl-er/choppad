@echo off
rem ChopPad launcher for Windows:  choppad --serve      choppad "https://www.youtube.com/watch?v=..." --serve
rem Needs uv (https://docs.astral.sh/uv/), plus ffmpeg and node on PATH. Run "uv sync" once in this folder first.
uv run --project "%~dp0." python "%~dp0components\integrate\choppad.py" %*
