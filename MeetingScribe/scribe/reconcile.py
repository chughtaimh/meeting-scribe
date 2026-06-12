"""Speaker reconciliation + meeting-end detection.

Runs once per meeting, after transcription and turn-merging, before cleanup.
One fast-model call over the whole (compacted) transcript, with code-enforced
guardrails. It addresses three real failure modes:

1. FRAGMENTATION — a voice with no reference clip (5th+ participant, or
   anyone absent from the anchor part) gets a per-part provisional label like
   "P3:A". The same human may hold several provisional labels across parts.
   The model merges provisionals using conversational evidence (addressed by
   name and replying, continuing a thread, self-references). Merging is only
   allowed INTO a named/anchored speaker or another provisional — two
   identified speakers can never be merged.

2. MISATTRIBUTION — the diarizer occasionally matches an unprofiled voice to
   the wrong reference clip (e.g. a guest labeled as a regular). The model may
   reassign INDIVIDUAL turns, but only with quoted textual evidence and under
   a hard cap; every reassignment is logged visibly in the transcript.

3. POST-MEETING CAPTURE — recordings left running after everyone signs off
   pick up private conversation. The model proposes a meeting end time; code
   accepts it only if a real silence gap follows that point. Trailing turns
   are quarantined: preserved in transcript.json and a collapsed section,
   excluded from the cleaned transcript, the AI summary, and the search index.

Any failure degrades gracefully: provisionals become plain letters and
nothing is trimmed.
"""

import re
import time

from . import config, oai

MAX_PROMPT_CHARS = 20000
TURN_TEXT_CAP = 220
GAP_REQUIRED_S = 75.0       # silence needed right after a proposed end
MIN_END_FRACTION = 0.4      # end must lie in the back 60% of the recording
MAX_REASSIGN_FRACTION = 0.15

PROV_RE = re.compile(r"^P\d+:")

_SYSTEM = ("You analyze diarized meeting transcripts and fix speaker "
           "bookkeeping. You are precise and conservative: when evidence is "
           "not clear, you change nothing. Reply with valid JSON only.")

_RULES = """TRANSCRIPT of a recorded meeting is below. Speaker labels:
- IDENTIFIED speakers (voice-matched): %(named)s
- PROVISIONAL speakers (unidentified voices; the SAME person may appear under
  DIFFERENT provisional labels in different time ranges — e.g. "P2:A" and
  "P4:A" may or may not be one person): %(prov)s

Do three tasks:

1. "merges": unify provisional labels that clearly belong to one person, or to
   an identified speaker. Evidence that counts: they are addressed by name and
   the reply comes from that label; they continue the same sentence or thread
   across a boundary; they refer to their own earlier statements; identical
   distinctive role. NEVER merge two IDENTIFIED speakers. When unsure, leave
   the label alone.

2. "reassigned": individual turns whose label contradicts the conversation —
   e.g. the turn answers a question addressed by name to someone else, or the
   speaker refers to the labeled person in the third person ("I'd defer to
   <that very name>"). Reassign ONLY single turns, ONLY with a short evidence
   quote. When unsure, do nothing.

3. "meeting_end_s": when the meeting clearly ends — farewells ("thanks all",
   "bye", "peace out") followed by content that is obviously NOT the meeting
   (household or private talk, unrelated chatter, long gaps) — give the end
   time in seconds (the end of the last real meeting turn). If the meeting
   runs to the final turn, use null.

Return JSON exactly:
{"merges": [{"from": "<provisional>", "to": "<label>"}],
 "reassigned": [{"seq": <n>, "to": "<label>", "evidence": "<short quote>"}],
 "meeting_end_s": <number or null>}

TRANSCRIPT (seq | label | start_s | text):
%(turns)s"""


