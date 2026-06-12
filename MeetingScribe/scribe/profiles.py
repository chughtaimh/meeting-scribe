"""Persistent voice profiles.

A profile is a short reference clip of a known person's voice, stored in
MeetingScribe/data/voices/<Name>.wav. Profiles are passed to the diarization
model from the FIRST audio part of every meeting, which anchors speaker
clusters to known voices — this is the main defence against phantom speakers
(e.g. background voices at a remote participant's location being assigned
"Speaker C"/"Speaker D", or a real participant splitting into a new label).

Profiles are harvested automatically when a speaker is renamed to a real name
in the app (the existing rename flow), so the library builds itself.
Everything stays local; clips ride the same OpenAI API call the audio already
takes. Delete a file in data/voices/ to forget a profile.
"""

import re
from pathlib import Path

from . import audio, config

VOICES_DIR = config.DATA_DIR / "voices"

MAX_REFS = 4          # API limit for known_speaker_references
CLIP_SECONDS = 8.0    # reference length
MIN_TURN_SECONDS = 3.0


def _safe(name: str) -> str:
    s = re.sub(r'[\\/:*?"<>|\r\n]+', " ", name or "").strip()
    s = re.sub(r"\s+", " ", s)
    return s[:60]


def list_profiles() -> list:
    """[(name, wav_path)] — most recently updated first."""
    try:
        VOICES_DIR.mkdir(parents=True, exist_ok=True)
        items = [(p.stem, str(p)) for p in VOICES_DIR.glob("*.wav")]
        items.sort(key=lambda x: Path(x[1]).stat().st_mtime, reverse=True)
        return items
    except Exception as e:
        config.log("voice profiles list failed: %s" % e)
        return []


def known_names() -> set:
    return {n for n, _ in list_profiles()}


def pick_for_meeting(max_n=MAX_REFS) -> list:
    """Profiles to send with part 1 of a meeting (most recent first)."""
    return list_profiles()[:max_n]


def save_profile(name: str, src_audio, start_s: float, dur_s: float) -> str:
    """Extract a clip from src_audio and store it as <name>'s profile.
    Overwrites any existing profile for that name (latest voice wins)."""
    name = _safe(name)
    if not name or name.lower().startswith("speaker "):
        raise ValueError("Not a real name: %r" % name)
    VOICES_DIR.mkdir(parents=True, exist_ok=True)
    out = VOICES_DIR / (name + ".wav")
    clip_len = min(CLIP_SECONDS, max(2.0, dur_s - 0.4))
    audio.extract_clip(src_audio, start_s + 0.2, clip_len, out)
    config.log("voice profile saved: %s (%.1fs)" % (name, clip_len))
    return str(out)


def harvest_from_recording(name: str, label: str, folder, turns: list) -> str:
    """Best-effort: find the longest clean turn for `label` in this recording
    and save it as `name`'s profile. Returns path or '' on failure."""
    try:
        best = None
        for t in turns or []:
            if t.get("speaker") != label:
                continue
            dur = float(t.get("end_s") or 0) - float(t.get("start_s") or 0)
            if dur >= MIN_TURN_SECONDS and (best is None or dur > best[1]):
                best = (float(t["start_s"]), dur)
        if not best:
            return ""
        folder = Path(folder)
        candidates = [p for p in folder.iterdir()
                      if p.is_file() and p.name.startswith("audio")]
        if not candidates:
            return ""
        return save_profile(name, candidates[0], best[0], best[1])
    except Exception as e:
        config.log("voice profile harvest failed for %s: %s" % (name, e))
        return ""
