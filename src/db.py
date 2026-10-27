"""SQLite state: jobs, idea history, characters, quota usage, tokens, post slots."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

# Stage order. A job's status is the last stage it COMPLETED.
STAGES = ["NEW", "IDEA", "IMAGES", "AUDIO", "CLIPS", "COMPOSED", "QA_PASSED", "UPLOADED"]
TERMINAL = {"UPLOADED", "FAILED"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    status TEXT NOT NULL DEFAULT 'NEW',
    engine TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    title TEXT,
    format TEXT,
    story_json TEXT,
    qa_json TEXT,
    error TEXT,
    failed_stage TEXT,
    video_path TEXT,
    publish_id TEXT,
    post_url TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    uploaded_at REAL
);
CREATE TABLE IF NOT EXISTS characters (
    id TEXT PRIMARY KEY,
    name_ar TEXT NOT NULL,
    voice TEXT NOT NULL,
    look TEXT NOT NULL,
    pitch INTEGER NOT NULL DEFAULT 0,
    image_path TEXT,
    mouth_json TEXT,
    episodes INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS quota (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    service TEXT NOT NULL,
    amount REAL NOT NULL,
    note TEXT,
    ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS posts (
    job_id INTEGER NOT NULL,
    platform TEXT NOT NULL,
    status TEXT NOT NULL,          -- DONE | RETRY | FAILED
    attempts INTEGER NOT NULL DEFAULT 0,
    publish_id TEXT,
    url TEXT,
    error TEXT,
    updated_at REAL NOT NULL,
    PRIMARY KEY (job_id, platform)
);
CREATE TABLE IF NOT EXISTS post_slots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    day TEXT NOT NULL,
    due_ts REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING',
    job_id INTEGER,
    UNIQUE(day, due_ts)
);
"""


