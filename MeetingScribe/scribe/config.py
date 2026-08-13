"""Configuration management. Stored locally in <data dir>/config.json —
MeetingScribe/data/ for in-place installs, or
~/Library/Application Support/MeetingScribe/data/ when run from the .app bundle."""

import json
import os
import stat
import threading
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent          # .../MeetingScribe


def _resolve_data_dir() -> Path:
    # Precedence: SCRIBE_DATA_DIR env (set by launch.sh when running from an
    # .app bundle, whose Resources tree is read-only under app translocation),
    # then an existing in-place data/ dir (dev checkouts and installs that
    # predate the bundle), then the standard macOS per-user location.
    env = os.environ.get("SCRIBE_DATA_DIR")
    if env:
        return Path(env).expanduser()
    legacy = APP_DIR / "data"
    if legacy.is_dir():
        return legacy
    return Path.home() / "Library" / "Application Support" / "MeetingScribe" / "data"


DATA_DIR = _resolve_data_dir()
INPROGRESS_DIR = DATA_DIR / "inprogress"
TMP_DIR = DATA_DIR / "tmp"
LOG_FILE = DATA_DIR / "scribe.log"
CONFIG_FILE = DATA_DIR / "config.json"

_lock = threading.Lock()


def _default_transcripts_dir() -> str:
    if DATA_DIR == APP_DIR / "data":
        # In-place install: a "Transcripts" folder next to the MeetingScribe
        # app folder, as before.
        return str(APP_DIR.parent / "Transcripts")
    # Bundled install: the app folder may be read-only, so keep transcripts
    # somewhere the user can find them.
    return str(Path.home() / "Documents" / "Meeting Scribe Transcripts")


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
    # The user's own voice profile (data/voices/<name>.wav), sent to the
    # diarizer with EVERY meeting so the one voice we can identify with high
    # precision — the person recording — is named automatically. Everyone else
    # stays "Speaker A/B" and is named after the meeting via the rename flow.
    # This replaces the old indiscriminate "send the 4 most-recent profiles"
    # behavior, which stamped absent people's names onto whoever sounded closest.
    "self_profile_name": "",        # chosen profile name; "" = not set
    "self_profile_enabled": False,  # master toggle for auto-identifying "me"
    "port": 5723,
    # Meetings up to this duration are diarized in ONE request — the API
    # keeps speaker labels consistent within a request, so no cross-part
    # stitching (the main source of wrongly merged speakers). The API rejects
    # requests over 1400 s of audio regardless of chunking_strategy (verified
    # 2026-08-10: "audio duration ... longer than 1400 seconds which is the
    # maximum for this model"), so this caps just under. Longer meetings fall
    # back to the parallel per-part path below.
    "single_call_max_seconds": 1380,
    # Fallback-path segment length (seconds), used only past the single-call
    # cap. Transcription time scales with part length (a 20-minute part
    # measured >5 min on its own), and the anchor part runs alone before the
    # rest fan out, so oversized parts cost wall-clock twice over. 10 minutes
    # keeps all 4 workers busy while giving the anchor enough coverage to
    # sample most speakers. Voices missed by the anchor now stay separate
    # rather than being folded into someone else (see scribe/reconcile.py).
    "segment_seconds": 600,
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
    "segment_seconds": (1140, 300),
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
