"""Countdown story before the next series part (John, 2026-10-28: "a story 2 hours before part 2").

About two hours before a series part is due, an Instagram story and a Facebook Page story go up: the
last moment of the previous part with "الجزء التاني الساعة 9 بالليل - مين مستني؟". The picture is a
clean scene picture of the new part (no captions, no band: John, 2026-10-28). Story stickers (polls, links) are not offered by the APIs, so the story is a plain picture.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import math
import time
from pathlib import Path

import httpx

from ..config import CAPTION_FONT, FONTS_DIR
from ..upload import r2
from ..util import ff_path, ffprobe_duration, run

log = logging.getLogger(__name__)

LEAD_H = 2.5  # a story goes up when the slot is at most this far away (the hourly run makes it ~2 h)
RUN_MINUTE = 0  # the Cloudflare clock starts a run on the hour, so a due video posts at the next :00
PART_WORD = {2: "التاني", 3: "التالت والأخير", 4: "الرابع", 5: "الخامس"}


def post_time(due_ts: float, tz) -> dt.datetime:
    t = dt.datetime.fromtimestamp(due_ts, tz)
    run_at = t.replace(minute=RUN_MINUTE, second=0, microsecond=0)
    return run_at if run_at >= t else run_at + dt.timedelta(hours=1)


def when_text(t: dt.datetime) -> str:
    h = str(t.hour % 12 or 12).translate(str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩"))  # Arabic digits: no RTL jumble
    part = "الصبح" if t.hour < 12 else "الضهر" if t.hour < 15 else "العصر" if t.hour < 18 else "بالليل"
    return f"الساعة {h} {part}" if t.minute < 30 else f"الساعة {h} ونص {part}"


def _ass(lines: list[str], out: Path) -> Path:
    out.write_text(
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\nWrapStyle: 0\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Big,{CAPTION_FONT},104,&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,1,0,0,0,100,100,0,0,1,7,3,2,60,60,420,1\n"
        f"Style: Small,{CAPTION_FONT},84,&H0000D7FF,&H0000D7FF,&H00000000,&H96000000,1,0,0,0,100,100,0,0,1,6,3,2,60,60,250,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        f"Dialogue: 0,0:00:00.00,0:00:05.00,Big,,0,0,0,,{lines[0]}\n"
        f"Dialogue: 0,0:00:00.00,0:00:05.00,Small,,0,0,0,,{lines[1]}\n", encoding="utf-8")
    return out


def render(picture: Path, lines: list[str], out: Path) -> Path:
    """1080x1920 jpg: the picture filled to the frame, darker at the bottom, two lines of text."""
    ass = _ass(lines, out.with_suffix(".ass"))
    vf = ("scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
          f"ass=filename='{ff_path(ass)}':fontsdir='{ff_path(FONTS_DIR)}'")
    run(["ffmpeg", "-y", "-v", "error", "-i", str(picture), "-vf", vf, "-frames:v", "1", "-q:v", "2", str(out)])
    return out


def _clean_picture(jd: Path, last: bool) -> Path | None:
    """A scene picture without any writing on it (the video frames carry the captions)."""
    scenes = sorted((jd / "images").glob("scene_*.jpg"))
    if scenes:
        return scenes[-1] if last else scenes[0]
    return next((jd / n for n in ("cover_art.jpg",) if (jd / n).exists()), None)


def _picture(pipe, prev: dict, nxt: dict, work: Path) -> Path | None:
    """A clean scene picture of the new part (John: no frame with captions, no black band)."""
    return _clean_picture(pipe.job_dir(nxt["id"]), last=False)


def _instagram(pipe, image_url: str) -> str:
    from ..upload.instagram_api import GRAPH, InstagramTokens
    s = pipe.s
    token, user = InstagramTokens(pipe.db, s.instagram_app_id, s.instagram_app_secret,
                                  s.instagram_api_version).access()
    base = f"{GRAPH}/{s.instagram_api_version}/{user}"
    r = httpx.post(f"{base}/media", timeout=60, data={"media_type": "STORIES", "image_url": image_url,
                                                      "access_token": token})
    r.raise_for_status()
    cid = r.json()["id"]
    for _ in range(10):
        st = httpx.get(f"{GRAPH}/{s.instagram_api_version}/{cid}", timeout=30,
                       params={"fields": "status_code", "access_token": token}).json().get("status_code")
        if st in ("FINISHED", None):
            break
        if st == "ERROR":
            raise RuntimeError("instagram story container failed")
        time.sleep(3)
    r = httpx.post(f"{base}/media_publish", timeout=60, data={"creation_id": cid, "access_token": token})
    r.raise_for_status()
    return r.json()["id"]


def _facebook(pipe, image_url: str) -> str:
    page = pipe.db.get("facebook_page")
    if not page:
        raise RuntimeError("facebook page not connected")
    g = f"https://graph.facebook.com/v23.0/{page['page_id']}"
    r = httpx.post(f"{g}/photos", timeout=60, data={"url": image_url, "published": "false",
                                                    "access_token": page["page_token"]})
    r.raise_for_status()
    r = httpx.post(f"{g}/photo_stories", timeout=60, data={"photo_id": r.json()["id"],
                                                           "access_token": page["page_token"]})
    r.raise_for_status()
    return str(r.json().get("post_id") or r.json().get("id"))


def maybe_countdown(pipe, tz, now: float | None = None) -> bool:
    """Post the countdown story once per series part, ~2 h before its slot. True when one went up."""
    now = now or time.time()
    nxt_slot = pipe.db.q("SELECT * FROM post_slots WHERE status='PENDING' AND due_ts>? ORDER BY due_ts LIMIT 1",
                         (now,))
    if not nxt_slot or nxt_slot[0]["due_ts"] - now > LEAD_H * 3600:
        return False
    slot = nxt_slot[0]
    jobs = pipe.db.postable_jobs()
    if not jobs or not jobs[0].get("series_id") or (jobs[0].get("part") or 1) < 2:
        return False
    nxt = jobs[0]
    key = f"countdown:{nxt['id']}"
    if pipe.db.get(key):
        return False
    pipe.db.put(key, {"slot": slot["id"], "ts": now})  # once, even if posting fails below
    prev = pipe.db.q("SELECT * FROM jobs WHERE series_id=? AND part=?", (nxt["series_id"], nxt["part"] - 1))[0]
    at = post_time(slot["due_ts"], tz)
    part = PART_WORD.get(nxt["part"], str(nxt["part"]))
    lines = [f"الجزء {part} {when_text(at)}", "مين مستني؟"]
    work = pipe.s.data_dir / "stories" / str(nxt["id"])
    work.mkdir(parents=True, exist_ok=True)
    pic = _picture(pipe, dict(prev), nxt, work)
    if pic is None:
        log.warning("story: no picture for job %s", nxt["id"])
        return False
    img = render(pic, lines, work / "story.jpg")
    if not pipe.s.upload_enabled or not r2.enabled(pipe.s):
        log.info("story made (posting off): %s", img)
        return False
    url = r2.put_public(pipe.s, img, f"stories/{nxt['id']:05d}_{math.floor(now)}.jpg", "image/jpeg")
    done = []
    for name, fn in (("Instagram", _instagram), ("Facebook", _facebook)):
        try:
            fn(pipe, url)
            done.append(name)
        except Exception as e:  # noqa: BLE001 - one platform failing never blocks the other
            log.warning("story on %s failed: %s", name, e)
    pipe.tg.send_photo(img, f"⏰ ستوري العد التنازلي نزلت على {' و '.join(done) or 'ولا حتة (فشلت)'}:\n"
                            f"{lines[0]} - {lines[1]}\n«{nxt.get('series_title') or nxt['title']}»"
                            "\nلو عايز: نزّلها ستوري على TikTok كمان")
    return bool(done)


# --- teaser story with every new reel (John, 2026-10-28) ------------------------------------------

TEASER_S = 9.0


def teaser_clip(video: Path, audio_json: Path, out: Path) -> Path:
    """9 s from the strongest moment (the scene before the payoff: the twist / the fight), with
    "الحلقة الجديدة نزلت" on top and "شوفها على البروفايل" at the bottom, sound kept."""
    durs = [a["duration"] for a in json.loads(audio_json.read_text(encoding="utf-8"))]
    k = max(0, len(durs) - 2)
    total = ffprobe_duration(video)
    start = min(sum(durs[:k]), max(0.0, total - TEASER_S))
    ass = out.with_suffix(".ass")
    ass.write_text(
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\nWrapStyle: 0\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Top,{CAPTION_FONT},96,&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,1,0,0,0,100,100,0,0,3,18,0,8,60,60,170,1\n"
        f"Style: Bottom,{CAPTION_FONT},78,&H0000D7FF,&H0000D7FF,&H00000000,&H96000000,1,0,0,0,100,100,0,0,3,14,0,8,60,60,330,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        f"Dialogue: 0,0:00:00.00,0:00:{TEASER_S:05.2f},Top,,0,0,0,,الحلقة الجديدة نزلت\n"
        f"Dialogue: 0,0:00:00.00,0:00:{TEASER_S:05.2f},Bottom,,0,0,0,,شوفها على البروفايل\n", encoding="utf-8")
    run(["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.2f}", "-t", str(TEASER_S), "-i", str(video),
         "-vf", f"ass=filename='{ff_path(ass)}':fontsdir='{ff_path(FONTS_DIR)}'",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-c:a", "aac", "-b:a", "128k",
         "-movflags", "+faststart", str(out)])
    return out


def _instagram_video(pipe, video_url: str) -> str:
    from ..upload.instagram_api import GRAPH, InstagramTokens
    s = pipe.s
    token, user = InstagramTokens(pipe.db, s.instagram_app_id, s.instagram_app_secret,
                                  s.instagram_api_version).access()
    base = f"{GRAPH}/{s.instagram_api_version}/{user}"
    r = httpx.post(f"{base}/media", timeout=60, data={"media_type": "STORIES", "video_url": video_url,
                                                      "access_token": token})
    r.raise_for_status()
    cid = r.json()["id"]
    for _ in range(40):  # videos take a little while to process
        st = httpx.get(f"{GRAPH}/{s.instagram_api_version}/{cid}", timeout=30,
                       params={"fields": "status_code", "access_token": token}).json().get("status_code")
        if st == "FINISHED":
            break
        if st == "ERROR":
            raise RuntimeError("instagram story video failed")
        time.sleep(5)
    r = httpx.post(f"{base}/media_publish", timeout=60, data={"creation_id": cid, "access_token": token})
    r.raise_for_status()
    return r.json()["id"]


def _facebook_video(pipe, video_url: str) -> str:
    page = pipe.db.get("facebook_page")
    if not page:
        raise RuntimeError("facebook page not connected")
    g = f"https://graph.facebook.com/v23.0/{page['page_id']}/video_stories"
    tok = page["page_token"]
    r = httpx.post(g, timeout=60, data={"upload_phase": "start", "access_token": tok})
    r.raise_for_status()
    vid, up = r.json()["video_id"], r.json()["upload_url"]
    r = httpx.post(up, timeout=300, headers={"Authorization": f"OAuth {tok}", "file_url": video_url})
    r.raise_for_status()
    r = httpx.post(g, timeout=60, data={"upload_phase": "finish", "video_id": vid, "access_token": tok})
    r.raise_for_status()
    return str(r.json().get("post_id") or vid)


def teaser(pipe, job: dict) -> bool:
    """Right after a reel goes out: the same moment as a story on Instagram and the Facebook Page."""
    if not pipe.s.upload_enabled or not r2.enabled(pipe.s):
        return False
    video = Path(job.get("video_path") or "")
    aj = pipe.job_dir(job["id"]) / "audio" / "audio.json"
    if not video.exists() or not aj.exists():
        return False
    work = pipe.s.data_dir / "stories" / str(job["id"])
    work.mkdir(parents=True, exist_ok=True)
    clip = teaser_clip(video, aj, work / "teaser.mp4")
    url = r2.put_public(pipe.s, clip, f"stories/{job['id']:05d}_teaser.mp4")
    done = []
    for name, fn in (("Instagram", _instagram_video), ("Facebook", _facebook_video)):
        try:
            fn(pipe, url)
            done.append(name)
        except Exception as e:  # noqa: BLE001
            log.warning("teaser story on %s failed: %s", name, e)
    pipe.tg.send_video(clip, f"📲 ستوري «الحلقة الجديدة نزلت» على {' و '.join(done) or 'ولا حتة (فشلت)'}"
                             "\nلو عايز: نزّلها ستوري على TikTok كمان")
    return bool(done)


# --- vote card with series part 1 (John, 2026-10-28) ----------------------------------------------

def vote_card(picture: Path, question: str, options: list[str], out: Path) -> Path:
    """Story picture: the cover with the question and the two numbered choices, sending people to
    the reel's comments (the APIs offer no poll sticker)."""
    ass = out.with_suffix(".ass")
    head = ("[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\nWrapStyle: 0\n\n"
            "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
            "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, "
            "Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
            f"Style: Q,{CAPTION_FONT},92,&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,1,0,0,0,100,100,0,0,1,6,3,2,60,60,560,1\n"
            f"Style: A,{CAPTION_FONT},84,&H00FFFFFF,&H00FFFFFF,&H00B5641E,&H96000000,1,0,0,0,100,100,0,0,3,16,0,2,60,60,400,1\n"
            f"Style: B,{CAPTION_FONT},84,&H00FFFFFF,&H00FFFFFF,&H001E7DE6,&H96000000,1,0,0,0,100,100,0,0,3,16,0,2,60,60,260,1\n"
            f"Style: C,{CAPTION_FONT},66,&H0000D7FF,&H0000D7FF,&H00000000,&H96000000,1,0,0,0,100,100,0,0,1,5,2,2,60,60,120,1\n\n"
            "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
    ev = [("Q", question), ("A", f"اكتب ١ لو {options[0]}"), ("B", f"اكتب ٢ لو {options[1]}"),
          ("C", "في كومنتات الريل")]
    ass.write_text(head + "".join(f"Dialogue: 0,0:00:00.00,0:00:05.00,{st},,0,0,0,,{tx}\n" for st, tx in ev),
                   encoding="utf-8")
    vf = ("scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
          f"ass=filename='{ff_path(ass)}':fontsdir='{ff_path(FONTS_DIR)}'")
    run(["ffmpeg", "-y", "-v", "error", "-i", str(picture), "-vf", vf, "-frames:v", "1", "-q:v", "2", str(out)])
    return out


def vote_story(pipe, job: dict, story) -> bool:
    """With a series part that asks the viewers to vote: the vote card as a story too."""
    if not story.vote or not pipe.s.upload_enabled or not r2.enabled(pipe.s):
        return False
    jd = pipe.job_dir(job["id"])
    pic = _clean_picture(jd, last=True)  # the cliffhanger scene, no writing on it
    if pic is None:
        return False
    work = pipe.s.data_dir / "stories" / str(job["id"])
    work.mkdir(parents=True, exist_ok=True)
    img = vote_card(pic, story.vote["question_ar"], story.vote["options"], work / "vote.jpg")
    url = r2.put_public(pipe.s, img, f"stories/{job['id']:05d}_vote.jpg", "image/jpeg")
    done = []
    for name, fn in (("Instagram", _instagram), ("Facebook", _facebook)):
        try:
            fn(pipe, url)
            done.append(name)
        except Exception as e:  # noqa: BLE001
            log.warning("vote story on %s failed: %s", name, e)
    pipe.tg.send_photo(img, f"🗳️ ستوري التصويت نزلت على {' و '.join(done) or 'ولا حتة (فشلت)'}"
                            "\nلو عايز: نزّلها ستوري على TikTok كمان")
    return bool(done)
