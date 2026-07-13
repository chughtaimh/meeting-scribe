"""One-time migration: recover mislabeled video recordings.

Imports used to rename uploaded .mp4/.mov files to .m4a, so the full video
sits in the transcript folder as audio.m4a and the UI shows an audio player.
This script finds every audio.m4a that actually contains a video stream,
remuxes it losslessly to audio.mp4 (+faststart), and updates transcript.json
and the SQLite row so the transcript page shows the video player.

Safe to re-run; recordings without video are untouched.

    cd MeetingScribe && .venv/bin/python -m tools.recover_video [--dry-run]
"""

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scribe import audio, config, db  # noqa: E402


def has_video_stream(path: Path) -> bool:
    proc = audio._run(["-hide_banner", "-i", str(path)], timeout=60)
    return bool(re.search(r": Video: ", proc.stderr.decode("utf-8", "replace")))


def main(dry_run=False):
    db.init()
    base = config.transcripts_dir()
    fixed = skipped = failed = 0
    for tj in sorted(base.glob("*/transcript.json")):
        folder = tj.parent
        src = folder / "audio.m4a"
        if not src.exists() or not has_video_stream(src):
            continue
        data = json.loads(tj.read_text(encoding="utf-8"))
        rec_id = data.get("id") or ""
        print("video found: %s" % folder.name)
        if dry_run:
            fixed += 1
            continue
        dest = folder / "audio.mp4"
        if not audio.remux_to_mp4(src, dest):
            # Codecs the browser can't play (rare) — keep the m4a untouched.
            print("  ! remux failed (unsupported codecs) — left as audio.m4a")
            failed += 1
            continue
        data["audio_file"] = "audio.mp4"
        tmp = folder / "transcript.json.tmp"
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        tmp.replace(tj)
        if rec_id and db.get_recording(rec_id):
            db.update_recording(rec_id, audio_file="audio.mp4")
        src.unlink()
        print("  -> audio.mp4 (%.1f MB)" % (dest.stat().st_size / 1e6))
        fixed += 1
    print("done: %d recovered, %d failed, %d others untouched"
          % (fixed, failed, skipped))


if __name__ == "__main__":
    main(dry_run="--dry-run" in sys.argv[1:])
