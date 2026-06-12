"""Minimal OpenAI REST client (requests-based; no SDK dependency).

Covers: quick transcription, diarized transcription (gpt-4o-transcribe-diarize),
embeddings, and small chat completions for titles/summaries.
"""

import base64
import json
import mimetypes
import threading
import time
from pathlib import Path

import requests

from . import config

# Global gate on concurrent OpenAI calls — shared across all jobs/threads so
# parallel part-transcription in two recordings can't stack into a rate spike.
_gate = None
_gate_lock = threading.Lock()


def _api_gate():
    global _gate
    with _gate_lock:
        if _gate is None:
            n = max(1, int(config.load().get("transcribe_concurrency") or 4))
            _gate = threading.BoundedSemaphore(n)
        return _gate


class OAIError(RuntimeError):
    def __init__(self, message, status=None, body=None):
        super().__init__(message)
        self.status = status
        self.body = body or ""


def _friendly(status, body) -> str:
    text = ""
    try:
        text = (json.loads(body).get("error") or {}).get("message", "")
    except Exception:
        text = (body or "")[:300]
    if status == 401:
        return "OpenAI rejected the API key (401). Check the key in Settings."
    if status == 429:
        return "OpenAI rate/credit limit hit (429). %s" % text
    if status == 413:
        return "Audio part too large for OpenAI (413)."
    return "OpenAI error %s: %s" % (status, text)


def _headers(cfg):
    key = (cfg.get("openai_api_key") or "").strip()
    if not key:
        raise OAIError("No OpenAI API key set. Add it in Settings.", status=0)
    return {"Authorization": "Bearer %s" % key}


def _post(cfg, path, *, data=None, files=None, json_body=None, timeout=(15, 900),
          retries=3):
    with _api_gate():
        return _post_inner(cfg, path, data=data, files=files,
                           json_body=json_body, timeout=timeout, retries=retries)


def _post_inner(cfg, path, *, data=None, files=None, json_body=None,
                timeout=(15, 900), retries=3):
    url = cfg["openai_base_url"].rstrip("/") + path
    last = None
    for attempt in range(retries):
        try:
            r = requests.post(url, headers=_headers(cfg), data=data, files=files,
                              json=json_body, timeout=timeout)
        except requests.RequestException as e:
            last = OAIError("Network error talking to OpenAI: %s" % e)
            time.sleep(1.5 * (attempt + 1))
            continue
        if r.status_code == 200:
            return r
        last = OAIError(_friendly(r.status_code, r.text), status=r.status_code,
                        body=r.text)
        # retry only transient statuses
        if r.status_code in (429, 500, 502, 503, 504) and attempt < retries - 1:
            time.sleep(2.0 * (attempt + 1))
            continue
        raise last
    raise last


def test_key(cfg) -> dict:
    url = cfg["openai_base_url"].rstrip("/") + "/models"
    try:
        r = requests.get(url, headers=_headers(cfg), timeout=(10, 30))
    except requests.RequestException as e:
        raise OAIError("Network error: %s" % e)
    if r.status_code != 200:
        raise OAIError(_friendly(r.status_code, r.text), status=r.status_code)
    ids = [m.get("id", "") for m in r.json().get("data", [])]
    return {
        "ok": True,
        "diarize_available": any(cfg["diarize_model"] in i for i in ids),
        "model_count": len(ids),
    }


# ---------- transcription ----------

def _file_tuple(path):
    p = Path(path)
    mime = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
    return (p.name, open(str(p), "rb"), mime)


def transcribe_quick(cfg, path) -> str:
    """Plain transcription (no speakers). Tries quick_model, falls back."""
    models = [cfg["quick_model"], cfg["quick_model_fallback"]]
    last = None
    for model in [m for m in models if m]:
        fh = None
        try:
            name, fh, mime = _file_tuple(path)
            r = _post(cfg, "/audio/transcriptions",
                      data={"model": model, "response_format": "json"},
                      files={"file": (name, fh, mime)})
            return (r.json().get("text") or "").strip()
        except OAIError as e:
            last = e
            if e.status in (400, 404):  # model not available — try fallback
                continue
            raise
        finally:
            if fh:
                fh.close()
    raise last or OAIError("Transcription failed")


def _b64_data_url(path) -> str:
    mime = mimetypes.guess_type(str(path))[0] or "audio/wav"
    with open(str(path), "rb") as f:
        return "data:%s;base64,%s" % (mime, base64.b64encode(f.read()).decode())


