"""Shared state for the GitHub runner and the PC backup (John, 2026-10-28: "move everything to GitHub,
keep the PC as a backup").

The whole working state - the SQLite DB (jobs, post slots, platform tokens) plus the folders of jobs
that are still in flight - is packed, encrypted with Fernet (STATE_KEY) and kept in R2. The bucket
has a public r2.dev URL, so the object name is derived from the key too and the bytes are useless
without it. A small plaintext lease says who ran last; the PC only takes over when GitHub has been
quiet for hours.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import shutil
import socket
import sqlite3
import tarfile
import time
from pathlib import Path

import httpx
from cryptography.fernet import Fernet

from .upload import r2

log = logging.getLogger(__name__)

KEEP_UPLOADED_S = 3 * 86400  # finished videos stay a few days for per-platform retries
SKIP_PARTS = ("image_candidates", "seg_", "concat.txt", "video.mp4", "voice.wav", ".tmp")


def _fernet(key: str) -> Fernet:
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(key.encode()).digest()))


def _object(s, name: str) -> str:
    # state_ns "state" is the real channel; another name (e.g. "dryrun") is a separate test copy
    return f"{s.state_ns}/{hashlib.sha256(('obj' + s.state_key).encode()).hexdigest()[:32]}/{name}"


def enabled(s) -> bool:
    return bool(r2.enabled(s) and s.state_key)


def _get(s, name: str) -> bytes | None:
    r = httpx.get(r2.presign(s, "GET", _object(s, name)), timeout=300)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.content


def _put(s, name: str, data: bytes) -> None:
    tmp = s.data_dir / f".{name}.upload"
    tmp.write_bytes(data)
    try:
        r2.put_public(s, tmp, _object(s, name), "application/octet-stream")
    finally:
        tmp.unlink(missing_ok=True)


# --- lease ---------------------------------------------------------------------
def lease(s) -> dict:
    raw = _get(s, "lease.json")
    return json.loads(raw) if raw else {}


def write_lease(s, owner: str, note: str = "") -> None:
    _put(s, "lease.json", json.dumps({"owner": owner, "ts": time.time(), "host": socket.gethostname(),
                                      "note": note}).encode())


# --- pack / unpack ---------------------------------------------------------------
def _jobs_to_keep(db_path: Path) -> list[int]:
    con = sqlite3.connect(db_path)
    try:
        rows = con.execute("SELECT id, status, COALESCE(uploaded_at, 0) FROM jobs").fetchall()
    except sqlite3.OperationalError:
        rows = [(i, st, 0) for i, st in con.execute("SELECT id, status FROM jobs").fetchall()]
    finally:
        con.close()
    now = time.time()
    return [i for i, st, up in rows
            if st != "FAILED" and (st != "UPLOADED" or now - (up or 0) < KEEP_UPLOADED_S)]


def pack(s) -> bytes:
    """tar.gz of the DB (a consistent copy), the character cache and the in-flight job folders."""
    buf = io.BytesIO()
    snap = s.data_dir / ".state_snapshot.db"
    src = sqlite3.connect(s.db_path)
    dst = sqlite3.connect(snap)
    src.backup(dst)
    dst.close()
    src.close()
    try:
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            tar.add(snap, "pipeline.db")
            info = tarfile.TarInfo("manifest.json")
            man = json.dumps({"data_dir": str(s.data_dir), "ts": time.time()}).encode()
            info.size = len(man)
            tar.addfile(info, io.BytesIO(man))
            if s.cache_dir.exists():
                tar.add(s.cache_dir, "cache")
            for jid in _jobs_to_keep(snap):
                d = s.jobs_dir / f"{jid:05d}"
                if d.exists():
                    tar.add(d, f"jobs/{d.name}",
                            filter=lambda ti: None if any(p in ti.name for p in SKIP_PARTS) else ti)
    finally:
        snap.unlink(missing_ok=True)
    return buf.getvalue()


def _rebase(value, old: str, new: str):
    """Saved jobs hold absolute paths from the machine that wrote them; point them at this one."""
    if isinstance(value, str):
        v = value.replace("\\", "/")
        o = old.replace("\\", "/").rstrip("/")
        if v.startswith(o + "/"):
            return str(Path(new) / v[len(o) + 1:])
        return value
    if isinstance(value, list):
        return [_rebase(x, old, new) for x in value]
    if isinstance(value, dict):
        return {k: _rebase(x, old, new) for k, x in value.items()}
    return value


def unpack(s, blob: bytes) -> None:
    s.data_dir.mkdir(parents=True, exist_ok=True)
    stage = s.data_dir / ".state_in"
    shutil.rmtree(stage, ignore_errors=True)
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        tar.extractall(stage, filter="data")
    old = json.loads((stage / "manifest.json").read_text(encoding="utf-8"))["data_dir"]
    new = str(s.data_dir)
    for f in stage.rglob("*.json"):
        if f.name == "manifest.json":
            continue
        try:
            f.write_text(json.dumps(_rebase(json.loads(f.read_text(encoding="utf-8")), old, new),
                                    ensure_ascii=False, indent=1), encoding="utf-8")
        except (ValueError, UnicodeDecodeError):
            pass
    con = sqlite3.connect(stage / "pipeline.db")
    for (table,) in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
        cols = [c[1] for c in con.execute(f"PRAGMA table_info({table})") if c[2].upper() in ("TEXT", "")]
        for c in cols:
            for rowid, v in con.execute(f"SELECT rowid, {c} FROM {table} WHERE {c} LIKE '%jobs%'").fetchall():
                if isinstance(v, str):
                    nv = _rebase(v, old, new)
                    if nv == v and v.lstrip().startswith(("{", "[")):
                        try:
                            nv = json.dumps(_rebase(json.loads(v), old, new), ensure_ascii=False)
                        except ValueError:
                            pass
                    if nv != v:
                        con.execute(f"UPDATE {table} SET {c}=? WHERE rowid=?", (nv, rowid))
    con.commit()
    con.close()
    # swap in: DB (and its WAL files) + cache + job folders
    for ext in ("", "-wal", "-shm"):
        Path(str(s.db_path) + ext).unlink(missing_ok=True)
    shutil.move(str(stage / "pipeline.db"), s.db_path)
    if (stage / "cache").exists():
        shutil.rmtree(s.cache_dir, ignore_errors=True)
        shutil.move(str(stage / "cache"), s.cache_dir)
    s.jobs_dir.mkdir(parents=True, exist_ok=True)
    for d in (stage / "jobs").iterdir() if (stage / "jobs").exists() else []:
        target = s.jobs_dir / d.name
        shutil.rmtree(target, ignore_errors=True)
        shutil.move(str(d), target)
    shutil.rmtree(stage, ignore_errors=True)


def pull(s) -> bool:
    """Replace local state with the shared copy. False when there is no shared copy yet."""
    blob = _get(s, "state.bin")
    if blob is None:
        return False
    unpack(s, _fernet(s.state_key).decrypt(blob))
    log.info("state pulled (%d KB)", len(blob) // 1024)
    return True


def push(s) -> None:
    blob = _fernet(s.state_key).encrypt(pack(s))
    _put(s, "state.bin", blob)
    log.info("state pushed (%d KB)", len(blob) // 1024)