def _render(turns, speakers):
    lines, total = [], 0
    per_turn = TURN_TEXT_CAP
    if sum(len(t["text"]) for t in turns) > MAX_PROMPT_CHARS:
        per_turn = max(90, MAX_PROMPT_CHARS // max(1, len(turns)))
    for t in turns:
        disp = t["speaker"]
        line = "%d | %s | %d | %s" % (t["seq"], disp,
                                      int(t.get("start_s") or 0),
                                      t["text"][:per_turn])
        total += len(line) + 1
        if total > MAX_PROMPT_CHARS:
            lines.append("… (transcript truncated)")
            break
        lines.append(line)
    return "\n".join(lines)


def _resolve_merges(raw_merges, named, provisional):
    """Validated, transitively-resolved mapping {provisional -> final label}."""
    direct = {}
    for m in raw_merges or []:
        src = str(m.get("from") or "")
        dst = str(m.get("to") or "")
        if (src in provisional and src != dst
                and (dst in named or dst in provisional)):
            direct[src] = dst
    resolved = {}
    for src in direct:
        cur, seen = src, set()
        while cur in direct and cur not in seen:
            seen.add(cur)
            cur = direct[cur]
        if cur not in seen:           # no cycle
            resolved[src] = cur
    return resolved


def _validate_end(turns, end_s, duration):
    try:
        end_s = float(end_s)
    except (TypeError, ValueError):
        return None
    if duration and end_s < MIN_END_FRACTION * duration:
        return None
    before = [t for t in turns if (t.get("start_s") or 0) <= end_s + 2]
    after = [t for t in turns if (t.get("start_s") or 0) > end_s + 2]
    if not before or not after:
        return None
    prev_end = max(float(t.get("end_s") or t.get("start_s") or 0) for t in before)
    next_start = min(float(t.get("start_s") or 0) for t in after)
    if next_start - prev_end < GAP_REQUIRED_S:
        return None                   # no real silence gap — don't trim
    return prev_end


def _next_letter(taken):
    for c in range(ord("A"), ord("Z") + 1):
        if chr(c) not in taken:
            return chr(c)
    return "X%d" % (len(taken) + 1)


def run(cfg, turns, named, provisional, duration):
    """turns: merged turns whose speakers are identified labels or 'P<i>:<x>'
    provisionals. Returns {turns, post_turns, speakers, log}. Never raises."""
    named = set(named or [])
    provisional = set(provisional or [])
    log = {"merges": [], "reassigned": [], "meeting_end_s": None, "note": ""}
    data = None

    if turns and (provisional or len(turns) > 3):
        prompt = _RULES % {
            "named": ", ".join(sorted(named)) or "(none)",
            "prov": ", ".join(sorted(provisional)) or "(none)",
            "turns": _render(turns, {}),
        }
        t0 = time.time()
        try:
            data = oai.chat_json(cfg, _SYSTEM, prompt,
                                 max_completion=1600, timeout=(15, 90))
            config.log("reconcile: model pass in %.1fs" % (time.time() - t0))
        except Exception as e:
            config.log("reconcile: model pass failed (%s) — relabel only" % e)
            log["note"] = "reconciliation unavailable; labels kept as-is"

    # ---- merges (guarded) ----
    mapping = {}
    if data:
        mapping = _resolve_merges(data.get("merges"), named, provisional)
        for src, dst in sorted(mapping.items()):
            log["merges"].append({"from": src, "to": dst})
    for t in turns:
        if t["speaker"] in mapping:
            t["speaker"] = mapping[t["speaker"]]

    # ---- turn reassignments (guarded) ----
    if data:
        by_seq = {t["seq"]: t for t in turns}
        allowed = named | provisional | {t["speaker"] for t in turns}
        cap = max(1, int(len(turns) * MAX_REASSIGN_FRACTION))
        for r in (data.get("reassigned") or [])[:cap]:
            t = by_seq.get(r.get("seq"))
            dst = mapping.get(str(r.get("to") or ""), str(r.get("to") or ""))
            ev = str(r.get("evidence") or "").strip()
            if t is None or not ev or dst not in allowed or dst == t["speaker"]:
                continue
            log["reassigned"].append({"start_s": t.get("start_s"),
                                      "from": t["speaker"], "to": dst,
                                      "why": ev[:160]})
            t["speaker"] = dst

    # ---- meeting end (guarded) ----
    post_turns = []
    if data:
        end = _validate_end(turns, data.get("meeting_end_s"), duration)
        if end is not None:
            log["meeting_end_s"] = end
            post_turns = [t for t in turns if (t.get("start_s") or 0) > end + 2]
            turns = [t for t in turns if (t.get("start_s") or 0) <= end + 2]

    # ---- final labels: surviving provisionals become plain letters ----
    taken = set(named) | {t["speaker"] for t in turns + post_turns
                          if not PROV_RE.match(t["speaker"])}
    relabel = {}
    for t in turns + post_turns:          # chronological; main section first
        lab = t["speaker"]
        if PROV_RE.match(lab) and lab not in relabel:
            nl = _next_letter(taken)
            relabel[lab] = nl
            taken.add(nl)
    for t in turns + post_turns:
        t["speaker"] = relabel.get(t["speaker"], t["speaker"])
    # Keep the visible log in final labels (no internal P-ids leaking out).
    for r in log["reassigned"]:
        r["from"] = relabel.get(r["from"], r["from"])
        r["to"] = relabel.get(r["to"], r["to"])
    for m in log["merges"]:
        m["to"] = relabel.get(m["to"], m["to"])

    for i, t in enumerate(turns):
        t["seq"] = i
    for i, t in enumerate(post_turns):
        t["seq"] = i

    speakers_out = {}
    for t in turns + post_turns:
        lab = t["speaker"]
        if lab not in speakers_out:
            speakers_out[lab] = lab if lab in named else "Speaker %s" % lab
    return {"turns": turns, "post_turns": post_turns,
            "speakers": speakers_out, "log": log}
