"""One long YouTube video a week with every episode of the week (John, 2026-10-28).

Each posted video is copied to R2 (archive/<week>/...) with its cover; on Friday evening (Cairo) the
week's episodes are joined in order, with YouTube chapters (one timestamp per episode) in the
description, and uploaded as a normal (non-Shorts) video: long watch time is what YouTube pays for.
The archive copies are deleted afterwards.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import logging
import shutil
from pathlib import Path

import httpx

from ..upload import r2
from ..util import ffprobe_duration, run

log = logging.getLogger(__name__)

DAY, HOUR = 4, 18  # Friday (Monday=0), from 18:00 Cairo
MIN_EPISODES = 5


def week_key(now: dt.datetime) -> str:
    """The Friday whose evening compilation this moment belongs to (Fri 18:00 -> next Fri 18:00)."""
    days = (DAY - now.weekday()) % 7
    if days == 0 and now.hour >= HOUR:
        days = 7
    return (now.date() + dt.timedelta(days=days)).isoformat()


def _prefix(s, week: str) -> str:
    return f"archive/{hashlib.sha256(('wk' + (s.state_key or s.r2_secret_key)).encode()).hexdigest()[:24]}/{week}"


def archive(pipe, job: dict, story) -> None:
    s = pipe.s
    if not r2.enabled(s) or not s.upload_enabled:  # dry runs post nothing, so nothing to collect
        return
    video = Path(job["video_path"])
    if not video.exists():
        return
    week = week_key(dt.datetime.now(pipe_tz(pipe)))
    key = f"{_prefix(s, week)}/{job['id']:05d}.mp4"
    r2.put_public(s, video, key)
    cover = video.parent / "cover.jpg"
    if cover.exists():
        r2.put_public(s, cover, key.replace(".mp4", ".jpg"), "image/jpeg")
    rows = pipe.db.get(f"week:{week}") or []
    rows.append({"job": job["id"], "title": story.title, "key": key,
                 "series": job.get("series_title") or "", "part": job.get("part")})
    pipe.db.put(f"week:{week}", rows)


def pipe_tz(pipe):
    from zoneinfo import ZoneInfo
    return ZoneInfo(pipe.s.timezone)


def _ts(sec: float) -> str:
    sec = int(sec)
    return f"{sec // 60}:{sec % 60:02d}" if sec < 3600 else f"{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def build(rows: list[dict], files: list[Path], out: Path) -> tuple[Path, list[tuple[float, str]]]:
    """Join the episodes (re-encoded to one format) and return the chapter start times."""
    starts, t = [], 0.0
    for r, f in zip(rows, files):
        starts.append((t, r["title"] + (f" (الجزء {r['part']})" if r.get("series") else "")))
        t += ffprobe_duration(f)
    inputs = sum((["-i", str(f)] for f in files), [])
    n = len(files)
    chain = "".join(f"[{i}:v]scale=1080:1920,setsar=1,fps=30,format=yuv420p[v{i}];[{i}:a]aresample=44100,"
                    f"aformat=channel_layouts=stereo[a{i}];" for i in range(n))
    chain += "".join(f"[v{i}][a{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=1[v][a]"
    run(["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", chain, "-map", "[v]", "-map", "[a]",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-c:a", "aac", "-b:a", "160k",
         "-movflags", "+faststart", str(out)], timeout=3600)
    return out, starts


def description(starts: list[tuple[float, str]], channel: str) -> str:
    # YouTube makes chapters from a list that starts at 0:00, has 3+ entries of 10+ seconds each
    lines = [f"{_ts(t)} {title}" for t, title in starts]
    brand = "#" + "_".join(channel.split()) if channel else ""
    return ("كل حكايات الخضار والفاكهة بتاعة الأسبوع في فيديو واحد 🥗😂\n"
            "اكتبولنا في الكومنتات: أنهي حلقة كانت الأحلى؟ 👇\n\n" + "\n".join(lines)
            + f"\n\n{brand} #كوميديا_مصرية #حكايات_الخضار").strip()


def maybe_post_weekly(pipe) -> bool:
    """Friday evening: make and upload last 7 days' compilation once. True when one went up."""
    s = pipe.s
    now = dt.datetime.now(pipe_tz(pipe))
    if now.weekday() != DAY or now.hour < HOUR:
        return False
    week = now.date().isoformat()  # today is that Friday
    if pipe.db.get(f"weekly_done:{week}"):
        return False
    rows = pipe.db.get(f"week:{week}") or []
    ups = pipe.uploaders()
    if len(rows) < MIN_EPISODES or "youtube" not in ups:
        pipe.db.put(f"weekly_done:{week}", {"skipped": len(rows)})
        return False
    work = s.data_dir / "weekly" / week
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    files = []
    for r in rows:
        f = work / Path(r["key"]).name
        resp = httpx.get(r2.presign(s, "GET", r["key"]), timeout=300)
        resp.raise_for_status()
        f.write_bytes(resp.content)
        files.append(f)
    cover = work / "cover.jpg"
    try:
        resp = httpx.get(r2.presign(s, "GET", rows[0]["key"].replace(".mp4", ".jpg")), timeout=60)
        if resp.status_code == 200:
            cover.write_bytes(resp.content)
    except httpx.HTTPError:
        pass
    video, starts = build(rows, files, work / "weekly.mp4")
    title = f"كل حلقات الأسبوع 🥗 نهارك سلطة | حكايات الخضار المصرية ({now:%d/%m})"
    res = ups["youtube"].upload(video, description(starts, s.channel_name), title=title,
                                tags=["نهارك سلطة", "كرتون مصري", "حكايات الخضار", "كوميديا مصرية"],
                                cover=cover if cover.exists() else None, short=False)
    pipe.db.put(f"weekly_done:{week}", {"url": res.url, "episodes": len(rows)})
    pipe.tg.send(f"📺 فيديو الأسبوع الطويل نزل على YouTube ({len(rows)} حلقة، {_ts(ffprobe_duration(video))})\n{res.url}")
    for r in rows:
        r2.delete(s, r["key"])
        r2.delete(s, r["key"].replace(".mp4", ".jpg"))
    shutil.rmtree(work, ignore_errors=True)
    return True