def transcribe_diarized(cfg, path, known_speakers=None) -> list:
    """Diarized transcription. Returns [{speaker, text, start, end}].

    known_speakers: optional [(name, clip_path)] (max 4) to keep speaker labels
    consistent across multi-part recordings.
    """
    def call(with_refs):
        data = [
            ("model", cfg["diarize_model"]),
            ("response_format", "diarized_json"),
            ("chunking_strategy", "auto"),
        ]
        if with_refs and known_speakers:
            for nm, clip in known_speakers[:4]:
                data.append(("known_speaker_names[]", nm))
                data.append(("known_speaker_references[]", _b64_data_url(clip)))
        name, fh, mime = _file_tuple(path)
        try:
            return _post(cfg, "/audio/transcriptions", data=data,
                         files={"file": (name, fh, mime)})
        finally:
            fh.close()

    try:
        r = call(with_refs=True)
    except OAIError as e:
        # If the speaker-reference parameters are rejected, retry without them.
        if known_speakers and e.status == 400 and "speaker" in (e.body or "").lower():
            config.log("known_speaker refs rejected; retrying without: %s" % e)
            r = call(with_refs=False)
        else:
            raise

    payload = r.json()
    segs = payload.get("segments") or []
    out = []
    for s in segs:
        text = (s.get("text") or "").strip()
        if not text:
            continue
        out.append({
            "speaker": str(s.get("speaker") or "A").strip() or "A",
            "text": text,
            "start": float(s.get("start") or 0.0),
            "end": float(s.get("end") or 0.0),
        })
    if not out and (payload.get("text") or "").strip():
        out.append({"speaker": "A", "text": payload["text"].strip(),
                    "start": 0.0, "end": 0.0})
    return out


# ---------- embeddings ----------

def embed(cfg, texts, batch=96) -> list:
    """Returns list of float vectors, aligned with texts."""
    vectors = []
    for i in range(0, len(texts), batch):
        chunk = [t[:6000] for t in texts[i:i + batch]]
        r = _post(cfg, "/embeddings",
                  json_body={"model": cfg["embedding_model"], "input": chunk},
                  timeout=(15, 120))
        data = sorted(r.json()["data"], key=lambda d: d["index"])
        vectors.extend([d["embedding"] for d in data])
    return vectors


def embed_one(cfg, text):
    return embed(cfg, [text])[0]


# ---------- title & summary ----------

def _chat(cfg, model, system, user, max_completion=None,
          timeout=(15, 180), retries=3):
    """Chat call. max_completion (tokens) caps the reply when given;
    timeout/retries let latency-sensitive callers fail fast."""
    body = {
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
    }
    if max_completion:
        body["max_completion_tokens"] = int(max_completion)
    r = _post(cfg, "/chat/completions", json_body=body, timeout=timeout,
              retries=retries)
    return r.json()["choices"][0]["message"]["content"]


def chat_json(cfg, system, user, models=None, max_completion=1400,
              timeout=(15, 90), retries=1) -> dict:
    """One chat call that must return a JSON object; tries models in order.
    Defaults to the fast cleanup-model rotation."""
    if not models:
        models = [m for m in (cfg.get("cleanup_models") or []) if m] \
                 or list(cfg.get("summary_models") or [])
    last = None
    for model in models:
        try:
            raw = _chat(cfg, model, system, user, max_completion=max_completion,
                        timeout=timeout, retries=retries)
            raw = raw.strip()
            if raw.startswith("```"):
                raw = raw.strip("`")
            data = json.loads(raw[raw.find("{"):raw.rfind("}") + 1])
            if isinstance(data, dict):
                return data
            last = "unexpected shape from %s" % model
        except Exception as e:
            last = "%s: %s" % (model, e)
            config.log("chat_json model failed — %s" % last)
    raise RuntimeError(last or "no model available")


def title_and_summary(cfg, transcript_text, mode, speakers) -> dict:
    """Returns {"title": str, "summary": markdown str (may be '')}. Never raises."""
    excerpt = transcript_text[:24000]
    if mode == "meeting":
        ask = (
            'Return strict JSON: {"title": "...", "summary": "..."}.\n'
            "title: a specific 3-8 word name for this meeting (no quotes/emoji).\n"
            "summary: concise Markdown with sections '## Overview' (2-3 sentences), "
            "'## Key points' (bullets), '## Decisions' (bullets or 'None'), "
            "'## Action items' (bullets with owner if known, or 'None'). "
            "Participants are labeled %s.\n\nTRANSCRIPT:\n%s"
            % (", ".join(speakers) if speakers else "Speaker A/B", excerpt))
    else:
        ask = (
            'Return strict JSON: {"title": "...", "summary": ""}.\n'
            "title: a specific 3-8 word name for this voice note.\n\nNOTE:\n%s"
            % excerpt)

    models = []
    if cfg.get("summary_model_active"):
        models.append(cfg["summary_model_active"])
    models += [m for m in cfg.get("summary_models", []) if m not in models]

    for model in models:
        try:
            raw = _chat(cfg, model, "You produce crisp executive meeting notes. "
                                    "Reply with valid JSON only.", ask)
            raw = raw.strip()
            if raw.startswith("```"):
                raw = raw.strip("`")
                raw = raw[raw.find("{"):raw.rfind("}") + 1]
            data = json.loads(raw[raw.find("{"):raw.rfind("}") + 1])
            title = str(data.get("title") or "").strip()
            summary = str(data.get("summary") or "").strip()
            if title:
                if model != cfg.get("summary_model_active"):
                    config.save({"summary_model_active": model})
                return {"title": title[:90], "summary": summary}
        except Exception as e:
            config.log("summary model %s failed: %s" % (model, e))
            continue
    return {"title": "", "summary": ""}
