"""Viewers choose what happens next (John, 2026-10-28: "so there is interaction").

Part 1 of a series ends with a 2-way question ("اكتب 1 لو الطماطم و 2 لو الخيارة"). Parts 2 and 3
are only made VOTE_HOURS after part 1 went out: the comments on YouTube, the Facebook Page and Instagram are
counted, then Gemini rewrites parts 2-3 so the winning choice is what happens, and part 2 opens by
telling the viewers they chose it. TikTok has no comment API for us: those are not counted.
"""
from __future__ import annotations

import json
import logging
import re
import time

import httpx

from ..llm import LLMUnavailable, extract_json
from .schema import Story

log = logging.getLogger(__name__)

VOTE_HOURS = 18  # part 1 posted in the afternoon/evening -> votes until the next day's slots

ONE = re.compile(r"(^|[^0-9٠-٩])(1|١)([^0-9٠-٩]|$)|\bواحد\b|الاول|الأول")
TWO = re.compile(r"(^|[^0-9٠-٩])(2|٢)([^0-9٠-٩]|$)|\bاتنين\b|اثنين|التاني|الثاني")


def choice_of(text: str, options: list[str]) -> int | None:
    """0 / 1 for a comment that picks one side, None for anything else (both, neither)."""
    t = text.strip()
    a = bool(ONE.search(t)) or (options[0] and options[0] in t)
    b = bool(TWO.search(t)) or (options[1] and options[1] in t)
    if a == b:
        return None
    return 0 if a else 1


def youtube_comments(token: str, video_id: str) -> list[str]:
    out, page = [], None
    for _ in range(5):
        r = httpx.get("https://www.googleapis.com/youtube/v3/commentThreads", timeout=30,
                      params={"part": "snippet", "videoId": video_id, "maxResults": 100, "textFormat": "plainText",
                              **({"pageToken": page} if page else {})},
                      headers={"Authorization": f"Bearer {token}"})
        if r.status_code != 200:
            log.warning("youtube comments %s: %s", r.status_code, r.text[:200])
            break
        data = r.json()
        out += [i["snippet"]["topLevelComment"]["snippet"]["textDisplay"] for i in data.get("items", [])]
        page = data.get("nextPageToken")
        if not page:
            break
    return out


def facebook_comments(page_token: str, video_id: str) -> list[str]:
    r = httpx.get(f"https://graph.facebook.com/v23.0/{video_id}/comments", timeout=30,
                  params={"fields": "message", "limit": 200, "filter": "stream", "access_token": page_token})
    if r.status_code != 200:
        log.warning("facebook comments %s: %s", r.status_code, r.text[:200])
        return []
    return [c.get("message", "") for c in r.json().get("data", [])]


def instagram_comments(token: str, media_id: str, version: str) -> list[str]:
    r = httpx.get(f"https://graph.instagram.com/{version}/{media_id}/comments", timeout=30,
                  params={"fields": "text", "limit": 100, "access_token": token})
    if r.status_code != 200:
        log.warning("instagram comments %s: %s", r.status_code, r.text[:200])
        return []
    return [c.get("text", "") for c in r.json().get("data", [])]


def tally(pipe, job: dict, story: Story) -> list[int]:
    """[votes for option 1, votes for option 2] over the platforms we can read."""
    counts = [0, 0]
    texts: list[str] = []
    yt = pipe.db.post(job["id"], "youtube")
    if yt and yt["status"] == "DONE":
        vid = yt["publish_id"] or (yt["url"] or "").rstrip("/").rsplit("/", 1)[-1]
        try:
            from ..upload.youtube_api import YouTubeTokens
            tok = YouTubeTokens(pipe.db, pipe.s.youtube_client_id, pipe.s.youtube_client_secret).access_token()
            texts += youtube_comments(tok, vid)
        except Exception as e:  # noqa: BLE001 - a vote must never stop the series
            log.warning("youtube votes skipped: %s", e)
    fb = pipe.db.post(job["id"], "facebook")
    page = pipe.db.get("facebook_page")
    if fb and fb["status"] == "DONE" and page:
        vid = fb["publish_id"] or (fb["url"] or "").rstrip("/").rsplit("/", 1)[-1]
        try:
            texts += facebook_comments(page["page_token"], vid)
        except Exception as e:  # noqa: BLE001
            log.warning("facebook votes skipped: %s", e)
    ig = pipe.db.post(job["id"], "instagram")
    ig_tok = (pipe.db.get("instagram_tokens") or {}).get("access_token")
    if ig and ig["status"] == "DONE" and ig_tok and ig["publish_id"]:
        try:
            texts += instagram_comments(ig_tok, ig["publish_id"], pipe.s.instagram_api_version)
        except Exception as e:  # noqa: BLE001
            log.warning("instagram votes skipped: %s", e)
    for t in texts:
        c = choice_of(t, story.vote["options"])
        if c is not None:
            counts[c] += 1
    return counts


