# Backlog — deferred ideas and the reasoning behind them

## Orphan recovery is unsafe with two instances
`_recover_orphans` (server.py) runs at every startup and marks any
`processing` row with no in-process job as a retryable error. With a second
instance sharing the DB (e.g. the port-5799 test server), a fresh startup
falsely "recovers" jobs that are alive and well in the other process — the
user sees "Processing was interrupted" and a Retry button that would launch a
duplicate concurrent run on the same staging file. Happened live on
2026-07-08 (a41c593dd459). Fix idea: stamp rows with a process/heartbeat id
(e.g. `jobs` table row updated every N seconds) and only recover rows whose
heartbeat is stale, or gate recovery behind a "no other instance holds the
DB" check. Until then: never start a second instance while a job is running.

## Probe uploads for a real video stream
Today a `.mp4`/`.mov` upload is trusted to be video (extension is the signal the
frontend uses to render `<video>` vs `<audio>`). An audio-only `.mp4`, or an
audio file whose cover art shows up as an mjpeg "attached pic" video stream,
would render a black video box. If that ever annoys anyone: probe with ffmpeg
(parse `ffmpeg -i` stderr for `Stream #N…: Video:` lines, ignoring
`attached pic` / mjpeg-png-bmp-gif codecs — same stderr-parsing pattern as
`audio.probe_duration`) and fall back to the audio player.
Deferred deliberately: this is a personal/local app with no adversarial users,
and files named `.mp4`/`.mov` are videos in practice.

## Transcode non-web codecs (ProRes, exotic .mov)
`.mov` uploads are remuxed to `.mp4` at save time (`audio.remux_to_mp4`):
the source codecs are sniffed from `ffmpeg -i` stderr, video is stream-copied
only when browser-playable (h264/hevc/av1/vp9), audio is copied when aac/mp3
else re-encoded to AAC (QuickTime PCM). A `.mov` with a non-web video codec
(e.g. ProRes) is kept as `.mov` — plays in Safari, not Chrome; transcription
is unaffected. Note: "did ffmpeg succeed" is NOT a valid playability test —
modern ffmpeg happily writes PCM (ipcm) and ProRes into mp4 containers that
no browser can decode; that's why the codec sniff exists.
If needed: full transcode `-c:v libx264 -preset veryfast -crf 26 -pix_fmt
yuv420p -vf "scale='min(1280,iw)':-2" -c:a aac -movflags +faststart`, ideally
kicked off in parallel with transcription so the wall-time hides behind the
API calls.

## ~~Rescue video imports made before video support landed~~ — DONE 2026-07-13
`.mp4`/`.mov` files imported before this change were renamed to `audio.m4a` at
rest — the video track is still inside the container, only the name lied.
Automated in `tools/recover_video.py` (probe every audio.m4a for a video
stream, lossless remux to audio.mp4 +faststart, update transcript.json + db
row). Ran on 2026-07-13: 4 recordings recovered, 0 failed. Safe to re-run.
