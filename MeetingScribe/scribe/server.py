"""Flask server — serves the UI and the local API. Binds to 127.0.0.1 only."""

import json
import os
import re
import uuid
from datetime import datetime
from pathlib import Path

from flask import (Flask, jsonify, request, send_file, send_from_directory,
                   abort)

from . import __version__, config, db, jobs, oai, pipeline, search, store

STATIC_DIR = config.APP_DIR / "static"

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = 4 * 1024 * 1024 * 1024  # 4 GB uploads


def _err(msg, code=400):
    return jsonify({"error": str(msg)}), code


@app.errorhandler(Exception)
def _unhandled(e):
    config.log("server error: %r" % e)
    return _err(str(e) or e.__class__.__name__, 500)


# ---------- static ----------

@app.route("/")
def index():
    return send_from_directory(str(STATIC_DIR), "index.html")


@app.route("/static/<path:name>")
def static_files(name):
    return send_from_directory(str(STATIC_DIR), name)


# ---------- state & settings ----------

@app.route("/api/state")
def state():
    cfg = config.load()
    key = cfg.get("openai_api_key") or ""
    now = datetime.now()
    secs = db.month_usage(now.strftime("%Y-%m"))
    return jsonify({
        "app": "meeting-scribe",
        "version": __version__,
        "has_key": bool(key.strip()),
        "key_masked": ("•••" + key.strip()[-4:]) if key.strip() else "",
        "transcripts_dir": str(config.transcripts_dir(cfg)),
        "generate_summaries": bool(cfg.get("generate_summaries")),
        "month_hours": round(secs / 3600.0, 2),
        "month_cost_est": round(secs / 60.0 * 0.006, 2),
        "active_jobs": jobs.active(),
    })


@app.route("/api/settings", methods=["POST"])
def save_settings():
    body = request.get_json(force=True, silent=True) or {}
    updates = {}
    if "openai_api_key" in body:
        k = (body["openai_api_key"] or "").strip()
        if k and not k.startswith("•"):
            updates["openai_api_key"] = k
    if "transcripts_dir" in body:
        p = os.path.expanduser((body["transcripts_dir"] or "").strip())
        if p:
            try:
                Path(p).mkdir(parents=True, exist_ok=True)
            except Exception as e:
                return _err("Cannot create folder: %s" % e)
            updates["transcripts_dir"] = p
    if "generate_summaries" in body:
        updates["generate_summaries"] = bool(body["generate_summaries"])
    cfg = config.save(updates)
    return jsonify({"ok": True, "transcripts_dir": str(config.transcripts_dir(cfg))})


@app.route("/api/settings/test", methods=["POST"])
def test_settings():
    body = request.get_json(force=True, silent=True) or {}
    cfg = config.load()
    k = (body.get("openai_api_key") or "").strip()
    if k and not k.startswith("•"):
        cfg = dict(cfg, openai_api_key=k)
    try:
        res = oai.test_key(cfg)
        return jsonify(res)
    except oai.OAIError as e:
        return _err(str(e), 400)


@app.route("/api/folders")
def list_folders():
    """Tiny directory browser for the folder picker."""
    raw = request.args.get("path") or "~"
    p = Path(os.path.expanduser(raw)).resolve()
    if not p.exists() or not p.is_dir():
        p = Path.home()
    dirs = []
    try:
        for child in sorted(p.iterdir(), key=lambda c: c.name.lower()):
            if child.is_dir() and not child.name.startswith("."):
                dirs.append(child.name)
    except PermissionError:
        pass
    return jsonify({"path": str(p), "parent": str(p.parent) if p != p.parent else "",
                    "dirs": dirs[:400]})


# ---------- recording lifecycle ----------

_EXT_BY_MIME = {
    "audio/webm": ".webm", "audio/ogg": ".ogg", "audio/mp4": ".m4a",
    "audio/mpeg": ".mp3", "audio/wav": ".wav", "audio/x-wav": ".wav",
    "audio/aac": ".m4a", "audio/x-m4a": ".m4a", "audio/flac": ".flac",
}


def _staging_path(rec_id, mime=None, ext=None):
    if not ext:
        base = (mime or "").split(";")[0].strip().lower()
        ext = _EXT_BY_MIME.get(base, ".webm")
    return config.INPROGRESS_DIR / (rec_id + ext)


@app.route("/api/recordings/start", methods=["POST"])
def rec_start():
    body = request.get_json(force=True, silent=True) or {}
    mode = body.get("mode") if body.get("mode") in ("quick", "meeting") else "meeting"
    rec_id = uuid.uuid4().hex[:12]
    config.ensure_dirs()
    # stage with a generic extension now; finish() fixes it based on actual mime
    open(str(_staging_path(rec_id, ext=".part")), "wb").close()
    return jsonify({"id": rec_id, "mode": mode})


