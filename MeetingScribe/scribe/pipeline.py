"""Recording -> transcript pipeline.

Meeting mode:
  normalize audio -> split into ~5-minute parts -> diarized transcription of
  the parts IN PARALLEL -> merge into speaker turns -> cleanup pass ->
  AI title/summary -> save files -> index for keyword + vector search.

Speaker consistency across parallel parts ("anchor-then-fan-out"):
  every part must be told who the speakers are via known-speaker reference
  clips, otherwise each part would label voices independently. If stored
  voice profiles exist they are the references and ALL parts run in parallel
  immediately. Otherwise part 1 runs alone first ("anchor") to harvest one
  clip per speaker, then the remaining parts fan out in parallel with those
  same references. A speaker who first appears after the anchor part may
  occasionally split into two labels (renaming both to the same name merges
  them) — the price of parallelism, and rare in practice.

Quick mode: all parts in parallel, no diarization.
"""

import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from . import audio, cleanup, config, db, oai, profiles, reconcile, store


def _fallback_title(mode: str, created: datetime, text: str) -> str:
    if mode == "quick" and text:
        words = text.split()
        if words:
            t = " ".join(words[:7])
            return t + ("…" if len(words) > 7 else "")
    label = "Meeting" if mode == "meeting" else "Voice note"
    return "%s %s" % (label, created.strftime("%b %-d, %H:%M")
                      if hasattr(created, "strftime") else "")


def _merge_turns(segments, max_chars=1200, max_gap_s=30.0):
    """Merge consecutive same-speaker segments into readable turns.
    A silence gap longer than max_gap_s always breaks the turn — long gaps
    are meaningful (pauses, meeting end) and must stay visible."""
    turns = []
    for seg in segments:
        if (turns and turns[-1]["speaker"] == seg["speaker"]
                and seg["start"] - turns[-1]["end_s"] <= max_gap_s
                and len(turns[-1]["text"]) + len(seg["text"]) + 1 <= max_chars):
            turns[-1]["text"] += " " + seg["text"]
            turns[-1]["end_s"] = seg["end"]
        else:
            turns.append({
                "seq": len(turns),
                "speaker": seg["speaker"],
                "start_s": seg["start"],
                "end_s": seg["end"],
                "text": seg["text"],
            })
    for i, t in enumerate(turns):
        t["seq"] = i
    return turns


class _SpeakerTracker:
    """Keeps global speaker labels consistent across audio parts.

    Three label classes — the distinction is what prevents collisions:
      named        real names backed by stored voice profiles
      letters      voices anchored by clips harvested from the anchor part
      provisional  "P<part>:<local>" — unmatched voices, scoped to ONE part;
                   the reconcile pass decides (with evidence) whether
                   provisionals from different parts are the same person.

    Reference clips are sent under DISTINCTIVE names ("Speaker A", real
    names) so a bare letter coming back from the API is always recognizably
    an unmatched voice, never silently fused with an existing speaker.
    """

    LETTERS = [chr(c) for c in range(ord("A"), ord("Z") + 1)]

    def __init__(self, known_names=None):
        self.named = set(known_names or [])   # profile-backed real names
        self.letters = []                     # anchor-clip-backed labels
        self.samples = {}                     # letter -> wav clip path
        self.talk_time = {}
        self.provisional = set()

    def _new_letter(self):
        for cand in self.LETTERS:
            if cand not in self.letters:
                self.letters.append(cand)
                return cand
        cand = "S%d" % (len(self.letters) + 1)
        self.letters.append(cand)
        return cand

    def _tally(self, segments):
        for s in segments:
            self.talk_time[s["speaker"]] = (self.talk_time.get(s["speaker"], 0)
                                            + max(0.0, s["end"] - s["start"]))

    def map_anchor(self, segments):
        """Anchor (or single-part) mapping: fresh letters, first-seen order."""
        mapping = {}
        for s in segments:
            if s["speaker"] not in mapping:
                mapping[s["speaker"]] = self._new_letter()
            s["speaker"] = mapping[s["speaker"]]
        self._tally(segments)

    def map_part(self, segments, part_idx: int, ref_names: dict):
        """Fan-out part mapping. ref_names: {name sent with the reference
        clip -> global label}. Anything else is provisional to this part."""
        for s in segments:
            lab = s["speaker"]
            if lab in ref_names:
                s["speaker"] = ref_names[lab]
            else:
                prov = "P%d:%s" % (part_idx, lab)
                self.provisional.add(prov)
                s["speaker"] = prov
        self._tally(segments)

    def collect_samples(self, segments, part_path, part_offset, tmp_dir):
        """Extract one clean reference clip per speaker (from this part's file)."""
        best = {}
        for s in segments:
            g = s["speaker"]
            if g in self.samples:
                continue
            dur = s["end"] - s["start"]
            if dur >= 3.0 and (g not in best or dur > best[g][1]):
                best[g] = (s["start"] - part_offset, dur)
        for g, (local_start, dur) in best.items():
            try:
                clip = Path(tmp_dir) / ("spk_%s.wav" % g)
                audio.extract_clip(part_path, local_start + 0.2,
                                   min(8.0, dur - 0.3), clip)
                self.samples[g] = str(clip)
            except Exception as e:
                config.log("speaker sample for %s failed: %s" % (g, e))

    def known_speakers(self):
        """Top-4 most talkative speakers that have reference clips."""
        ranked = sorted(self.samples.keys(), key=lambda g: -self.talk_time.get(g, 0))
        return [(g, self.samples[g]) for g in ranked[:4]]


