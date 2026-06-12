"""In-memory background job registry (one job per recording)."""

import threading
import traceback

from . import config

_jobs = {}
_lock = threading.Lock()
# Cap on simultaneous pipelines — local CPU politeness (ffmpeg re-encoding),
# not an API constraint: OpenAI concurrency is governed globally in oai._api_gate.
_workers = threading.Semaphore(
    max(1, int(config.load().get("max_concurrent_recordings") or 4)))


class Job:
    def __init__(self, rec_id):
        self.rec_id = rec_id
        self.stage = "queued"
        self.detail = ""
        self.pct = 0
        self.done = False
        self.error = ""

    def set(self, stage=None, detail=None, pct=None):
        if stage is not None:
            self.stage = stage
        if detail is not None:
            self.detail = detail
        if pct is not None:
            self.pct = max(0, min(100, int(pct)))

    def as_dict(self):
        return {"rec_id": self.rec_id, "stage": self.stage, "detail": self.detail,
                "pct": self.pct, "done": self.done, "error": self.error}


def get(rec_id):
    with _lock:
        j = _jobs.get(rec_id)
        return j.as_dict() if j else None


def active():
    with _lock:
        return [j.as_dict() for j in _jobs.values() if not j.done]


def start(rec_id, target):
    """Run target(job) in a background thread, tracked under rec_id."""
    job = Job(rec_id)
    with _lock:
        _jobs[rec_id] = job

    def run():
        with _workers:
            try:
                target(job)
                job.set(stage="done", pct=100)
            except Exception as e:
                config.log("job %s failed: %s\n%s"
                           % (rec_id, e, traceback.format_exc()))
                job.error = str(e) or e.__class__.__name__
                job.set(stage="error")
            finally:
                job.done = True

    t = threading.Thread(target=run, daemon=True, name="job-%s" % rec_id)
    t.start()
    return job
