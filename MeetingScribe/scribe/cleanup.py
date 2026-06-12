"""Transcript cleanup stage — turns raw diarized output into a readable
transcript without losing information.

What it does (per chunk of turns, via a small chat model):
  - merges fragment turns split by backchannels back into whole sentences
  - restores punctuation in long unpunctuated stretches
  - removes fillers/stutters (intelligent verbatim); KEEPS hedges and
    meaningful self-corrections
  - applies confirmed glossary fixes silently; flags new uncertain terms
  - reassigns phantom-speaker turns only when context is unambiguous
  - removes non-participant background speech, recorded in a visible log

Hard guardrails ENFORCED IN CODE, not just in the prompt:
  - every numeric figure in the source must survive (or sit in the removal
    log); no new figures may appear. Violations -> one retry with the error
    explained -> if still failing, that chunk falls back to raw, flagged.
  - every source turn must be accounted for (kept in a merge or logged as
    removed). Violations handled the same way.
  - the raw turns are always preserved alongside the cleaned ones.

Cost: one or two mini-model calls per ~10 minutes of meeting (~a cent).
"""

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import config, glossary, oai

CHUNK_CHARS = 3200     # source chars per model call (small => fast calls,
                       # small blast radius when one chunk fails validation)
CONTEXT_TURNS = 2      # read-only context carried into each chunk
CALL_TIMEOUT = (15, 75)   # connect/read timeout per model call
CALL_RETRIES = 1          # transport retries per model call
MAX_REPLY_TOKENS = 2600   # reply cap — a cleaned 3200-char chunk fits easily
CHUNK_BUDGET_S = 140      # wall-clock budget per chunk before raw fallback
STAGE_BUDGET_S = 420      # wall-clock budget for the whole stage

# This stage must NEVER hold a transcript hostage: when models are slow or
# misbehave, chunks ship raw (flagged) and the pipeline moves on.

_SYSTEM = (
    "You clean up raw diarized meeting transcripts. You are precise and "
    "conservative: you make speech readable but never change what was meant, "
    "never summarize, and never drop real content. Reply with valid JSON only."
)

_RULES = """Rewrite the TURNS below into clean, readable transcript turns.

Rules, in priority order:
1. NUMBERS ARE IMMUTABLE. Every figure must appear exactly as in the source.
   If a figure looks like a mis-transcription, keep it and append
   [sic — likely "X"]. Never silently correct a number.
2. Account for every source turn: each seq must appear in exactly one output
   turn's "seqs", OR in "removed". Never just drop a turn.
3. Merge fragments: when a speaker's sentence is split across multiple turns
   by short interjections, reunite it. Keep meaningful short replies
   ("It is.", "Awesome.") as their own turns; fold pure backchannels
   ("yeah", "okay", "um") into nothing — list their seqs with the merged
   turn they interrupt.
4. Speakers: output "speaker" must be one of the allowed labels. A turn may
   be reassigned to a different allowed label ONLY when context makes it
   unambiguous (e.g. it answers a direct question and is confirmed by the
   conversation); log it in "reassigned". Background speech from
   non-participants (content that does not thread with the conversation)
   goes in "removed" with reason "background". When genuinely uncertain,
   keep the original label and add a flag.
5. Readability: restore punctuation and sentence boundaries; fix casing;
   remove fillers (um, uh, like, you know) and stutter repetitions
   ("we we", "but but"); collapse false starts that add nothing. PRESERVE
   hedges ("I think", "probably", "I don't know if that's 100% true") and
   self-corrections that carry meaning ("Or shouldn't, sorry.").
6. Vocabulary: apply the confirmed glossary fixes silently. For NEW suspected
   mis-transcriptions of names or terms not in the glossary: names must stay
   as transcribed plus a flag; ordinary words may be fixed if near-certain,
   logged in "term_fixes" — when not near-certain, keep and flag.
7. Annotations you add use square brackets: [sic — likely "..."],
   [unclear], [audio dropped]. Do not add any other commentary.

Return JSON exactly in this shape:
{"turns": [{"speaker": "<label>", "seqs": [<source seq numbers>],
            "text": "<cleaned text>"}],
 "removed": [{"seq": <n>, "text": "<original text>", "reason": "background|disfluency"}],
 "reassigned": [{"seq": <n>, "from": "<label>", "to": "<label>", "why": "<short>"}],
 "term_fixes": [{"seq": <n>, "from": "<as transcribed>", "to": "<fixed>"}],
 "flags": [{"seq": <n>, "note": "<what to verify>"}]}
"""

_NUM_RE = re.compile(r"\d+(?:\.\d+)?")
_BRACKET_RE = re.compile(r"\[[^\]]*\]")


def _nums(text) -> set:
    return set(_NUM_RE.findall(text or ""))


def _strip_annotations(text) -> str:
    return _BRACKET_RE.sub(" ", text or "")


def _models(cfg):
    """Cleanup model rotation. Reasoning models (gpt-5*) are excluded — they
    are far too slow for echo-the-chunk JSON work; fast minis only."""
    models = []
    if cfg.get("cleanup_model_active"):
        models.append(cfg["cleanup_model_active"])
    models += [m for m in (cfg.get("cleanup_models") or []) if m not in models]
    return [m for m in models if not m.startswith("gpt-5")] or models


