"""Where a song's saved work lives on disk, for the browser tests.

Since the drum pad mega banks (contracts §15) a song's project is no longer media/<song>/project.json but the project
of its mega bank, media/_mega/<id>/mega.json. Tests read the saved state and reset it through these two helpers."""
import json
import os
import shutil


def saved_project(media_root, sid):
    """The project saved for song `sid`: its most recently updated mega bank, else the old project.json, else {}."""
    best = None
    root = os.path.join(media_root, "_mega")
    for d in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        path = os.path.join(root, d, "mega.json")
        if d.startswith(".") or not os.path.isfile(path):
            continue
        with open(path) as f:
            doc = json.load(f)
        if doc.get("source") == sid and (best is None or str(doc.get("updated", "")) >= str(best.get("updated", ""))):
            best = doc
    if best is not None:
        return best["project"]
    path = os.path.join(media_root, sid, "project.json")
    if os.path.isfile(path):
        with open(path) as f:
            return json.load(f)
    return {}


def forget_projects(media_root):
    """Every test starts from empty projects: drop all mega banks (and with them the record of migrated songs)."""
    shutil.rmtree(os.path.join(media_root, "_mega"), ignore_errors=True)
