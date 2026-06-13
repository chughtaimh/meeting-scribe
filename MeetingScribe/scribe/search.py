"""Hybrid search: SQLite FTS5 keyword search + embedding vector search,
merged with Reciprocal Rank Fusion.
"""

import html
import json
import re
import threading

from . import config, db, oai

try:
    import numpy as _np
except Exception:  # pure-python fallback
    _np = None

_cache_lock = threading.Lock()
_vec_cache = {"ids": None, "mat": None, "norms": None}


def invalidate_cache():
    with _cache_lock:
        _vec_cache["ids"] = None
        _vec_cache["mat"] = None
        _vec_cache["norms"] = None


def _load_matrix():
    with _cache_lock:
        if _vec_cache["ids"] is not None:
            return _vec_cache["ids"], _vec_cache["mat"], _vec_cache["norms"]
    ids, vecs = db.all_vectors()
    if not ids:
        result = ([], None, None)
    elif _np is not None:
        mat = _np.asarray(vecs, dtype=_np.float32)
        norms = _np.linalg.norm(mat, axis=1)
        norms[norms == 0] = 1.0
        result = (ids, mat, norms)
    else:
        norms = []
        for v in vecs:
            n = sum(x * x for x in v) ** 0.5 or 1.0
            norms.append(n)
        result = (ids, vecs, norms)
    with _cache_lock:
        _vec_cache["ids"], _vec_cache["mat"], _vec_cache["norms"] = result
    return result


def _vector_search(query: str, limit=30):
    ids, mat, norms = _load_matrix()
    if not ids:
        return []
    cfg = config.load()
    floor = float(cfg.get("search_semantic_floor") or 0.18)
    min_chars = int(cfg.get("search_min_semantic_chars") or 0)
    # Short conversational fragments ("so", "okay", "which") embed close to
    # almost any query and would otherwise dominate semantic results with
    # noise. Drop them from the vector side here (they stay keyword-searchable).
    short_ids = db.short_chunk_ids(min_chars) if min_chars > 0 else set()
    try:
        qv = oai.embed_one(cfg, query)
    except Exception as e:
        config.log("query embedding failed: %s" % e)
        return []
    if _np is not None:
        q = _np.asarray(qv, dtype=_np.float32)
        qn = float(_np.linalg.norm(q)) or 1.0
        sims = (mat @ q) / (norms * qn)
        # Walk the full descending-similarity order, skipping short fragments,
        # until we have `limit` hits or drop below the floor. We must NOT
        # pre-slice the order: when many short fragments sit at the very top
        # (a single-word query like "google" can have dozens), a fixed window
        # would be all fragments and starve the result of real matches.
        order = sims.argsort()[::-1]
        out = []
        for i in order:
            i = int(i)
            s = float(sims[i])
            if s <= floor:
                break
            if ids[i] in short_ids:
                continue
            out.append((ids[i], s))
            if len(out) >= limit:
                break
        return out
    # pure-python fallback
    qn = (sum(x * x for x in qv) ** 0.5) or 1.0
    scored = []
    for i, v in enumerate(mat):
        if ids[i] in short_ids:
            continue
        dot = sum(a * b for a, b in zip(v, qv))
        scored.append((ids[i], dot / (norms[i] * qn)))
    scored.sort(key=lambda t: -t[1])
    return [(cid, s) for cid, s in scored[:limit] if s > floor]


def _snippet(text: str, query: str, width=240) -> str:
    """HTML-escaped snippet with <mark> around query words."""
    words = [w for w in re.findall(r"[\w'-]+", query.lower()) if len(w) > 1]
    low = text.lower()
    pos = -1
    for w in words:
        pos = low.find(w)
        if pos >= 0:
            break
    if pos < 0:
        pos = 0
    start = max(0, pos - width // 3)
    end = min(len(text), start + width)
    snip = text[start:end].strip()
    if start > 0:
        snip = "…" + snip
    if end < len(text):
        snip += "…"
    out = html.escape(snip)
    for w in sorted(set(words), key=len, reverse=True):
        out = re.sub("(?i)(%s)" % re.escape(w), r"<mark>\1</mark>", out)
    return out


def search(query: str, limit=12) -> list:
    query = (query or "").strip()
    if not query:
        return []
    kw = db.keyword_search(query, limit=40)          # [(chunk_id, rec_id, rank)]
    vec = _vector_search(query, limit=40)            # [(chunk_id, sim)]

    K = 60.0
    scores, why = {}, {}
    for r, (cid, _rec, _rank) in enumerate(kw):
        scores[cid] = scores.get(cid, 0) + 1.0 / (K + r + 1)
        why.setdefault(cid, set()).add("keyword")
    for r, (cid, _sim) in enumerate(vec):
        scores[cid] = scores.get(cid, 0) + 1.0 / (K + r + 1)
        why.setdefault(cid, set()).add("semantic")

    ranked = sorted(scores.items(), key=lambda t: -t[1])
    chunk_map = db.chunks_by_ids([cid for cid, _ in ranked[:limit * 3]])

    results, seen_per_rec = [], {}
    for cid, score in ranked:
        ch = chunk_map.get(cid)
        if not ch:
            continue
        rec_id = ch["rec_id"]
        if seen_per_rec.get(rec_id, 0) >= 3:   # max 3 hits per recording
            continue
        rec = db.get_recording(rec_id)
        if not rec or rec.get("status") != "done":
            continue
        speakers = {}
        try:
            speakers = json.loads(rec.get("speakers_json") or "{}")
        except Exception:
            pass
        label = ch.get("speaker") or ""
        results.append({
            "chunk_id": cid,
            "rec_id": rec_id,
            "title": rec.get("title"),
            "created_at": rec.get("created_at"),
            "mode": rec.get("mode"),
            "speaker": speakers.get(label, ("Speaker %s" % label) if label else ""),
            "start_s": ch.get("start_s"),
            "seq": ch.get("seq"),
            "snippet": _snippet(ch.get("text") or "", query),
            "match": sorted(why.get(cid, [])),
        })
        seen_per_rec[rec_id] = seen_per_rec.get(rec_id, 0) + 1
        if len(results) >= limit:
            break
    return results
