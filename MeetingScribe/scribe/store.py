"""Transcript folder I/O. Each recording gets its own folder inside the
transcripts directory:

    2026-06-12 14.30 - Quarterly Partner Review/
        audio.webm            (original recording)
        transcript.json       (machine-readable; source of truth)
        transcript.md         (human-readable)

The SQLite index can always be rebuilt from these folders (rescan).
"""

import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path

from . import config, db


def safe_name(s: str, fallback="Recording") -> str:
    s = re.sub(r'[\\/:*?"<>|\r\n]+', " ", s or "").strip()
    s = re.sub(r"\s+", " ", s)
    return (s[:80].strip() or fallback)


def make_folder(created_at: datetime, title: str) -> Path:
    base = config.transcripts_dir()
    stamp = created_at.strftime("%Y-%m-%d %H.%M")
    name = "%s - %s" % (stamp, safe_name(title))
    folder = base / name
    n = 2
    while folder.exists():
        folder = base / ("%s - %s (%d)" % (stamp, safe_name(title), n))
        n += 1
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def fmt_ts(seconds) -> str:
    if seconds is None:
        return ""
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return ("%d:%02d:%02d" % (h, m, sec)) if h else ("%d:%02d" % (m, sec))


def write_transcript_files(folder: Path, meta: dict, turns: list):
    """meta: {id, title, mode, created_at, duration_s, audio_file, summary,
              speakers: {label: display}}
       turns: [{seq, speaker, start_s, end_s, text}]"""
    folder = Path(folder)
    data = dict(meta)
    data["turns"] = turns
    tmp = folder / "transcript.json.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, folder / "transcript.json")

    lines = ["# %s" % (meta.get("title") or "Transcript"), ""]
    created = (meta.get("created_at") or "").replace("T", " ")[:16]
    dur = fmt_ts(meta.get("duration_s"))
    info = " · ".join(x for x in [created, dur,
                      "Meeting" if meta.get("mode") == "meeting" else "Voice note"] if x)
    lines += [info, ""]
    if meta.get("summary"):
        lines += [meta["summary"].strip(), "", "---", "", "## Transcript", ""]
    speakers = meta.get("speakers") or {}
    cleanup_meta = meta.get("cleanup") or {}
    if cleanup_meta:
        lines += ["*Cleaned transcript — fragments merged, glossary applied; "
                  "the raw transcript is preserved in transcript.json.*", ""]

    # Interleave removed-background markers chronologically with the turns.
    events = [("turn", t.get("start_s") or 0, t) for t in turns]
    for r in cleanup_meta.get("removed") or []:
        events.append(("removed", r.get("start_s") or 0, r))
    events.sort(key=lambda e: e[1])
    for kind, _, obj in events:
        if kind == "turn":
            t = obj
            ts = fmt_ts(t.get("start_s"))
            if t.get("speaker"):
                name = speakers.get(t["speaker"], "Speaker %s" % t["speaker"])
                lines.append("**%s** [%s]: %s" % (name, ts, t["text"]))
            else:
                lines.append(t["text"])
        else:
            lines.append("*[%s — %s removed: %s]*"
                         % (fmt_ts(obj.get("start_s")),
                            obj.get("reason") or "speech",
                            obj.get("text") or ""))
        lines.append("")

    if cleanup_meta:
        lines += ["---", "", "## Corrections & flags", ""]
        if cleanup_meta.get("integrity"):
            lines += ["Integrity: %s" % cleanup_meta["integrity"], ""]
        for r in cleanup_meta.get("reassigned") or []:
            lines.append("- **%s** — reassigned %s → %s (%s)"
                         % (fmt_ts(r.get("start_s")), r.get("from"),
                            r.get("to"), r.get("why") or "by context"))
        for r in cleanup_meta.get("term_fixes") or []:
            lines.append("- **%s** — “%s” → “%s”"
                         % (fmt_ts(r.get("start_s")), r.get("from"), r.get("to")))
        for r in cleanup_meta.get("flags") or []:
            lines.append("- **%s** — ⚑ %s" % (fmt_ts(r.get("start_s")),
                                              r.get("note")))
        lines.append("")

    post = meta.get("post_meeting") or {}
    if post.get("turns"):
        end_ts = fmt_ts(post.get("meeting_end_s"))
        lines += ["---", "", "## After the meeting", "",
                  "*The recording kept running after the meeting ended"
                  + (" (~%s)" % end_ts if end_ts else "")
                  + ". The turns below are excluded from the summary and "
                    "from search. Delete this recording's files if they "
                    "shouldn't be kept.*", ""]
        for t in post["turns"]:
            ts = fmt_ts(t.get("start_s"))
            if t.get("speaker"):
                name = speakers.get(t["speaker"], "Speaker %s" % t["speaker"])
                lines.append("**%s** [%s]: %s" % (name, ts, t["text"]))
            else:
                lines.append(t["text"])
            lines.append("")
    with open(folder / "transcript.md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines).strip() + "\n")


def read_transcript(folder) -> dict:
    with open(Path(folder) / "transcript.json", "r", encoding="utf-8") as f:
        return json.load(f)


def index_recording(meta: dict, turns: list) -> list:
    """(Re)index one recording into SQLite. Returns new chunk ids."""
    speakers = meta.get("speakers") or {}
    names = {k: v for k, v in speakers.items()}
    chunks = []
    for t in turns:
        for piece in split_text(t["text"]):
            chunks.append({
                "seq": len(chunks),
                "speaker": t.get("speaker") or "",
                "start_s": t.get("start_s"),
                "end_s": t.get("end_s"),
                "text": piece,
            })
    db.upsert_recording({
        "id": meta["id"], "title": meta.get("title"), "mode": meta.get("mode"),
        "created_at": meta.get("created_at"), "duration_s": meta.get("duration_s") or 0,
        "folder": str(meta.get("folder")), "audio_file": meta.get("audio_file"),
        "status": meta.get("status", "done"), "error": meta.get("error", ""),
        "summary": meta.get("summary", ""),
        "speakers_json": json.dumps(speakers, ensure_ascii=False),
    })
    return db.replace_chunks(meta["id"], chunks, names)


def split_text(text: str, target=700) -> list:
    """Split long text at sentence-ish boundaries for indexing/embedding."""
    text = (text or "").strip()
    if len(text) <= target:
        return [text] if text else []
    parts, cur = [], ""
    for sent in re.split(r"(?<=[.!?])\s+", text):
        if cur and len(cur) + len(sent) + 1 > target:
            parts.append(cur.strip())
            cur = sent
        else:
            cur = (cur + " " + sent).strip()
    if cur.strip():
        parts.append(cur.strip())
    return parts


def _resummarize_labels(summary: str, renamed: dict) -> str:
    """Substitute literal "Speaker <label>" tokens in a pre-baked summary with
    corrected display names. ``renamed`` maps {label: display name} and should
    contain ONLY labels just renamed to a real name (display not starting with
    "Speaker"); un-renamed speakers and self-persona real-name keys produce no
    such token and are left untouched. Matching is whole-token and
    boundary-safe (so "Speaker A" never bites into "Speaker AB"), longest
    label first. Labels may be multi-char ("S2", "X1") — never assume A-Z.
    Pure and side-effect free.

    This fixes "Speaker X -> real name". It does NOT rewrite a name that is
    already real (e.g. re-editing a self-persona's name): no "Speaker <label>"
    token exists for it. The manual "Regenerate notes" action covers that case.
    """
    if not summary or not renamed:
        return summary
    out = summary
    for label in sorted(renamed, key=len, reverse=True):
        name = (renamed.get(label) or "").strip()
        if not name:
            continue
        pat = re.compile(r"\bSpeaker[ \t\u00a0]+" + re.escape(label)
                         + r"(?![A-Za-z0-9])")
        out = pat.sub(lambda m, n=name: n, out)
    return out


def rename_speakers(rec_id: str, mapping: dict) -> dict:
    """mapping: {label: new display name}. Updates JSON, MD and search index,
    and flows the corrected names into the pre-baked summary via instant
    zero-cost token substitution. Returns {"speakers": {...}, "summary": ...}
    so callers can refresh the UI without a full reload."""
    rec = db.get_recording(rec_id)
    if not rec:
        raise KeyError("Recording not found")
    folder = Path(rec["folder"])
    data = read_transcript(folder)
    speakers = data.get("speakers") or {}
    for label, name in mapping.items():
        name = (name or "").strip()
        if name:
            speakers[label] = name[:60]
    data["speakers"] = speakers

    # Instant, zero-cost notes fix: substitute "Speaker <label>" tokens in the
    # stored summary for labels renamed THIS call to a real name, so the AI
    # notes stop contradicting the (already re-rendered) transcript body.
    # Empty summaries (quick/voice notes) are left alone.
    old_summary = data.get("summary") or ""
    renamed = {lab: speakers[lab] for lab in mapping
               if lab in speakers and not speakers[lab].lower().startswith("speaker")}
    summary = _resummarize_labels(old_summary, renamed)
    if summary != old_summary:
        data["summary"] = summary

    write_transcript_files(folder, {k: v for k, v in data.items() if k != "turns"},
                           data.get("turns") or [])
    db.update_recording(rec_id, speakers_json=json.dumps(speakers, ensure_ascii=False))
    if summary != old_summary:
        db.update_recording(rec_id, summary=summary)
    db.refresh_fts_speakers(rec_id, speakers)

    # A confirmed rename teaches the system: the name joins the glossary and
    # (best-effort) a voice profile is harvested so the diarizer can anchor
    # this person from the first second of future meetings.
    try:
        from . import glossary, profiles
        source_turns = data.get("raw_turns") or data.get("turns") or []
        for label, name in mapping.items():
            nm = (name or "").strip()
            if nm and not nm.lower().startswith("speaker"):
                glossary.add_name(nm)
                profiles.harvest_from_recording(nm, label, folder, source_turns)
    except Exception as e:
        config.log("post-rename glossary/profile update failed: %s" % e)
    return {"speakers": speakers, "summary": summary}


def _allocate_label(taken) -> str:
    """Fresh speaker label following the pipeline convention: A-Z, then S<n>."""
    for c in (chr(x) for x in range(ord("A"), ord("Z") + 1)):
        if c not in taken:
            return c
    n = 2
    while ("S%d" % n) in taken:
        n += 1
    return "S%d" % n


def reassign_turns(rec_id: str, seqs, to_label=None, new_name=None,
                   section="main") -> dict:
    """Move the given turns to another speaker — the manual fix for turns the
    diarizer attributed to the wrong person (e.g. two people fused under one
    label).

    ``seqs`` index the chosen section's turn list ("main" or "post"). Assign
    to an existing label via ``to_label``, or allocate a fresh label
    (displayed as ``new_name`` when given). Only ``speaker`` fields change:
    no seq renumbering and no turn re-merging, so repeated edits stay
    predictable. Returns {"speakers", "turns", "to_label"} for in-place UI
    refresh.
    """
    rec = db.get_recording(rec_id)
    if not rec:
        raise KeyError("Recording not found")
    if (rec.get("mode") != "meeting" or rec.get("status") != "done"
            or not rec.get("folder")):
        raise ValueError("Only completed meetings can be edited")
    folder = Path(rec["folder"])
    data = read_transcript(folder)
    turns = data.get("turns") or []
    raw_turns = data.get("raw_turns") or []
    post = (data.get("post_meeting") or {}).get("turns") or []
    target_list = post if section == "post" else turns
    by_seq = {t.get("seq"): t for t in target_list}
    seqs = list(dict.fromkeys(int(s) for s in seqs))
    missing = [s for s in seqs if s not in by_seq]
    if not seqs or missing:
        raise ValueError("Unknown turn(s): %s"
                         % (", ".join(map(str, missing)) or "none given"))

    speakers = data.get("speakers") or {}
    if to_label:
        if to_label not in speakers:
            raise ValueError("Unknown speaker %r" % to_label)
        target = to_label
    else:
        taken = set(speakers)
        for t in turns + raw_turns + post:
            if t.get("speaker"):
                taken.add(t["speaker"])
        target = _allocate_label(taken)
        display = (new_name or "").strip()[:60]
        speakers[target] = display or ("Speaker %s" % target)

    edited = []  # (old_label, start_s, end_s) of each turn actually moved
    for s in seqs:
        t = by_seq[s]
        if t.get("speaker") != target:
            edited.append((t.get("speaker"), float(t.get("start_s") or 0.0),
                           float(t.get("end_s") or 0.0)))
            t["speaker"] = target

    # Keep raw_turns consistent so voice-profile harvesting (which prefers
    # raw_turns) follows the corrected attribution. Raw and cleaned turn
    # boundaries differ, so match by old label + time overlap (midpoint).
    if raw_turns and section == "main":
        for rt in raw_turns:
            mid = (float(rt.get("start_s") or 0.0)
                   + float(rt.get("end_s") or 0.0)) / 2.0
            for old, a, b in edited:
                if rt.get("speaker") == old and a <= mid <= b:
                    rt["speaker"] = target
                    break

    # Drop labels nothing references any more — keeps the legend honest when
    # the last turn leaves a speaker.
    referenced = {t["speaker"] for t in turns + raw_turns + post
                  if t.get("speaker")}
    for lab in list(speakers):
        if lab not in referenced:
            speakers.pop(lab)

    data["speakers"] = speakers
    meta = {k: v for k, v in data.items() if k != "turns"}
    write_transcript_files(folder, meta, turns)
    # Chunk speaker labels changed, so rebuild the chunks + FTS rows (a
    # speakers_json update alone would leave stale labels in the index).
    index_recording(meta, turns)

    # Rebuilding chunks drops their embeddings; restore them best-effort.
    try:
        from . import oai
        cfg = config.load()
        missing_vecs = db.chunks_missing_vectors(
            rec_id, min_chars=int(cfg.get("search_min_semantic_chars") or 0))
        if missing_vecs:
            vecs = oai.embed(cfg, [txt for _, txt in missing_vecs])
            db.store_vectors(list(zip([cid for cid, _ in missing_vecs], vecs)))
    except Exception as e:
        config.log("re-embed after reassign failed (keyword search still "
                   "works): %s" % e)

    # Refresh voice profiles for real-named speakers on either side of the
    # move, so a profile harvested from a now-moved turn gets corrected.
    try:
        from . import profiles
        source_turns = raw_turns or turns
        for lab in {old for old, _, _ in edited} | {target}:
            nm = (speakers.get(lab) or "").strip()
            if nm and not nm.lower().startswith("speaker"):
                profiles.harvest_from_recording(nm, lab, folder, source_turns)
    except Exception as e:
        config.log("post-reassign profile update failed: %s" % e)

    return {"speakers": speakers, "turns": turns, "to_label": target}


def update_title(rec_id: str, title: str) -> str:
    rec = db.get_recording(rec_id)
    if not rec:
        raise KeyError("Recording not found")
    title = safe_name(title, fallback=rec.get("title") or "Recording")
    folder = Path(rec["folder"])
    data = read_transcript(folder)
    data["title"] = title
    write_transcript_files(folder, {k: v for k, v in data.items() if k != "turns"},
                           data.get("turns") or [])
    db.update_recording(rec_id, title=title)
    return title


def update_summary(rec_id: str, summary: str) -> str:
    """Persist a (re)generated summary to transcript.json, transcript.md and the
    recordings cache. The title is intentionally NOT touched here — it is edited
    independently (see update_title). Returns the stored summary."""
    rec = db.get_recording(rec_id)
    if not rec:
        raise KeyError("Recording not found")
    folder = Path(rec["folder"])
    data = read_transcript(folder)
    data["summary"] = summary
    write_transcript_files(folder, {k: v for k, v in data.items() if k != "turns"},
                           data.get("turns") or [])
    db.update_recording(rec_id, summary=summary)
    return summary


def delete_recording(rec_id: str, delete_files=False):
    rec = db.get_recording(rec_id)
    db.delete_recording(rec_id)
    if rec and delete_files:
        folder = Path(rec["folder"])
        base = config.transcripts_dir()
        try:
            folder.resolve().relative_to(base.resolve())  # safety: only inside base
            shutil.rmtree(str(folder), ignore_errors=True)
        except Exception:
            pass


def rescan() -> dict:
    """Rebuild the whole index from transcript.json files on disk."""
    base = config.transcripts_dir()
    db.wipe_index()
    found = 0
    for tj in sorted(base.glob("*/transcript.json")):
        try:
            data = read_transcript(tj.parent)
            data["folder"] = str(tj.parent)
            data.setdefault("status", "done")
            meta = {k: v for k, v in data.items() if k != "turns"}
            index_recording(meta, data.get("turns") or [])
            found += 1
        except Exception as e:
            config.log("rescan skipped %s: %s" % (tj, e))
    return {"recordings": found}