class DB:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)
        # columns added after the first release (3-part mini-series)
        have = {r["name"] for r in self._conn.execute("PRAGMA table_info(jobs)")}
        for col, typ in (("series_id", "TEXT"), ("series_title", "TEXT"), ("part", "INTEGER"),
                         ("parts", "INTEGER")):
            if col not in have:
                self._conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} {typ}")

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def q(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, args).fetchall()

    def x(self, sql: str, args: tuple = ()) -> int:
        with self._lock:
            cur = self._conn.execute(sql, args)
            return cur.lastrowid or cur.rowcount

    # --- jobs -------------------------------------------------------------
    def new_job(self, engine: str, **fields: Any) -> int:
        """fields: optional status/title/format/story_json/series_id/series_title/part/parts."""
        now = time.time()
        fields = {"status": "NEW", **fields, "engine": engine, "created_at": now, "updated_at": now}
        cols = ", ".join(fields)
        return self.x(f"INSERT INTO jobs({cols}) VALUES({','.join('?' * len(fields))})",
                      tuple(fields.values()))

    def job(self, job_id: int) -> dict[str, Any]:
        rows = self.q("SELECT * FROM jobs WHERE id=?", (job_id,))
        if not rows:
            raise KeyError(job_id)
        return dict(rows[0])

    def update_job(self, job_id: int, **fields: Any) -> None:
        fields["updated_at"] = time.time()
        cols = ", ".join(f"{k}=?" for k in fields)
        self.x(f"UPDATE jobs SET {cols} WHERE id=?", (*fields.values(), job_id))

    def set_status(self, job_id: int, status: str, **fields: Any) -> None:
        assert status in STAGES or status == "FAILED", status
        self.update_job(job_id, status=status, **fields)

    def unfinished_jobs(self) -> list[dict]:
        rows = self.q(
            "SELECT * FROM jobs WHERE status NOT IN ('QA_PASSED','UPLOADED','FAILED') ORDER BY id"
        )
        return [dict(r) for r in rows]

    def ready_jobs(self) -> list[dict]:
        return [dict(r) for r in self.q("SELECT * FROM jobs WHERE status='QA_PASSED' ORDER BY id")]

    def postable_jobs(self) -> list[dict]:
        """Ready videos that may go out now: a series part only after the previous part is posted."""
        out = []
        for j in self.ready_jobs():
            if j.get("series_id") and (j.get("part") or 1) > 1:
                prev = self.q("SELECT status FROM jobs WHERE series_id=? AND part=?",
                              (j["series_id"], j["part"] - 1))
                if not prev or prev[0]["status"] != "UPLOADED":
                    continue
            out.append(j)
        # finish a running series before starting anything new
        out.sort(key=lambda j: (0 if j.get("series_id") and (j.get("part") or 1) > 1 else 1, j["id"]))
        return out

    def series_jobs(self, series_id: str) -> list[dict]:
        return [dict(r) for r in self.q("SELECT * FROM jobs WHERE series_id=? ORDER BY part", (series_id,))]

    def recent_titles(self, n: int = 50) -> list[str]:
        rows = self.q(
            "SELECT title FROM jobs WHERE title IS NOT NULL ORDER BY id DESC LIMIT ?", (n,)
        )
        return [r["title"] for r in rows]

    def recent_formats(self, n: int = 8) -> list[str]:
        rows = self.q(
            "SELECT format FROM jobs WHERE format IS NOT NULL ORDER BY id DESC LIMIT ?", (n,)
        )
        return [r["format"] for r in rows]

    def last_upload_ts(self) -> float | None:
        rows = self.q("SELECT MAX(uploaded_at) AS t FROM jobs WHERE status='UPLOADED'")
        return rows[0]["t"] if rows else None

    # --- per-platform posts ------------------------------------------------
    def post(self, job_id: int, platform: str) -> dict | None:
        rows = self.q("SELECT * FROM posts WHERE job_id=? AND platform=?", (job_id, platform))
        return dict(rows[0]) if rows else None

    def record_post(self, job_id: int, platform: str, status: str, **f: Any) -> None:
        prev = self.post(job_id, platform)
        attempts = (prev["attempts"] if prev else 0) + 1
        self.x("INSERT INTO posts(job_id, platform, status, attempts, publish_id, url, error, updated_at) "
               "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(job_id, platform) DO UPDATE SET status=excluded.status, "
               "attempts=excluded.attempts, publish_id=excluded.publish_id, url=excluded.url, "
               "error=excluded.error, updated_at=excluded.updated_at",
               (job_id, platform, status, attempts, f.get("publish_id"), f.get("url"), f.get("error"), time.time()))

    def posts_to_retry(self, max_attempts: int, since_ts: float) -> list[dict]:
        return [dict(r) for r in self.q(
            "SELECT * FROM posts WHERE status='RETRY' AND attempts<? AND updated_at>=? ORDER BY job_id",
            (max_attempts, since_ts))]

    # --- characters -------------------------------------------------------
    def characters(self) -> list[dict]:
        return [dict(r) for r in self.q("SELECT * FROM characters ORDER BY episodes DESC")]

    def character(self, cid: str) -> dict | None:
        rows = self.q("SELECT * FROM characters WHERE id=?", (cid,))
        return dict(rows[0]) if rows else None

    def upsert_character(self, cid: str, **f: Any) -> None:
        existing = self.character(cid)
        if existing is None:
            f.setdefault("created_at", time.time())
            cols = ["id", *f.keys()]
            self.x(
                f"INSERT INTO characters({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
                (cid, *f.values()),
            )
        elif f:
            cols = ", ".join(f"{k}=?" for k in f)
            self.x(f"UPDATE characters SET {cols} WHERE id=?", (*f.values(), cid))

    # --- quota ------------------------------------------------------------
    def add_quota(self, service: str, amount: float, note: str = "") -> None:
        self.x(
            "INSERT INTO quota(service, amount, note, ts) VALUES(?,?,?,?)",
            (service, amount, note, time.time()),
        )

    def quota_since(self, service: str, since_ts: float) -> float:
        rows = self.q(
            "SELECT COALESCE(SUM(amount),0) AS s FROM quota WHERE service=? AND ts>=?",
            (service, since_ts),
        )
        return float(rows[0]["s"])

    # --- kv (tokens, alert throttles) --------------------------------------
    def get(self, key: str, default: Any = None) -> Any:
        rows = self.q("SELECT value FROM kv WHERE key=?", (key,))
        return json.loads(rows[0]["value"]) if rows else default

    def put(self, key: str, value: Any) -> None:
        self.x(
            "INSERT INTO kv(key, value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value, ensure_ascii=False)),
        )
