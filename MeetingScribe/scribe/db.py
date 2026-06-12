"""SQLite index: recordings, transcript chunks, FTS5 keyword index, embedding vectors.

The database is a rebuildable cache — transcript.json files in the transcripts
folder are the source of truth (see store.rescan).
"""

import json
import sqlite3
import struct
import threading
from pathlib import Path

from . import config

DB_FILE = config.DATA_DIR / "index.db"
_write_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS recordings(
  id TEXT PRIMARY KEY,
  title TEXT,
  mode TEXT,                -- 'quick' | 'meeting'
  created_at TEXT,          -- ISO local time
  duration_s REAL DEFAULT 0,
  folder TEXT,              -- absolute path of the transcript folder
  audio_file TEXT,          -- filename inside folder
  status TEXT,              -- 'processing' | 'done' | 'error'
  error TEXT,
  summary TEXT,
  speakers_json TEXT        -- {"A": "Speaker A", ...} label -> display name
);
CREATE TABLE IF NOT EXISTS chunks(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  rec_id TEXT,
  seq INTEGER,
  speaker TEXT,             -- label ('A','B',...) or '' for quick notes
  start_s REAL,
  end_s REAL,
  text TEXT
);
CREATE INDEX IF NOT EXISTS idx_chunks_rec ON chunks(rec_id, seq);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
  text, speaker_name, rec_id UNINDEXED, chunk_id UNINDEXED
);
CREATE TABLE IF NOT EXISTS vectors(
  chunk_id INTEGER PRIMARY KEY,
  dim INTEGER,
  vec BLOB
);
"""


def connect() -> sqlite3.Connection:
    config.ensure_dirs()
    con = sqlite3.connect(str(DB_FILE), timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    return con


def init():
    with _write_lock, connect() as con:
        con.executescript(SCHEMA)


# ---------- recordings ----------

def upsert_recording(rec: dict):
    cols = ("id", "title", "mode", "created_at", "duration_s", "folder",
            "audio_file", "status", "error", "summary", "speakers_json")
    vals = [rec.get(c) for c in cols]
    with _write_lock, connect() as con:
        con.execute(
            "INSERT INTO recordings(%s) VALUES(%s) ON CONFLICT(id) DO UPDATE SET %s"
            % (",".join(cols), ",".join("?" * len(cols)),
               ",".join("%s=excluded.%s" % (c, c) for c in cols if c != "id")),
            vals,
        )


def update_recording(rec_id: str, **fields):
    if not fields:
        return
    sets = ",".join("%s=?" % k for k in fields)
    with _write_lock, connect() as con:
        con.execute("UPDATE recordings SET %s WHERE id=?" % sets,
                    list(fields.values()) + [rec_id])


def get_recording(rec_id: str):
    with connect() as con:
        row = con.execute("SELECT * FROM recordings WHERE id=?", (rec_id,)).fetchone()
        return dict(row) if row else None


def list_recordings(limit=500):
    with connect() as con:
        rows = con.execute(
            "SELECT * FROM recordings ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def delete_recording(rec_id: str):
    with _write_lock, connect() as con:
        ids = [r[0] for r in con.execute(
            "SELECT id FROM chunks WHERE rec_id=?", (rec_id,)).fetchall()]
        if ids:
            q = ",".join("?" * len(ids))
            con.execute("DELETE FROM vectors WHERE chunk_id IN (%s)" % q, ids)
        con.execute("DELETE FROM chunks_fts WHERE rec_id=?", (rec_id,))
        con.execute("DELETE FROM chunks WHERE rec_id=?", (rec_id,))
        con.execute("DELETE FROM recordings WHERE id=?", (rec_id,))


def month_usage(prefix: str) -> float:
    """Total transcribed seconds for created_at starting with prefix 'YYYY-MM'."""
    with connect() as con:
        row = con.execute(
            "SELECT COALESCE(SUM(duration_s),0) FROM recordings "
            "WHERE created_at LIKE ? AND status='done'", (prefix + "%",)
        ).fetchone()
        return float(row[0] or 0)


# ---------- chunks + fts ----------

def replace_chunks(rec_id: str, chunks: list, speaker_names: dict):
    """chunks: [{seq, speaker, start_s, end_s, text}] — returns inserted chunk ids."""
    with _write_lock, connect() as con:
        old = [r[0] for r in con.execute(
            "SELECT id FROM chunks WHERE rec_id=?", (rec_id,)).fetchall()]
        if old:
            q = ",".join("?" * len(old))
            con.execute("DELETE FROM vectors WHERE chunk_id IN (%s)" % q, old)
        con.execute("DELETE FROM chunks_fts WHERE rec_id=?", (rec_id,))
        con.execute("DELETE FROM chunks WHERE rec_id=?", (rec_id,))
        ids = []
        for ch in chunks:
            cur = con.execute(
                "INSERT INTO chunks(rec_id, seq, speaker, start_s, end_s, text) "
                "VALUES(?,?,?,?,?,?)",
                (rec_id, ch["seq"], ch.get("speaker") or "", ch.get("start_s"),
                 ch.get("end_s"), ch["text"]),
            )
            cid = cur.lastrowid
            ids.append(cid)
            disp = speaker_names.get(ch.get("speaker") or "", "")
            con.execute(
                "INSERT INTO chunks_fts(text, speaker_name, rec_id, chunk_id) "
                "VALUES(?,?,?,?)", (ch["text"], disp, rec_id, cid))
        return ids


def refresh_fts_speakers(rec_id: str, speaker_names: dict):
    """After a speaker rename, refresh display names in the FTS index."""
    with _write_lock, connect() as con:
        rows = con.execute(
            "SELECT id, speaker, text FROM chunks WHERE rec_id=?", (rec_id,)).fetchall()
        con.execute("DELETE FROM chunks_fts WHERE rec_id=?", (rec_id,))
        for r in rows:
            disp = speaker_names.get(r["speaker"] or "", "")
            con.execute(
                "INSERT INTO chunks_fts(text, speaker_name, rec_id, chunk_id) "
                "VALUES(?,?,?,?)", (r["text"], disp, rec_id, r["id"]))


def get_chunks(rec_id: str):
    with connect() as con:
        rows = con.execute(
            "SELECT * FROM chunks WHERE rec_id=? ORDER BY seq", (rec_id,)).fetchall()
        return [dict(r) for r in rows]


def chunks_by_ids(ids):
    if not ids:
        return {}
    with connect() as con:
        q = ",".join("?" * len(ids))
        rows = con.execute("SELECT * FROM chunks WHERE id IN (%s)" % q, list(ids)).fetchall()
        return {r["id"]: dict(r) for r in rows}


# ---------- vectors ----------

def store_vectors(pairs):
    """pairs: [(chunk_id, list_of_floats)]"""
    with _write_lock, connect() as con:
        for cid, vec in pairs:
            blob = struct.pack("%df" % len(vec), *vec)
            con.execute(
                "INSERT OR REPLACE INTO vectors(chunk_id, dim, vec) VALUES(?,?,?)",
                (cid, len(vec), blob))


def all_vectors():
    """Returns (chunk_ids, list of float lists). Kept simple; caller caches."""
    with connect() as con:
        rows = con.execute("SELECT chunk_id, dim, vec FROM vectors").fetchall()
    ids, vecs = [], []
    for r in rows:
        ids.append(r["chunk_id"])
        vecs.append(list(struct.unpack("%df" % r["dim"], r["vec"])))
    return ids, vecs


def chunks_missing_vectors(rec_id=None):
    sql = ("SELECT c.id, c.text FROM chunks c LEFT JOIN vectors v ON v.chunk_id=c.id "
           "WHERE v.chunk_id IS NULL")
    args = ()
    if rec_id:
        sql += " AND c.rec_id=?"
        args = (rec_id,)
    with connect() as con:
        return [(r["id"], r["text"]) for r in con.execute(sql, args).fetchall()]


def keyword_search(query: str, limit=30):
    """FTS5 search; returns [(chunk_id, rec_id, bm25_rank)] best first."""
    words = [w for w in "".join(
        c if (c.isalnum() or c in "'-") else " " for c in query).split() if w]
    if not words:
        return []
    with connect() as con:
        for joiner in (" ", " OR "):
            match = joiner.join('"%s"' % w.replace('"', "") for w in words)
            try:
                rows = con.execute(
                    "SELECT chunk_id, rec_id, bm25(chunks_fts) AS rank "
                    "FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY rank LIMIT ?",
                    (match, limit)).fetchall()
            except sqlite3.OperationalError:
                rows = []
            if rows:
                return [(r["chunk_id"], r["rec_id"], r["rank"]) for r in rows]
    return []


def wipe_index():
    with _write_lock, connect() as con:
        con.execute("DELETE FROM vectors")
        con.execute("DELETE FROM chunks_fts")
        con.execute("DELETE FROM chunks")
        con.execute("DELETE FROM recordings")