def _chat_json(cfg, system, user, dead=None):
    """One fast model call with fallback. `dead` is a per-run set of model
    names that already failed hard (400/404) — skipped without retrying."""
    dead = dead if dead is not None else set()
    last_err = None
    for model in _models(cfg):
        if model in dead:
            continue
        try:
            raw = oai._chat(cfg, model, system, user,
                            max_completion=MAX_REPLY_TOKENS,
                            timeout=CALL_TIMEOUT, retries=CALL_RETRIES)
            raw = raw.strip()
            if raw.startswith("```"):
                raw = raw.strip("`")
            data = json.loads(raw[raw.find("{"):raw.rfind("}") + 1])
            if isinstance(data, dict) and isinstance(data.get("turns"), list):
                if model != cfg.get("cleanup_model_active"):
                    config.save({"cleanup_model_active": model})
                return data
            last_err = "model %s returned unexpected shape" % model
        except oai.OAIError as e:
            last_err = "%s: %s" % (model, e)
            config.log("cleanup model failed — %s" % last_err)
            if e.status in (400, 404):   # bad model/param: don't try it again
                dead.add(model)
        except Exception as e:
            last_err = "%s: %s" % (model, e)
            config.log("cleanup model failed — %s" % last_err)
    raise RuntimeError(last_err or "no cleanup model available")


def _chunk(turns):
    """Split turns into chunks of <= CHUNK_CHARS source characters."""
    chunks, cur, size = [], [], 0
    for t in turns:
        n = len(t.get("text") or "")
        if cur and size + n > CHUNK_CHARS:
            chunks.append(cur)
            cur, size = [], 0
        cur.append(t)
        size += n
    if cur:
        chunks.append(cur)
    return chunks


def _render_turns(turns, speakers):
    lines = []
    for t in turns:
        lines.append("seq=%d | %s (%s) | %s" % (
            t["seq"], t["speaker"],
            speakers.get(t["speaker"], "Speaker %s" % t["speaker"]),
            t["text"]))
    return "\n".join(lines)


def _validate(chunk, result):
    """Code-enforced invariants. Returns (ok, errors)."""
    errors = []
    src = {t["seq"]: t for t in chunk}
    allowed = {t["speaker"] for t in chunk}

    seen = []
    for ot in result.get("turns") or []:
        if ot.get("speaker") not in allowed:
            errors.append("output uses unknown speaker label %r" % ot.get("speaker"))
        seen += [s for s in (ot.get("seqs") or [])]
    removed_seqs = [r.get("seq") for r in (result.get("removed") or [])]
    seen_all = set(seen) | set(removed_seqs)
    missing = set(src) - seen_all
    unknown = seen_all - set(src)
    dupes = {s for s in seen if seen.count(s) > 1}
    if missing:
        errors.append("source seqs not accounted for: %s" % sorted(missing))
    if unknown:
        errors.append("output references unknown seqs: %s" % sorted(unknown))
    if dupes:
        errors.append("seqs used in more than one output turn: %s" % sorted(dupes))

    src_nums = set()
    for t in chunk:
        src_nums |= _nums(t["text"])
    out_nums = set()
    for ot in result.get("turns") or []:
        out_nums |= _nums(_strip_annotations(ot.get("text")))
    removed_nums = set()
    for r in result.get("removed") or []:
        removed_nums |= _nums(r.get("text") or src.get(r.get("seq"), {}).get("text", ""))
    missing_nums = src_nums - out_nums - removed_nums
    invented_nums = out_nums - src_nums
    if missing_nums:
        errors.append("figures lost from source: %s" % sorted(missing_nums))
    if invented_nums:
        errors.append("figures invented (not in source): %s" % sorted(invented_nums))
    return (not errors), errors


def _finalize_chunk(chunk, result):
    """Convert validated model output into turn dicts with timestamps."""
    src = {t["seq"]: t for t in chunk}
    out = []
    for ot in result.get("turns") or []:
        seqs = sorted(ot.get("seqs") or [])
        members = [src[s] for s in seqs if s in src]
        if not members:
            continue
        out.append({
            "speaker": ot["speaker"],
            "start_s": min(m["start_s"] for m in members),
            "end_s": max(m["end_s"] for m in members),
            "text": (ot.get("text") or "").strip(),
        })
    return out


