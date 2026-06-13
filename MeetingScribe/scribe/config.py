"""Configuration management. Stored locally in MeetingScribe/data/config.json."""

import json
import os
import stat
import threading
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent          # .../MeetingScribe
DATA_DIR = APP_DIR / "data"
INPROGRESS_DIR = DATA_DIR / "inprogress"
TMP_DIR = DATA_DIR / "tmp"
LOG_FILE = DATA_DIR / "scribe.log"
CONFIG_FILE = DATA_DIR / "config.json"

_lock = threading.Lock()


def _default_transcripts_dir() -> str:
    # Default: a "Transcripts" folder next to the MeetingScribe app folder.
    return str(APP_DIR.parent / "Transcripts")


DEFAULTS = {
    "openai_api_key": "",
    "openai_base_url": "https://api.openai.com/v1",
    "transcripts_dir": _default_transcripts_dir(),
    "diarize_model": "gpt-4o-transcribe-diarize",
    "quick_model": "gpt-4o-transcribe",
    "quick_model_fallback": "whisper-1",
    "embedding_model": "text-embedding-3-small",
    # Semantic search quality: very short transcript fragments ("so", "okay",
    # "which") carry no retrievable meaning, yet their embeddings sit close to
    # almost any query and flood results with noise. Chunks with fewer than
    # this many characters are excluded from the *vector* (semantic) side of
    # search and are not embedded going forward. They remain fully keyword-
    # searchable, so a genuine short utterance ("Google ads.") is still found
    # when the literal word is typed. 25 was chosen empirically against real
    # transcripts: it removes near-noise fragments while every substantive
    # passage that is shorter is also a keyword match and so still surfaces.
    "search_min_semantic_chars": 25,
    # Backstop cosine-similarity floor for semantic hits. Kept below the
    # similarity of the weakest genuine matches observed in real transcripts
    # (~0.29) so it never clips a real result — it only trims residual noise.
    "search_semantic_floor": 0.22,
    # Tried in order until one works; the winner is cached in "summary_model_active".
    "summary_models": ["gpt-5-mini", "gpt-4.1-mini", "gpt-4o-mini"],
    "summary_model_active": "",
    "generate_summaries": True,
    # Cleanup stage: merges fragment turns, restores punctuation, applies the
    # glossary, resolves phantom speakers. Raw turns are always kept alongside.
    "cleanup_enabled": True,
    # Fast non-reasoning models only — cleanup echoes whole chunks as JSON,
    # which reasoning models (gpt-5*) do far too slowly.
    "cleanup_models": ["gpt-4.1-mini", "gpt-4o-mini"],
    "cleanup_model_active": "",
    # Pass stored voice profiles (data/voices/) to the diarizer from part 1.
    "voice_profiles_enabled": True,
    "port": 5723,
    # Per-request audio segment length sent to OpenAI (seconds). Small parts
    # transcribe fast individually and are processed IN PARALLEL; 5 minutes
    # balances per-request latency against diarization context per part.
    # (Hard API ceiling is ~1400s per chunk.)
    "segment_seconds": 300,
    # Max simultaneous OpenAI calls, shared across all recordings being
    # processed (keeps rate-limit pressure bounded).
    "transcribe_concurrency": 4,
    # Max recordings processed at once (local CPU politeness, not a hard limit).
    "max_concurrent_recordings": 4,
}


def ensure_dirs():
    for d in (DATA_DIR, INPROGRESS_DIR, TMP_DIR):
        d.mkdir(parents=True, exist_ok=True)


# Old default values that have been superseded. Earlier versions persisted
# the full config (defaults included), freezing them in config.json; these
# are treated as "not set" so improved defaults reach existing installs.
RETIRED_VALUES = {
    "segment_seconds": (1140,),
}


def _read_saved() -> dict:
    """Explicitly-saved settings only (defaults excluded, retired values dropped)."""
    try:
        if CONFIG_FILE.exists():
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
            if isinstance(saved, dict):
                out = {k: v for k, v in saved.items() if k in DEFAULTS}
                for k, stale in RETIRED_VALUES.items():
                    if out.get(k) in stale:
                        out.pop(k, None)
                return out
    except Exception:
        pass  # corrupted config falls back to defaults
    return {}


def load() -> dict:
    ensure_dirs()
    cfg = dict(DEFAULTS)
    cfg.update(_read_saved())
    return cfg


def save(updates: dict) -> dict:
    """Persist updates. Only explicitly-set keys are written to disk, so
    future default improvements automatically reach existing installs."""
    with _lock:
        saved = _read_saved()
        saved.update({k: v for k, v in updates.items() if k in DEFAULTS})
        ensure_dirs()
        tmp = CONFIG_FILE.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(saved, f, indent=2)
        os.replace(tmp, CONFIG_FILE)
        try:
            os.chmod(CONFIG_FILE, stat.S_IRUSR | stat.S_IWUSR)  # 0600 — key stays private
        except Exception:
            pass
        cfg = dict(DEFAULTS)
        cfg.update(saved)
        return cfg


def transcripts_dir(cfg=None) -> Path:
    cfg = cfg or load()
    p = Path(os.path.expanduser(cfg.get("transcripts_dir") or _default_transcripts_dir()))
    try:
        p.mkdir(parents=True, exist_ok=True)
    except Exception:
        p = Path(_default_transcripts_dir())
        p.mkdir(parents=True, exist_ok=True)
    return p


def log(msg: str):
    """Lightweight append log for troubleshooting."""
    try:
        ensure_dirs()
        from datetime import datetime
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass
