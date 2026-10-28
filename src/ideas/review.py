"""Every 2 weeks: how did our videos do, and make more of what works (John, 2026-10-28: "you look,
I want everything automated A to Z").

Views, likes and comments of every video posted 2-30 days ago are read from YouTube, the Facebook
Page and Instagram. Formats (and series vs standalone) are ranked by their median views; the ranking
becomes a weight that the idea generator uses to pick the next format, and the best titles are shown
to the brainstorm as "what our audience loved". John gets a short Telegram report.
"""
from __future__ import annotations

import logging
import statistics
import time

import httpx

log = logging.getLogger(__name__)

EVERY_DAYS = 14
MIN_AGE_DAYS, MAX_AGE_DAYS = 2, 30
WEIGHTS_KEY, WINNERS_KEY, LAST_KEY = "format_weights", "what_worked", "review_last_ts"


def _yt_stats(token: str, ids: list[str]) -> dict[str, dict]:
    out = {}
    for k in range(0, len(ids), 50):
        r = httpx.get("https://www.googleapis.com/youtube/v3/videos", timeout=30,
                      params={"part": "statistics", "id": ",".join(ids[k:k + 50])},
                      headers={"Authorization": f"Bearer {token}"})
        r.raise_for_status()
        for it in r.json().get("items", []):
            st = it.get("statistics", {})
            out[it["id"]] = {"views": int(st.get("viewCount", 0)), "likes": int(st.get("likeCount", 0)),
                             "comments": int(st.get("commentCount", 0))}
    return out


def _fb_stats(page_token: str, vid: str) -> dict:
    r = httpx.get(f"https://graph.facebook.com/v23.0/{vid}/video_insights", timeout=30,
                  params={"metric": "blue_reels_play_count", "access_token": page_token})
    views = 0
    if r.status_code == 200:
        for m in r.json().get("data", []):
            views = int((m.get("values") or [{}])[0].get("value") or 0)
    return {"views": views}


def _ig_stats(token: str, media: str, version: str) -> dict:
    r = httpx.get(f"https://graph.instagram.com/{version}/{media}/insights", timeout=30,
                  params={"metric": "views,likes,comments", "access_token": token})
    if r.status_code != 200:
        return {}
    vals = {m["name"]: int((m.get("values") or [{}])[0].get("value") or 0) for m in r.json().get("data", [])}
    return {"views": vals.get("views", 0), "likes": vals.get("likes", 0), "comments": vals.get("comments", 0)}


def _vid(post: dict) -> str:
    return post.get("publish_id") or (post.get("url") or "").rstrip("/").rsplit("/", 1)[-1]


def collect(pipe, now: float | None = None) -> list[dict]:
    """[{job, title, format, series, views, likes, comments}] for videos 2-30 days old."""
    now = now or time.time()
    db, s = pipe.db, pipe.s
    jobs = [j for j in db.q("SELECT * FROM jobs WHERE status='UPLOADED' AND uploaded_at IS NOT NULL")
            if MIN_AGE_DAYS * 86400 <= now - j["uploaded_at"] <= MAX_AGE_DAYS * 86400]
    rows = {j["id"]: {"job": j["id"], "title": j["title"], "format": j["format"] or "?",
                      "series": bool(j["series_id"]), "views": 0, "likes": 0, "comments": 0} for j in jobs}
    posts = {(j["id"], p): db.post(j["id"], p) for j in jobs for p in ("youtube", "facebook", "instagram")}
    done = {k: v for k, v in posts.items() if v and v["status"] == "DONE"}

    def add(job_id: int, st: dict) -> None:
        for k in ("views", "likes", "comments"):
            rows[job_id][k] += int(st.get(k) or 0)
    try:
        from ..upload.youtube_api import YouTubeTokens
        yt = {_vid(v): jid for (jid, p), v in done.items() if p == "youtube"}
        if yt:
            tok = YouTubeTokens(db, s.youtube_client_id, s.youtube_client_secret).access_token()
            for vid, st in _yt_stats(tok, list(yt)).items():
                add(yt[vid], st)
    except Exception as e:  # noqa: BLE001 - one platform down never stops the review
        log.warning("review: youtube skipped: %s", e)
    page = db.get("facebook_page")
    for (jid, p), v in done.items():
        try:
            if p == "facebook" and page:
                add(jid, _fb_stats(page["page_token"], _vid(v)))
            elif p == "instagram":
                tok = (db.get("instagram_tokens") or {}).get("access_token")
                if tok:
                    add(jid, _ig_stats(tok, _vid(v), s.instagram_api_version))
        except Exception as e:  # noqa: BLE001
            log.warning("review: %s %s skipped: %s", p, jid, e)
    return list(rows.values())


def weights(rows: list[dict]) -> dict[str, float]:
    """format -> 0.5..2.0 (its median views over the channel median); formats with < 2 videos stay 1."""
    if len(rows) < 4:
        return {}
    overall = statistics.median(r["views"] for r in rows) or 1
    by: dict[str, list[int]] = {}
    for r in rows:
        by.setdefault(r["format"], []).append(r["views"])
    return {f: round(min(2.0, max(0.5, statistics.median(v) / overall)), 2) for f, v in by.items() if len(v) >= 2}


def report(rows: list[dict], w: dict[str, float]) -> str:
    top = sorted(rows, key=lambda r: r["views"], reverse=True)
    lines = [f"📊 مراجعة الأسبوعين ({len(rows)} فيديو)"]
    lines += [f"🥇 {r['title']}: {r['views']:,} مشاهدة، {r['likes']:,} لايك، {r['comments']:,} كومنت" for r in top[:3]]
    if len(top) > 3:
        lines.append(f"⬇️ أقل واحد: {top[-1]['title']} ({top[-1]['views']:,})")
    ser = [r["views"] for r in rows if r["series"]]
    one = [r["views"] for r in rows if not r["series"]]
    if ser and one:
        lines.append(f"المسلسلات: متوسط {int(statistics.mean(ser)):,} | الفيديوهات المنفصلة: {int(statistics.mean(one)):,}")
    if w:
        more = [f for f, x in sorted(w.items(), key=lambda i: -i[1]) if x > 1.1]
        less = [f for f, x in w.items() if x < 0.9]
        if more:
            lines.append("هنعمل أكتر من: " + "، ".join(more))
        if less:
            lines.append("هنقلل من: " + "، ".join(less))
    lines.append("✅ اتظبط لوحده، مش محتاج منك حاجة")
    return "\n".join(lines)


def maybe_review(pipe, now: float | None = None) -> bool:
    now = now or time.time()
    if now - float(pipe.db.get(LAST_KEY) or 0) < EVERY_DAYS * 86400:
        return False
    if not pipe.db.get(LAST_KEY):  # first run: start the clock, review after two weeks of videos
        pipe.db.put(LAST_KEY, now)
        return False
    rows = collect(pipe, now)
    pipe.db.put(LAST_KEY, now)
    if not rows:
        return False
    w = weights(rows)
    if w:
        pipe.db.put(WEIGHTS_KEY, w)
    pipe.db.put(WINNERS_KEY, [r["title"] for r in sorted(rows, key=lambda r: r["views"], reverse=True)[:5]
                              if r["views"] > 0])
    pipe.tg.send(report(rows, w))
    return True