def run(cfg, turns, speakers):
    """Clean `turns` (raw merged turns with seq/speaker/start_s/end_s/text).

    Returns dict:
      turns        cleaned turns (seq renumbered)
      removed      [{start_s, text, reason}]
      reassigned / term_fixes / flags   change log entries with timestamps
      integrity    summary string
      fallback_chunks  number of chunks where raw was kept
    """
    g = glossary.load()
    gloss = glossary.prompt_block(g)
    chunks = _chunk(turns)
    cleaned, removed, reassigned, fixes, flags = [], [], [], [], []
    fallback = 0
    stage_t0 = time.time()
    dead = set()

    def _clean_one(ci, chunk):
        """Clean one chunk. Returns (ci, status, result);
        status: 'ok' | 'budget' | 'failed'.

        Chunks are independent — the read-only context comes from SOURCE
        turns, not from other chunks' output — so they run in parallel and
        results are assembled in chronological order afterwards.
        """
        if time.time() - stage_t0 > STAGE_BUDGET_S:
            config.log("cleanup: stage budget hit at chunk %d/%d"
                       % (ci + 1, len(chunks)))
            return ci, "budget", None
        chunk_t0 = time.time()
        ctx = ""
        if ci > 0:
            prev = chunks[ci - 1][-CONTEXT_TURNS:]
            ctx = ("CONTEXT — preceding turns, already processed, do NOT "
                   "output them:\n" + _render_turns(prev, speakers) + "\n\n")
        ask = ("%s\nGLOSSARY:\n%s\n\nPARTICIPANT LABELS: %s\n\n%sTURNS:\n%s"
               % (_RULES, gloss or "(empty)",
                  ", ".join("%s=%s" % (k, v) for k, v in speakers.items()),
                  ctx, _render_turns(chunk, speakers)))

        result, ok, errors = None, False, []
        for attempt in range(2):
            if attempt and time.time() - chunk_t0 > CHUNK_BUDGET_S:
                config.log("cleanup chunk %d budget hit; falling back to raw"
                           % (ci + 1))
                break
            try:
                prompt = ask if attempt == 0 else (
                    ask + "\n\nYOUR PREVIOUS ATTEMPT FAILED VALIDATION:\n- "
                    + "\n- ".join(errors) + "\nFix these exactly; change nothing else.")
                result = _chat_json(cfg, _SYSTEM, prompt, dead)
                ok, errors = _validate(chunk, result)
                if ok:
                    break
                config.log("cleanup chunk %d failed validation (attempt %d): %s"
                           % (ci + 1, attempt + 1, "; ".join(errors)))
            except Exception as e:
                errors = [str(e)]
                config.log("cleanup chunk %d error: %s" % (ci + 1, e))

        if ok and result:
            config.log("cleanup chunk %d/%d ok in %.1fs"
                       % (ci + 1, len(chunks), time.time() - chunk_t0))
            return ci, "ok", result
        return ci, "failed", None

    # Run chunks concurrently (bounded globally by oai's API gate).
    workers = max(1, int(cfg.get("transcribe_concurrency") or 4))
    outcomes = {}
    if len(chunks) <= 1 or workers == 1:
        for ci, chunk in enumerate(chunks):
            _, status, result = _clean_one(ci, chunk)
            outcomes[ci] = (status, result)
    else:
        with ThreadPoolExecutor(max_workers=min(workers, len(chunks))) as ex:
            futs = [ex.submit(_clean_one, ci, chunk)
                    for ci, chunk in enumerate(chunks)]
            for fut in as_completed(futs):
                ci, status, result = fut.result()
                outcomes[ci] = (status, result)

    # Assemble in chronological order.
    for ci, chunk in enumerate(chunks):
        status, result = outcomes.get(ci, ("failed", None))
        if status == "ok" and result:
            cleaned.extend(_finalize_chunk(chunk, result))
            src = {t["seq"]: t for t in chunk}
            for r in result.get("removed") or []:
                s = src.get(r.get("seq"))
                if s:
                    removed.append({"start_s": s["start_s"],
                                    "text": s["text"],
                                    "reason": r.get("reason") or "removed"})
            for r in result.get("reassigned") or []:
                s = src.get(r.get("seq"))
                reassigned.append({"start_s": (s or {}).get("start_s"),
                                   "from": r.get("from"), "to": r.get("to"),
                                   "why": r.get("why") or ""})
            for r in result.get("term_fixes") or []:
                s = src.get(r.get("seq"))
                fixes.append({"start_s": (s or {}).get("start_s"),
                              "from": r.get("from"), "to": r.get("to")})
            for r in result.get("flags") or []:
                s = src.get(r.get("seq"))
                flags.append({"start_s": (s or {}).get("start_s"),
                              "note": r.get("note") or ""})
        else:
            # Guardrail fallback: keep this chunk verbatim, flagged.
            fallback += 1
            cleaned.extend({"speaker": t["speaker"], "start_s": t["start_s"],
                            "end_s": t["end_s"], "text": t["text"]}
                           for t in chunk)
            flags.append({"start_s": chunk[0]["start_s"],
                          "note": ("cleanup time budget exhausted; "
                                   "this section is shown raw")
                          if status == "budget" else
                          ("cleanup skipped for this section "
                           "(validation failed); showing raw")})

    cleaned.sort(key=lambda t: t["start_s"])
    for i, t in enumerate(cleaned):
        t["seq"] = i

    raw_n = len(_NUM_RE.findall(" ".join(t["text"] for t in turns)))
    integrity = ("all figures preserved or logged; %d/%d chunks cleaned"
                 % (len(chunks) - fallback, len(chunks)))
    return {"turns": cleaned, "removed": removed, "reassigned": reassigned,
            "term_fixes": fixes, "flags": flags, "integrity": integrity,
            "fallback_chunks": fallback, "source_figures": raw_n}