REWRITE = """Part 1 of our mini-series "{series}" is already online. It ended with this question to the viewers:
"{question}"  option 1 = {o1}, option 2 = {o2}.
The viewers voted: {votes}. The winner is: {winner}.

Part 1 (already aired, do not change it):
{part1}

Our earlier draft of parts 2 and 3 (rewrite them so the WINNING choice is what really happens; keep the
same characters, ids, names, voices and looks):
{drafts}

Rules: everything from the original brief still applies (Egyptian dialect, kids-friendly, no violence
words, 5-7 scenes and at least 85 words per part, part 2 ends on a cliffhanger, part 3 is the biggest
episode). Part 2's FIRST line tells the viewers they chose this ("إنتوا اخترتوا {winner}!... ") and then
jumps into the action. Return ONLY JSON: {{"parts": [STORY, STORY]}} with each STORY in the same shape as
the drafts."""


def rewrite_rest(pipe, series_jobs: list[dict], counts: list[int]) -> bool:
    """Rewrite the not-yet-made parts after part 1 to follow the vote. False = keep the drafts."""
    part1 = Story.model_validate_json(series_jobs[0]["story_json"])
    rest = [j for j in series_jobs[1:] if j["status"] in ("NEW", "IDEA")]
    if not rest or not part1.vote or sum(counts) == 0:
        return False  # no votes: the drafts already follow option 1
    o = part1.vote["options"]
    win = 0 if counts[0] >= counts[1] else 1
    prompt = REWRITE.format(series=series_jobs[0].get("series_title") or "", question=part1.vote["question_ar"],
                            o1=o[0], o2=o[1], votes=f"{counts[0]} for 1, {counts[1]} for 2", winner=o[win],
                            part1=part1.model_dump_json(), drafts=json.dumps(
                                [json.loads(j["story_json"]) for j in rest], ensure_ascii=False))
    feedback = ""
    for attempt in range(3):
        try:
            data = extract_json(pipe.llm.text(prompt + feedback, json_mode=True, temperature=0.9))
            parts = [Story.model_validate(p) for p in data["parts"]][: len(rest)]
            if len(parts) != len(rest):
                raise ValueError(f"need {len(rest)} parts")
            break
        except LLMUnavailable:
            return False
        except Exception as e:  # noqa: BLE001 - invalid answer: tell Gemini and try again
            feedback = f"\n\nYour previous answer was rejected: {str(e)[:300]}\nFix it."
    else:
        return False
    from .proofread import proofread
    parts = proofread(pipe.llm, parts)
    for j, st in zip(rest, parts):
        st = pipe.ideas._apply_library(st)
        pipe.db.update_job(j["id"], story_json=st.model_dump_json(), title=st.title)
        pipe._write(pipe.job_dir(j["id"]) / "story.json", st.model_dump())
    log.info("series %s rewritten for vote %s -> %s", series_jobs[0]["series_id"], counts, o[win])
    return True


def gate(pipe, job: dict) -> bool:
    """True while this series part must wait for the viewers' vote on part 1. Runs the count and
    the rewrite once the voting time is over."""
    if not job.get("series_id") or (job.get("part") or 1) < 2:
        return False
    sid = job["series_id"]
    if pipe.db.get(f"vote_done:{sid}"):
        return False
    jobs = pipe.db.series_jobs(sid)
    first = jobs[0]
    try:
        p1 = Story.model_validate_json(first["story_json"])
    except Exception:  # noqa: BLE001
        return False
    if not p1.vote:
        return False
    if first["status"] != "UPLOADED" or time.time() - (first["uploaded_at"] or 0) < VOTE_HOURS * 3600:
        return True
    counts = tally(pipe, first, p1)
    changed = rewrite_rest(pipe, jobs, counts)
    pipe.db.put(f"vote_done:{sid}", {"counts": counts, "rewritten": changed})
    o = p1.vote["options"]
    pipe.tg.send(f"🗳️ نتيجة التصويت على «{first.get('series_title') or first['title']}»:\n"
                 f"1) {o[0]}: {counts[0]}   2) {o[1]}: {counts[1]}\n"
                 + ("الجزء التاني اتكتب على اختيار الجمهور ✅" if changed else "مكملين بالقصة الأصلية"))
    return False
