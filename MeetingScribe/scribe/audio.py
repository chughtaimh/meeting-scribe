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


def normalize(src, out_path) -> str:
    """Convert to a single 16 kHz mono Opus (.ogg) file — no segmenting.

    Recordings under the API's per-request duration cap (1400 s) go to the
    diarizer in one request, which keeps speaker labels consistent for the
    entire recording (no cross-part stitching). Longer recordings are
    segmented from this file via segment_copy.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    proc = _run([
        "-hide_banner", "-y", "-i", str(src),
        "-vn", "-ac", "1", "-ar", "16000",
        "-c:a", "libopus", "-b:a", "24k", "-application", "voip",
        str(out_path),
    ])
    if proc.returncode != 0 or not out_path.exists():
        tail = proc.stderr.decode("utf-8", "replace").strip().splitlines()[-3:]
        raise RuntimeError("Audio conversion failed: %s" % " / ".join(tail))
    return str(out_path)


def segment_copy(src_ogg, out_dir, segment_seconds) -> list:
    """Split an already-normalized .ogg into parts of <= segment_seconds.

    Stream copy (no re-encode). Returns ordered part paths; raises on failure
    (the caller may fall back to normalize_and_segment on the original source).
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
        "-hide_banner", "-y", "-i", str(src_ogg),
        "-c", "copy",
        "-f", "segment", "-segment_time", str(int(segment_seconds)),
        "-reset_timestamps", "1",
        pattern,
    ])
    parts = sorted(out_dir.glob("part_*.ogg"))
    if proc.returncode != 0 or not parts:
        tail = proc.stderr.decode("utf-8", "replace").strip().splitlines()[-3:]
        raise RuntimeError("Audio segmenting failed: %s" % " / ".join(tail))

    # Same phantom-tail guard as normalize_and_segment.
    if len(parts) > 1 and probe_duration(parts[-1]) < 0.3:
        try:
            parts[-1].unlink()
        except OSError:
            pass
        parts = parts[:-1]
    return [str(p) for p in parts]


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


def remux_to_mp4(src, dest) -> bool:
    """Best-effort .mov -> .mp4 rewrap so the browser <video> element can play
    it (Chrome refuses video/quicktime). Never raises; returns False when the
    caller should keep the original file.

    ffmpeg will happily stream-copy PCM audio (ipcm) or ProRes video into an
    mp4 that no browser can decode, so "did ffmpeg succeed" is not the test —
    the source codecs are. Video must already be browser-playable (copy only);
    audio is copied when aac/mp3, else re-encoded to AAC (QuickTime PCM).
    """
    dest = Path(dest)
    proc = _run(["-hide_banner", "-i", str(src)], timeout=60)
    text = proc.stderr.decode("utf-8", "replace")
    v = re.search(r": Video: (\w+)", text)
    a = re.search(r": Audio: (\w+)", text)
    if not v or v.group(1) not in ("h264", "hevc", "av1", "vp9"):
        return False
    args = ["-hide_banner", "-y", "-i", str(src), "-c:v", "copy"]
    if v.group(1) == "hevc":
        args += ["-tag:v", "hvc1"]  # Safari needs the hvc1 brand to play HEVC
    if a and a.group(1) in ("aac", "mp3"):
        args += ["-c:a", "copy"]
    else:
        args += ["-c:a", "aac", "-b:a", "160k"]
    proc = _run(args + ["-movflags", "+faststart", str(dest)], timeout=1800)
    if proc.returncode == 0 and dest.exists() and dest.stat().st_size > 0:
        return True
    try:
        dest.unlink()
    except OSError:
        pass
    return False


def check() -> str:
    """Returns ffmpeg version string or raises."""
    proc = _run(["-version"], timeout=30)
    first = proc.stdout.decode("utf-8", "replace").splitlines()
    return first[0] if first else "ffmpeg"
