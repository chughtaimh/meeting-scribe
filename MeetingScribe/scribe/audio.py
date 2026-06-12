"""Audio utilities built on ffmpeg (system ffmpeg or the bundled imageio-ffmpeg binary)."""

import re
import shutil
import subprocess
from pathlib import Path

from . import config

_FFMPEG = None


def ffmpeg_path() -> str:
    global _FFMPEG
    if _FFMPEG:
        return _FFMPEG
    p = shutil.which("ffmpeg")
    if not p:
        try:
            import imageio_ffmpeg
            p = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception as e:
            raise RuntimeError("ffmpeg is not available: %s" % e)
    _FFMPEG = p
    return p


def _run(args, timeout=900):
    proc = subprocess.run(
        [ffmpeg_path()] + args,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    return proc


def _hms_to_seconds(h, m, s) -> float:
    return int(h) * 3600 + int(m) * 60 + float(s)


def probe_duration(path) -> float:
    """Duration in seconds.

    Browser MediaRecorder files (WebM from Chrome, fragmented MP4 from Safari)
    are written as live streams and often carry NO duration in the header —
    ffmpeg reports 'Duration: N/A'. So: try the header first, then fall back to
    decoding the stream and reading the last progress timestamp, which gives
    the true length for any playable file.
    """
    # 1) Fast path: header metadata
    proc = _run(["-hide_banner", "-i", str(path)], timeout=60)
    text = proc.stderr.decode("utf-8", "replace")
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", text)
    if m:
        return _hms_to_seconds(m.group(1), m.group(2), m.group(3))

    # 2) Robust path: decode to null and take the final time= progress stamp
    proc = _run(["-hide_banner", "-v", "info", "-i", str(path),
                 "-vn", "-f", "null", "-"], timeout=600)
    text = proc.stderr.decode("utf-8", "replace")
    stamps = re.findall(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)", text)
    if stamps:
        return _hms_to_seconds(*stamps[-1])
    return 0.0


def normalize_and_segment(src, out_dir, segment_seconds) -> list:
    """Convert to 16 kHz mono Opus (.ogg) and split into parts of <= segment_seconds.

    One ffmpeg pass does both. Returns ordered list of part paths.
    Small files yield a single part. Opus @24kbps keeps each ~20-min part ~3.5 MB,
    far below API upload limits.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("part_*.ogg"):
        try:
            old.unlink()
        except OSError:
            pass
    pattern = str(out_dir / "part_%03d.ogg")
    proc = _run([
        "-hide_banner", "-y", "-i", str(src),
        "-vn", "-ac", "1", "-ar", "16000",
        "-c:a", "libopus", "-b:a", "24k", "-application", "voip",
        "-f", "segment", "-segment_time", str(int(segment_seconds)),
        "-reset_timestamps", "1",
        pattern,
    ])
    parts = sorted(out_dir.glob("part_*.ogg"))
    if proc.returncode != 0 or not parts:
        # Fallback: try a plain single-file conversion (some inputs dislike the
        # segment muxer); if even that fails, surface ffmpeg's error.
        single = out_dir / "part_000.ogg"
        proc2 = _run([
            "-hide_banner", "-y", "-i", str(src),
            "-vn", "-ac", "1", "-ar", "16000",
            "-c:a", "libopus", "-b:a", "24k", str(single),
        ])
        if proc2.returncode != 0:
            tail = proc.stderr.decode("utf-8", "replace").strip().splitlines()[-3:]
            raise RuntimeError("Audio conversion failed: %s" % " / ".join(tail))
        parts = [single]

    # The segment muxer can emit a phantom few-millisecond tail part when the
    # source length is an exact multiple of segment_time — drop it (nothing
    # speakable fits in <0.3s, and it would waste an API call).
    if len(parts) > 1 and probe_duration(parts[-1]) < 0.3:
        try:
            parts[-1].unlink()
        except OSError:
            pass
        parts = parts[:-1]
    return [str(p) for p in parts]


def extract_clip(src, start_s: float, dur_s: float, out_path) -> str:
    """Extract a short clip (16 kHz mono WAV) — used as a known-speaker reference."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    proc = _run([
        "-hide_banner", "-y",
        "-ss", "%.2f" % max(0.0, start_s), "-t", "%.2f" % dur_s,
        "-i", str(src),
        "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
        str(out_path),
    ], timeout=120)
    if proc.returncode != 0 or not out_path.exists():
        raise RuntimeError("Could not extract speaker reference clip")
    return str(out_path)


def check() -> str:
    """Returns ffmpeg version string or raises."""
    proc = _run(["-version"], timeout=30)
    first = proc.stdout.decode("utf-8", "replace").splitlines()
    return first[0] if first else "ffmpeg"