@app.route("/api/recordings/<rec_id>/chunk", methods=["POST"])
def rec_chunk(rec_id):
    if not re.fullmatch(r"[0-9a-f]{12}", rec_id):
        return _err("bad id")
    p = _staging_path(rec_id, ext=".part")
    data = request.get_data()
    if data:
        with open(str(p), "ab") as f:
            f.write(data)
    return jsonify({"ok": True, "size": p.stat().st_size})


@app.route("/api/recordings/<rec_id>/finish", methods=["POST"])
def rec_finish(rec_id):
    if not re.fullmatch(r"[0-9a-f]{12}", rec_id):
        return _err("bad id")
    body = request.get_json(force=True, silent=True) or {}
    mode = body.get("mode") if body.get("mode") in ("quick", "meeting") else "meeting"
    part = _staging_path(rec_id, ext=".part")
    if not part.exists() or part.stat().st_size < 200:
        return _err("No audio was captured. Check the microphone permission "
                    "for your browser in System Settings → Privacy & Security.")
    final = _staging_path(rec_id, mime=body.get("mime") or "audio/webm")
    os.replace(str(part), str(final))

    db.upsert_recording({
        "id": rec_id,
        "title": "Processing…",
        "mode": mode,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "duration_s": float(body.get("duration") or 0),
        "folder": "",
        "audio_file": str(final),          # staging path while processing
        "status": "processing", "error": "", "summary": "", "speakers_json": "{}",
    })
    jobs.start(rec_id, lambda job: _run_pipeline(rec_id, job))
    return jsonify({"ok": True, "id": rec_id})


def _run_pipeline(rec_id, job):
    pipeline.process(rec_id, job)
    search.invalidate_cache()


@app.route("/api/recordings/import", methods=["POST"])
def rec_import():
    f = request.files.get("file")
    if not f or not f.filename:
        return _err("No file received")
    mode = request.form.get("mode") if request.form.get("mode") in ("quick", "meeting") else "meeting"
    ext = Path(f.filename).suffix.lower() or ".webm"
    if ext not in (".webm", ".ogg", ".m4a", ".mp3", ".wav", ".mp4", ".aac",
                   ".flac", ".oga", ".opus", ".mov"):
        return _err("Unsupported audio format: %s" % ext)
    rec_id = uuid.uuid4().hex[:12]
    config.ensure_dirs()
    dest = config.INPROGRESS_DIR / (rec_id + (".m4a" if ext in (".mp4", ".mov", ".aac") else ext))
    f.save(str(dest))
    db.upsert_recording({
        "id": rec_id, "title": "Processing…", "mode": mode,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "duration_s": 0, "folder": "", "audio_file": str(dest),
        "status": "processing", "error": "", "summary": "", "speakers_json": "{}",
    })
    jobs.start(rec_id, lambda job: _run_pipeline(rec_id, job))
    return jsonify({"ok": True, "id": rec_id})


# ---------- library, transcripts, jobs ----------

@app.route("/api/recordings")
def rec_list():
    out = db.list_recordings()
    for r in out:
        try:
            r["speakers"] = json.loads(r.pop("speakers_json") or "{}")
        except Exception:
            r["speakers"] = {}
    return jsonify(out)


@app.route("/api/recordings/<rec_id>")
def rec_detail(rec_id):
    rec = db.get_recording(rec_id)
    if not rec:
        return _err("Not found", 404)
    try:
        rec["speakers"] = json.loads(rec.pop("speakers_json") or "{}")
    except Exception:
        rec["speakers"] = {}
    rec["turns"] = []
    if rec.get("status") == "done" and rec.get("folder"):
        try:
            data = store.read_transcript(rec["folder"])
            rec["turns"] = data.get("turns") or []
            rec["speakers"] = data.get("speakers") or rec["speakers"]
            rec["post_meeting"] = data.get("post_meeting")
        except Exception as e:
            rec["turns_error"] = str(e)
    rec["job"] = jobs.get(rec_id)
    return jsonify(rec)


@app.route("/api/recordings/<rec_id>", methods=["PATCH"])
def rec_patch(rec_id):
    body = request.get_json(force=True, silent=True) or {}
    if "title" in body:
        title = store.update_title(rec_id, body["title"])
        return jsonify({"ok": True, "title": title})
    return _err("Nothing to update")


@app.route("/api/recordings/<rec_id>", methods=["DELETE"])
def rec_delete(rec_id):
    delete_files = request.args.get("files") == "1"
    store.delete_recording(rec_id, delete_files=delete_files)
    search.invalidate_cache()
    return jsonify({"ok": True})


