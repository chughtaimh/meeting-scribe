"""Persistent vocabulary glossary used by the transcript cleanup stage.

Stored in MeetingScribe/data/glossary.json. Three sections:

  corrections: {"misheard text": "correct text"}  — applied/known fixes
  vocabulary:  ["SteerCo", "JAPAC", ...]          — domain terms to bias toward
  names:       ["Jay Furlano", ...]               — people; never silently changed,
                                                    used to resolve unclear names

Once a term or name is confirmed here, the cleanup stage applies it silently
and stops flagging it — review noise should fall meeting over meeting.
"""

import json
import threading

from . import config

GLOSSARY_FILE = config.DATA_DIR / "glossary.json"

_lock = threading.Lock()

_EMPTY = {"corrections": {}, "vocabulary": [], "names": []}


def load() -> dict:
    try:
        if GLOSSARY_FILE.exists():
            with open(GLOSSARY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                out = dict(_EMPTY)
                out.update({k: v for k, v in data.items() if k in _EMPTY})
                return out
    except Exception as e:
        config.log("glossary load failed: %s" % e)
    return dict(_EMPTY)


def save(data: dict):
    with _lock:
        config.ensure_dirs()
        tmp = GLOSSARY_FILE.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        import os
        os.replace(tmp, GLOSSARY_FILE)


def add_name(name: str):
    """Add a confirmed person name (called when a speaker is renamed)."""
    name = (name or "").strip()
    if not name or name.lower().startswith("speaker "):
        return
    g = load()
    if name not in g["names"]:
        g["names"].append(name)
        save(g)


def prompt_block(g=None) -> str:
    """Render the glossary as a prompt section for the cleanup model."""
    g = g or load()
    lines = []
    if g["corrections"]:
        lines.append("Known transcription fixes (apply silently when the left side "
                     "appears; these are confirmed):")
        for bad, good in g["corrections"].items():
            lines.append('  - "%s" -> "%s"' % (bad, good))
    if g["vocabulary"]:
        lines.append("Domain vocabulary (prefer these spellings/casings): "
                     + ", ".join(g["vocabulary"]))
    if g["names"]:
        lines.append("Known people (use these spellings; do NOT invent or swap "
                     "names that are not on this list): " + ", ".join(g["names"]))
    return "\n".join(lines)