def process(rec_id: str, job):
    cfg = config.load()
    rec = db.get_recording(rec_id)
    if not rec:
        raise RuntimeError("Recording %s not found" % rec_id)
    src = Path(rec["audio_file"])  # while processing this holds the staging path
    mode = rec["mode"]
    created = datetime.fromisoformat(rec["created_at"])
    tmp_dir = config.TMP_DIR / rec_id
    tmp_dir.mkdir(parents=True, exist_ok=True)

    try:
        job.set(stage="preparing", detail="Preparing audio…", pct=4)
        if not src.exists() or src.stat().st_size < 1000:
            raise RuntimeError("The recording file is empty — nothing was captured. "
                               "Check the microphone permission for your browser "
                               "in System Settings → Privacy & Security, then try again.")

        parts = audio.normalize_and_segment(src, tmp_dir,
                                            int(cfg.get("segment_seconds") or 1140))
        n = len(parts)
        part_durs = [audio.probe_duration(p) for p in parts]

        # Browser recordings often lack header duration — use, in order:
        # header probe, the sum of converted parts, the browser-reported length.
        duration = audio.probe_duration(src)
        if duration < 0.4:
            duration = sum(part_durs)
        if duration < 0.4:
            duration = float(rec.get("duration_s") or 0)
        if duration < 0.4:
            raise RuntimeError("No readable audio in this recording. Check the "
                               "microphone permission for your browser and try again.")
        db.update_recording(rec_id, duration_s=duration)

        # Cumulative time offset of each part within the full recording.
        offsets, acc = [], 0.0
        for d in part_durs:
            offsets.append(acc)
            acc += d if d > 0 else (duration / max(1, n))

        # The user's chosen "this is me" profile (0 or 1 entry) anchors the
        # recorder's own voice from the very first part. Everyone else falls
        # through to the safe anchor-then-fan-out path and becomes "Speaker A/B".
        stored_profiles = (profiles.selected_for_meeting(cfg)
                           if mode == "meeting" else [])
        segments = []
        tracker = _SpeakerTracker(known_names=[nm for nm, _ in stored_profiles])
        full_text_parts = []
        workers = max(1, int(cfg.get("transcribe_concurrency") or 4))
        job.set(stage="transcribing",
                detail=("Transcribing %d parts…" % n) if n > 1 else "Transcribing…",
                pct=8)

        if mode == "meeting":
            results = {}            # part index -> raw segments (offsets applied)
            ref_names = {}          # name sent with each reference clip -> label
            refs = None
            todo = list(range(n))
            anchored = False

            if stored_profiles:
                refs = stored_profiles[:4]
                ref_names = {nm: nm for nm, _ in refs}
            elif n > 1:
                anchored = True
                # Anchor: part 1 alone establishes who the speakers are.
                job.set(detail="Transcribing part 1 of %d (anchoring speakers)…" % n)
                segs = oai.transcribe_diarized(cfg, parts[0])
                for s in segs:
                    s["start"] += offsets[0]
                    s["end"] += offsets[0]
                tracker.map_anchor(segs)
                tracker.collect_samples(segs, parts[0], offsets[0], tmp_dir)
                results[0] = segs
                anchor_refs = tracker.known_speakers()
                refs = [("Speaker %s" % lab, clip)
                        for lab, clip in anchor_refs] or None
                ref_names = {"Speaker %s" % lab: lab for lab, _ in anchor_refs}
                todo = list(range(1, n))
                job.set(detail="Transcribed 1 of %d parts…" % n,
                        pct=8 + int(58 / n))

            if todo:
                done_ct = n - len(todo)
                with ThreadPoolExecutor(max_workers=min(workers, len(todo))) as ex:
                    futs = {ex.submit(oai.transcribe_diarized, cfg, parts[i],
                                      refs): i for i in todo}
                    try:
                        for fut in as_completed(futs):
                            i = futs[fut]
                            segs = fut.result()
                            for s in segs:
                                s["start"] += offsets[i]
                                s["end"] += offsets[i]
                            results[i] = segs
                            done_ct += 1
                            job.set(detail="Transcribed %d of %d parts…"
                                    % (done_ct, n) if n > 1 else "Transcribing…",
                                    pct=8 + int(58 * done_ct / n))
                    except Exception:
                        for f in futs:
                            f.cancel()
                        raise

            # Deterministic label mapping, in chronological part order.
            for i in sorted(results):
                segs = results[i]
                if anchored and i == 0:
                    pass                             # anchor already mapped
                elif refs:
                    tracker.map_part(segs, i, ref_names)
                else:                                # single part, no refs
                    tracker.map_anchor(segs)
                segments.extend(segs)
            segments.sort(key=lambda s: (s["start"], s["end"]))
        else:
            # Quick mode: parts are fully independent — straight fan-out.
            texts = {}
            with ThreadPoolExecutor(max_workers=min(workers, n)) as ex:
                futs = {ex.submit(oai.transcribe_quick, cfg, parts[i]): i
                        for i in range(n)}
                done_ct = 0
                try:
                    for fut in as_completed(futs):
                        i = futs[fut]
                        texts[i] = fut.result()
                        done_ct += 1
                        job.set(detail="Transcribed %d of %d parts…"
                                % (done_ct, n) if n > 1 else "Transcribing…",
                                pct=8 + int(58 * done_ct / n))
                except Exception:
                    for f in futs:
                        f.cancel()
                    raise
            full_text_parts = [texts[i] for i in sorted(texts) if texts[i]]

        raw_turns, cleanup_meta, post_meta = None, None, None
        if mode == "meeting":
            if not segments:
                raise RuntimeError("OpenAI returned an empty transcript. "
                                   "The audio may be silent.")
            turns = _merge_turns(segments)

            # Reconciliation: merge fragmented voices (evidence required),
            # fix contradicted attributions, detect the real meeting end.
            job.set(stage="reconciling", detail="Reconciling speakers…", pct=66)
            identified = set(tracker.named) | set(tracker.letters)
            rec_res = reconcile.run(cfg, turns, identified,
                                    tracker.provisional, duration)
            turns = rec_res["turns"]
            post_turns = rec_res["post_turns"]
            recon_log = rec_res["log"]
            for m in recon_log.get("merges") or []:
                config.log("reconcile merge: %s -> %s" % (m["from"], m["to"]))
            labels_in_use = []
            for t in turns + post_turns:
                if t["speaker"] not in labels_in_use:
                    labels_in_use.append(t["speaker"])
            speakers = {lab: (lab if lab in tracker.named
                              else "Speaker %s" % lab)
                        for lab in labels_in_use}
            if post_turns:
                post_meta = {"meeting_end_s": recon_log.get("meeting_end_s"),
                             "turns": post_turns}
                config.log("reconcile: quarantined %d post-meeting turns "
                           "(meeting ended ~%.0fs)"
                           % (len(post_turns),
                              recon_log.get("meeting_end_s") or 0))

            # Cleanup stage: readable turns, glossary fixes, phantom-speaker
            # resolution. Raw turns are kept alongside; guardrails enforced in
            # scribe/cleanup.py. Any failure falls back to the raw transcript.
            if cfg.get("cleanup_enabled"):
                job.set(stage="cleaning", detail="Cleaning transcript…", pct=70)
                try:
                    res = cleanup.run(cfg, turns, speakers)
                    if res.get("turns"):
                        raw_turns = turns
                        turns = res["turns"]
                        cleanup_meta = {
                            "removed": res["removed"],
                            "reassigned": res["reassigned"],
                            "term_fixes": res["term_fixes"],
                            "flags": res["flags"],
                            "integrity": res["integrity"],
                            "fallback_chunks": res["fallback_chunks"],
                        }
                except Exception as e:
                    config.log("cleanup stage failed; keeping raw transcript: %s" % e)

            # Reconciliation corrections appear alongside cleanup's, in the
            # visible "Corrections & flags" section.
            if recon_log.get("reassigned"):
                if cleanup_meta is None:
                    cleanup_meta = {"removed": [], "reassigned": [],
                                    "term_fixes": [], "flags": [],
                                    "integrity": "", "fallback_chunks": 0}
                cleanup_meta["reassigned"] = (list(recon_log["reassigned"])
                                              + list(cleanup_meta.get("reassigned") or []))

            full_text = "\n".join(
                "%s: %s" % (speakers.get(t["speaker"], t["speaker"]), t["text"])
                for t in turns)
        else:
            full_text = "\n\n".join(full_text_parts).strip()
            if not full_text:
                raise RuntimeError("OpenAI returned an empty transcript. "
                                   "The audio may be silent.")
            turns = [{"seq": 0, "speaker": "", "start_s": 0.0,
                      "end_s": duration, "text": full_text}]
            speakers = {}

        # Title + summary
        title, summary = "", ""
        if cfg.get("generate_summaries"):
            job.set(stage="summarizing", detail="Writing title and summary…", pct=76)
            ts = oai.title_and_summary(cfg, full_text, mode,
                                       list(speakers.values()))
            title, summary = ts["title"], ts["summary"]
        if not title:
            title = _fallback_title(mode, created, full_text)

        # Save files to the transcripts folder
        job.set(stage="saving", detail="Saving transcript…", pct=84)
        folder = store.make_folder(created, title)
        ext = src.suffix or ".webm"
        audio_name = "audio" + ext
        shutil.move(str(src), str(folder / audio_name))

        meta = {
            "id": rec_id, "title": title, "mode": mode,
            "created_at": rec["created_at"], "duration_s": duration,
            "audio_file": audio_name, "summary": summary,
            "speakers": speakers, "folder": str(folder), "status": "done",
            "app": "Meeting Scribe",
        }
        if cleanup_meta is not None:
            meta["cleanup"] = cleanup_meta
        if raw_turns is not None:
            meta["raw_turns"] = raw_turns
        if post_meta is not None:
            meta["post_meeting"] = post_meta
        store.write_transcript_files(folder, meta, turns)
        store.index_recording(meta, turns)

        # Embeddings for semantic search (best-effort)
        job.set(stage="indexing", detail="Indexing for search…", pct=90)
        try:
            missing = db.chunks_missing_vectors(
                rec_id, min_chars=int(cfg.get("search_min_semantic_chars") or 0))
            if missing:
                vecs = oai.embed(cfg, [t for _, t in missing])
                db.store_vectors(list(zip([cid for cid, _ in missing], vecs)))
        except Exception as e:
            config.log("embedding failed for %s (keyword search still works): %s"
                       % (rec_id, e))

        job.set(stage="done", detail="Complete", pct=100)
    except Exception as e:
        db.update_recording(rec_id, status="error", error=str(e))
        raise
    finally:
        shutil.rmtree(str(tmp_dir), ignore_errors=True)


def reembed_missing():
    """Embed any chunks that lack vectors (used after rescan)."""
    cfg = config.load()
    missing = db.chunks_missing_vectors(
        min_chars=int(cfg.get("search_min_semantic_chars") or 0))
    if not missing:
        return 0
    vecs = oai.embed(cfg, [t for _, t in missing])
    db.store_vectors(list(zip([cid for cid, _ in missing], vecs)))
    return len(missing)