@app.route("/api/recordings/<rec_id>/retry", methods=["POST"])
def rec_retry(rec_id):
    """Re-run the pipeline for a failed recording (audio is still staged)."""
    rec = db.get_recording(rec_id)
    if not rec:
        return _err("Not found", 404)
    # "Processing" only counts if a live job actually exists — a stale db row
    # left behind by a restart must stay retryable.
    j = jobs.get(rec_id)
    if rec.get("status") == "processing" and j and not j.get("done"):
        return _err("Already processing")
    src = Path(rec.get("audio_file") or "")
    if rec.get("status") == "done" or not src.is_absolute() or not src.exists():
        return _err("This recording has no saved audio to retry.")
    db.update_recording(rec_id, status="processing", error="")
    jobs.start(rec_id, lambda job: _run_pipeline(rec_id, job))
    return jsonify({"ok": True})


@app.route("/api/recordings/<rec_id>/speakers", methods=["POST"])
def rec_speakers(rec_id):
    body = request.get_json(force=True, silent=True) or {}
    mapping = body.get("speakers") or {}
    if not isinstance(mapping, dict):
        return _err("speakers must be an object")
    speakers = store.rename_speakers(rec_id, mapping)
    return jsonify({"ok": True, "speakers": speakers})


@app.route("/api/recordings/<rec_id>/audio")
def rec_audio(rec_id):
    rec = db.get_recording(rec_id)
    if not rec:
        abort(404)
    if rec.get("status") == "done" and rec.get("folder"):
        p = Path(rec["folder"]) / (rec.get("audio_file") or "")
    else:
        p = Path(rec.get("audio_file") or "")
    if not p.exists():
        abort(404)
    return send_file(str(p), conditional=True)


@app.route("/api/recordings/<rec_id>/file/<kind>")
def rec_file(rec_id, kind):
    rec = db.get_recording(rec_id)
    if not rec or not rec.get("folder"):
        abort(404)
    name = {"md": "transcript.md", "json": "transcript.json"}.get(kind)
    if not name:
        abort(404)
    p = Path(rec["folder"]) / name
    if not p.exists():
        abort(404)
    fname = "%s.%s" % (store.safe_name(rec.get("title") or "transcript"),
                       "md" if kind == "md" else "json")
    return send_file(str(p), as_attachment=True, download_name=fname)


@app.route("/api/jobs/<rec_id>")
def job_status(rec_id):
    j = jobs.get(rec_id)
    rec = db.get_recording(rec_id)
    return jsonify({"job": j, "status": (rec or {}).get("status"),
                    "error": (rec or {}).get("error"),
                    "title": (rec or {}).get("title")})


# ---------- search & maintenance ----------

@app.route("/api/search")
def do_search():
    q = request.args.get("q") or ""
    return jsonify(search.search(q))


@app.route("/api/reindex", methods=["POST"])
def reindex():
    res = store.rescan()
    search.invalidate_cache()
    embedded = 0
    try:
        embedded = pipeline.reembed_missing()
        search.invalidate_cache()
    except Exception as e:
        config.log("re-embed during reindex failed: %s" % e)
    res["embedded_chunks"] = embedded
    return jsonify(res)


@app.route("/api/quit", methods=["POST"])
def quit_app():
    """Shut down the local server (used by the Settings page)."""
    def die():
        import time
        time.sleep(0.6)
        os._exit(0)
    import threading
    threading.Thread(target=die, daemon=True).start()
    return jsonify({"ok": True})


def _cleanup_stale():
    """Remove abandoned in-progress files older than 3 days."""
    import time
    cutoff = time.time() - 3 * 86400
    try:
        for p in config.INPROGRESS_DIR.glob("*"):
            try:
                if p.stat().st_mtime < cutoff:
                    p.unlink()
            except OSError:
                pass
    except Exception:
        pass


def _recover_orphans():
    """After a restart, db rows can claim 'processing' although their job
    thread died with the old process. Mark them as retryable errors so the
    UI shows a Retry button instead of a zombie spinner."""
    try:
        for r in db.list_recordings():
            if r.get("status") == "processing" and not jobs.get(r["id"]):
                db.update_recording(
                    r["id"], status="error",
                    error="Processing was interrupted (app restarted). "
                          "Press Retry to run it again.")
                config.log("recovered orphaned job %s -> retryable" % r["id"])
    except Exception as e:
        config.log("orphan recovery failed: %s" % e)


def create_app():
    db.init()
    config.ensure_dirs()
    _cleanup_stale()
    _recover_orphans()
    return app
