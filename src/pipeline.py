"""One full run as a resumable state machine.

NEW -> IDEA -> IMAGES -> AUDIO -> CLIPS -> COMPOSED -> QA_PASSED -> UPLOADED   (or FAILED)

The status is the last COMPLETED stage and every stage writes its artifacts into
data/jobs/<id>/ before the status moves on, so a crashed run resumes at the next stage.
(Voice comes before clips: clip lengths and the mouth animation follow the audio.)
"""
from __future__ import annotations

import json
import logging
import shutil
import time
import traceback
import uuid
from pathlib import Path

from .compose.composer import compose
from .compose.thumbnail import cover_for_job, cover_intro
from .db import DB
from .ideas.generator import IdeaGenerator
from .ideas.schema import clean_tag, Story
from .images.pollinations import ImageGen
from .llm import LLM
from .notify.telegram import Telegram
from .qa.gate import run_qa
from .upload.base import OutboxUploader, UploadError, Uploader
from .upload.facebook_page import TOKEN_KEY as FB_PAGE_KEY, FacebookPageUploader
from .upload.instagram_api import TOKEN_KEY as IG_TOKEN_KEY, InstagramTokens, InstagramUploader
from .upload.telegram_handoff import TelegramHandoff
from .upload.tiktok_api import TOKEN_KEY, TikTokUploader, TokenStore
from .upload.youtube_api import TOKEN_KEY as YT_TOKEN_KEY, YouTubeTokens, YouTubeUploader
from .video.base import EngineUnavailable, QuotaWait, SceneSpec
from .video.fallback_engine import FallbackEngine
from .video.kaggle_engine import KaggleEngine
from .voice.tts import OUTRO_TEXT, synth_outro, synthesize_story

log = logging.getLogger(__name__)


class Pipeline:
    def __init__(self, settings, db: DB | None = None, notifier: Telegram | None = None):
        self.s = settings
        self.db = db or DB(settings.db_path)
        self.tg = notifier or Telegram(settings.telegram_bot_token, settings.telegram_chat_id, self.db)
        self.llm = LLM(settings, self.db)
        self.ideas = IdeaGenerator(self.llm, self.db)
        self.fallback = FallbackEngine()
        self.kaggle = KaggleEngine(settings, self.db)
        self.images = ImageGen(settings, self.db, self.llm, self.kaggle)

    # --- helpers ---------------------------------------------------------------
    def job_dir(self, job_id: int) -> Path:
        d = self.s.jobs_dir / f"{job_id:05d}"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @staticmethod
    def _read(path: Path):
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _write(path: Path, data) -> None:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)

    def story(self, job_id: int) -> Story:
        return Story.model_validate(self._read(self.job_dir(job_id) / "story.json"))

    # --- stages ----------------------------------------------------------------
    def stage_idea(self, job_id: int) -> None:
        story = self.ideas.generate()
        self._write(self.job_dir(job_id) / "story.json", story.model_dump())
        self.db.set_status(job_id, "IDEA", title=story.title, format=story.format,
                           story_json=story.model_dump_json())

    def stage_images(self, job_id: int, force_new: bool = False) -> None:
        story = self.story(job_id)
        imgs = self.images.copy_into(self.job_dir(job_id), story, force_new=force_new)
        imgs["__scenes__"] = self.images.scene_pictures(story, self.job_dir(job_id), imgs)
        self._write(self.job_dir(job_id) / "images.json", imgs)
        self.db.set_status(job_id, "IMAGES")

    def stage_audio(self, job_id: int) -> None:
        story, jd = self.story(job_id), self.job_dir(job_id)
        synthesize_story(story, jd, settings=self.s)
        job = self.db.job(job_id)
        last = story.scenes[-1].line_ar
        said = any(k in last for k in ("تابع", "الجزء التاني", "الجزء الجاي", "الحلقة الجاية"))  # never twice
        if job.get("series_id") and (job.get("part") or 0) < (job.get("parts") or 0) and (story.vote or not said):
            text = ("اكتبولنا في الكومنتات: واحد ولا اتنين؟ وتابعونا عشان تشوفوا اللي هيحصل"
                    if story.vote else OUTRO_TEXT)
            self._write(jd / "audio" / "outro.json", synth_outro(story, jd / "audio", self.s, text))
        self.db.set_status(job_id, "AUDIO")

    def _specs(self, job_id: int) -> list[SceneSpec]:
        jd, story = self.job_dir(job_id), self.story(job_id)
        imgs = self._read(jd / "images.json")
        audio = self._read(jd / "audio" / "audio.json")
        per_scene = imgs.get("__scenes__") or [imgs[sc.character_id] for sc in story.scenes]
        return [SceneSpec(index=i, character_id=sc.character_id, image=Path(per_scene[i]["path"]),
                          meta=per_scene[i]["meta"], audio=Path(a["wav"]), duration=a["duration"],
                          emotion=sc.emotion, prompt=sc.visual_prompt_en)
                for i, (sc, a) in enumerate(zip(story.scenes, audio))]

    def stage_clips(self, job_id: int) -> None:
        job = self.db.job(job_id)
        jd = self.job_dir(job_id)
        specs = self._specs(job_id)
        out = jd / "clips"
        shutil.rmtree(out, ignore_errors=True)
        seed = job_id * 10 + job["attempts"]
        engine = job["engine"] or self.s.video_engine
        clips: list[Path | None] = [None] * len(specs)
        used = "fallback"
        if engine == "kaggle":
            try:
                clips = self.kaggle.generate(specs, out, seed)
                used = "kaggle"
            except EngineUnavailable as e:
                log.warning("kaggle unavailable, using fallback: %s", e)
                self._note_kaggle(str(e))
            except Exception as e:
                log.exception("kaggle engine failed, using fallback")
                self._note_kaggle(f"error: {e}")
        for i, spec in enumerate(specs):  # fill any scene the GPU didn't deliver
            if clips[i] is None:
                clips[i] = self.fallback.render_scene(spec, out / f"scene_{spec.index:02d}.mp4", seed)
        if used == "fallback":
            self.db.update_job(job_id, engine="fallback")
        self._write(jd / "clips.json", {"engine": used, "clips": [str(c) for c in clips]})
        self.db.set_status(job_id, "CLIPS")

    def _note_kaggle(self, msg: str) -> None:
        if "credentials not configured" not in msg:
            self.tg.send(f"⚠️ Kaggle -> fallback engine: {msg[:300]}", throttle_key="kaggle_down", throttle_s=6 * 3600)

    def stage_compose(self, job_id: int) -> None:
        jd = self.job_dir(job_id)
        clips = [Path(c) for c in self._read(jd / "clips.json")["clips"]]
        audio = self._read(jd / "audio" / "audio.json")
        outro_f = jd / "audio" / "outro.json"
        outro = self._read(outro_f) if outro_f.exists() else None
        loop = not self.db.job(job_id).get("series_id")  # standalones loop; series parts end on the cliffhanger
        final = compose(self.story(job_id), audio, clips, jd, outro=outro, loop=loop, settings=self.s)
        try:  # designed cover for the platforms that take one; a failure never blocks the video
            cover_for_job(self.db.job(job_id), self.story(job_id), jd, self.s.channel_name, self.s)
        except Exception:
            log.exception("job %d: cover failed", job_id)
        self.db.set_status(job_id, "COMPOSED", video_path=str(final))

    def stage_qa(self, job_id: int) -> None:
        job = self.db.job(job_id)
        res = run_qa(Path(job["video_path"]), self.story(job_id), self.s, self.llm, job["engine"] or "fallback")
        self._write(self.job_dir(job_id) / "qa.json", res)
        self.db.update_job(job_id, qa_json=json.dumps(res, ensure_ascii=False)[:20000])
        if res["passed"]:
            self.db.set_status(job_id, "QA_PASSED")
            story = self.story(job_id)
            part = f" (الجزء {job['part']} من {job['parts']})" if job.get("series_id") else ""
            self.tg.send_video(job["video_path"], f"🎬 فيديو جديد اتعمل{part}: {story.title}\n"
                                                  f"هينزل في أقرب ميعاد نشر.\n\n{self.caption(job, story, self.s.channel_name, 'tiktok_manual')}")
            for c in story.characters:
                row = self.db.character(c.id)
                self.db.upsert_character(c.id, episodes=(row["episodes"] if row else 0) + 1)
            return
        attempts = job["attempts"] + 1
        part = res["failed_part"]
        log.warning("QA failed (%s), attempt %d: %s", part, attempts, res.get(part, {}).get("problems"))
        if attempts > self.s.max_retries:
            self._fail(job_id, "QA", f"{part}: {res.get(part, {}).get('problems')}")
            return
        # regenerate only the failing part
        if part == "tech":
            back = "CLIPS"  # recompose
        elif part == "audio":
            back = "IMAGES"  # redo the voice-over
        elif job["engine"] == "kaggle" and attempts < self.s.max_retries:
            back = "AUDIO"  # new GPU clips with a new seed
        elif job["engine"] == "kaggle":
            back = "AUDIO"
            self.db.update_job(job_id, engine="fallback")
        else:  # fallback engine already: the character images themselves are bad
            back = "IDEA"
            self.db.put(f"force_new_images:{job_id}", True)
        self.db.update_job(job_id, attempts=attempts)
        self.db.set_status(job_id, back)

    def _fail(self, job_id: int, stage: str, error: str) -> None:
        self.db.set_status(job_id, "FAILED", failed_stage=stage, error=error[:4000])
        job = self.db.job(job_id)
        title = job.get("title") or "-"
        self.tg.send(f"❌ Run #{job_id} failed at {stage}\n{title}\n{error[:900]}")
        if job.get("series_id"):  # later parts make no sense without this one
            for other in self.db.series_jobs(job["series_id"]):
                if (other["part"] or 0) > (job["part"] or 0) and other["status"] not in ("UPLOADED", "FAILED"):
                    self.db.set_status(other["id"], "FAILED", failed_stage="SERIES",
                                       error=f"part {job['part']} of the series failed")

    # --- driver -----------------------------------------------------------------
    NEXT = {"NEW": "stage_idea", "IDEA": "stage_images", "IMAGES": "stage_audio",
            "AUDIO": "stage_clips", "CLIPS": "stage_compose", "COMPOSED": "stage_qa"}

    def run_job(self, job_id: int) -> str:
        stage_errors = 0
        while True:
            job = self.db.job(job_id)
            status = job["status"]
            if status not in self.NEXT:
                return status
            fn = self.NEXT[status]
            t = time.time()
            try:
                log.info("job %d: %s", job_id, fn)
                if fn == "stage_images" and self.db.get(f"force_new_images:{job_id}"):
                    self.db.put(f"force_new_images:{job_id}", False)
                    self.stage_images(job_id, force_new=True)
                else:
                    getattr(self, fn)(job_id)
                log.info("job %d: %s done in %.0fs", job_id, fn, time.time() - t)
                stage_errors = 0
            except QuotaWait as e:
                # keep the job where it is; production pauses an hour and resumes it then
                log.warning("job %d: %s waiting for a daily allowance: %s", job_id, fn, e)
                self.db.put("produce_backoff_until", time.time() + 3600)
                return "WAITING"
            except Exception as e:
                stage_errors += 1
                log.exception("job %d: %s crashed (%d)", job_id, fn, stage_errors)
                if stage_errors > self.s.max_retries:
                    self._fail(job_id, fn.replace("stage_", "").upper(),
                               f"{e!r}\n{traceback.format_exc()[-1500:]}")
                    return "FAILED"
                time.sleep(min(300, 10 * 2 ** stage_errors))

    def plan_next(self) -> int:
        """New work: a 3-part series every (standalone_between_series + 1) videos, else one episode."""
        since = int(self.db.get("standalone_since_series", 99))
        running = any(j.get("series_id") for j in self.db.unfinished_jobs())  # parts waiting for a vote
        if self.s.series_enabled and since >= self.s.standalone_between_series and not running:
            got = self.ideas.generate_series()
            if got:
                title, stories = got
                sid = uuid.uuid4().hex[:10]
                ids = []
                for k, st in enumerate(stories, 1):
                    jid = self.db.new_job(self.s.video_engine, status="IDEA", title=st.title, format=st.format,
                                          story_json=st.model_dump_json(), series_id=sid, series_title=title,
                                          part=k, parts=len(stories))
                    self._write(self.job_dir(jid) / "story.json", st.model_dump())
                    ids.append(jid)
                self.db.put("standalone_since_series", 0)
                log.info("new series %r: jobs %s", title, ids)
                return ids[0]
        self.db.put("standalone_since_series", since + 1)
        return self.db.new_job(self.s.video_engine)

    def produce_one(self) -> int:
        """Resume the oldest unfinished job, or start new work. Returns the job id."""
        from .ideas.vote import gate
        pending = [j for j in self.db.unfinished_jobs() if not gate(self, j)]
        job_id = pending[0]["id"] if pending else self.plan_next()
        status = self.run_job(job_id)
        log.info("job %d finished as %s", job_id, status)
        return job_id

    # --- upload -----------------------------------------------------------------
    POST_MAX_ATTEMPTS = 3

    def uploaders(self) -> dict[str, Uploader]:
        """Every connected platform; the outbox when none is connected yet."""
        out: dict[str, Uploader] = {}
        want = {p.strip() for p in self.s.platforms.split(",")}
        if self.s.upload_enabled:
            if "tiktok" in want and self.s.tiktok_client_key and self.db.get(TOKEN_KEY):
                out["tiktok"] = TikTokUploader(TokenStore(self.db, self.s.tiktok_client_key,
                                                          self.s.tiktok_client_secret),
                                               privacy_level=self.s.tiktok_privacy_level)
            if "tiktok_manual" in want and self.s.telegram_bot_token and self.s.telegram_chat_id:
                out["tiktok_manual"] = TelegramHandoff(self.s.telegram_bot_token, self.s.telegram_chat_id)
            if "youtube" in want and self.s.youtube_client_id and self.db.get(YT_TOKEN_KEY):
                out["youtube"] = YouTubeUploader(YouTubeTokens(self.db, self.s.youtube_client_id,
                                                               self.s.youtube_client_secret),
                                                 privacy=self.s.youtube_privacy,
                                                 made_for_kids=self.s.youtube_made_for_kids)
            if "instagram" in want and self.s.instagram_app_id and self.db.get(IG_TOKEN_KEY):
                out["instagram"] = InstagramUploader(InstagramTokens(self.db, self.s.instagram_app_id,
                                                                     self.s.instagram_app_secret,
                                                                     self.s.instagram_api_version), self.s)
            if "facebook" in want and self.db.get(FB_PAGE_KEY):
                out["facebook"] = FacebookPageUploader(self.db, self.s)
        return out or {"outbox": OutboxUploader(self.s.data_dir / "outbox")}

    # hashtags per platform (checked 2026-10-28): TikTok and Instagram cap a post at 5 (Instagram
    # refuses more), YouTube shows the first 3 above the title and ignores them all when stuffed,
    # Facebook does best with very few. Over the limit hurts reach, so we stay under it.
    TAG_LIMIT = {"tiktok": 5, "tiktok_manual": 5, "instagram": 5, "facebook": 3, "youtube": 4, "outbox": 5}
    WEAK_TAGS = {"fyp", "foryou", "foryoupage", "viral", "explore", "اكسبلور", "ترند", "تريند", "فوريو"}

    @classmethod
    def hashtags(cls, job: dict, story, channel: str = "", platform: str = "tiktok") -> list[str]:
        """Most useful first: the series tag (people follow the parts), the story's topic tags,
        the channel tag; generic 'fyp'-style tags only if room is left."""
        series = [clean_tag(job.get("series_title") or "")] if job.get("series_id") else []
        brand = [clean_tag("_".join(channel.split()))] if channel else []
        topical = [t for t in story.hashtags if t.lower() not in cls.WEAK_TAGS]
        weak = [t for t in story.hashtags if t.lower() in cls.WEAK_TAGS]
        limit = cls.TAG_LIMIT.get(platform, 5)
        picked: list[str] = []
        for t in [*series, *topical[: max(1, limit - len(series) - len(brand))], *brand, *topical, *weak]:
            if t and t not in picked and len(picked) < limit:
                picked.append(t)
        return picked

    @classmethod
    def caption(cls, job: dict, story, channel: str = "", platform: str = "tiktok") -> str:
        tags = " ".join(f"#{t}" for t in cls.hashtags(job, story, channel, platform))
        text = story.caption_ar.strip()
        if job.get("series_id"):
            head = f"{job.get('series_title') or ''} | الجزء {job['part']} من {job['parts']}".strip(" |")
            # Gemini's caption often says "تابعوا" already: don't say it twice
            follow = ("\nتابعونا عشان الجزء الجاي 👀" if job["part"] < job["parts"] and not any(k in text for k in ("تابع", "استنو", "الجزء الجاي", "الجزء التالت", "الجزء التاني"))
                      else "")
            if getattr(story, "vote", None):
                o = story.vote["options"]
                follow += (f"\n\n🗳️ {story.vote['question_ar']}\nاكتب 1 لو {o[0]} و 2 لو {o[1]} 👇"
                           "\nاللي ياخد أصوات أكتر هو اللي هيحصل في الجزء الجاي!")
            text = f"{head}\n{text}{follow}"
        return f"{text}\n\n{tags}".strip()[:2200]

    @staticmethod
    def yt_title(job: dict, story) -> str:
        if job.get("series_id"):
            return f"{story.title} | {job.get('series_title') or ''} ج{job['part']}"
        return story.title

    def _post_one(self, job: dict, story, platform: str, up: Uploader) -> str:
        """Post a job on one platform; records the outcome. Returns DONE / RETRY / FAILED."""
        video = Path(job["video_path"])
        cover = video.parent / "cover.jpg"
        try:
            if platform != "outbox" and not cover.exists():
                # never post without the designed cover (John): make it now, else wait and retry
                cover_for_job(job, story, video.parent, self.s.channel_name, self.s)
            if platform != "outbox" and not cover.exists():
                raise UploadError("cover missing - not posting without it", retryable=True)
            if platform in ("youtube", "tiktok"):
                # these take the thumbnail from the video: use the copy that opens on the cover
                intro = video.with_name("final_cover_intro.mp4")
                if not intro.exists():
                    cover_intro(video, cover, intro)
                video = intro
            if platform == "youtube":
                res = up.upload(video, self.caption(job, story, self.s.channel_name, platform),
                                title=self.yt_title(job, story), tags=story.hashtags, cover=cover)
            elif platform in ("instagram", "facebook", "tiktok_manual"):
                res = up.upload(video, self.caption(job, story, self.s.channel_name, platform), cover=cover)
            else:
                res = up.upload(video, self.caption(job, story, self.s.channel_name, platform))
        except Exception as e:
            retry = getattr(e, "retryable", True)
            meta_lock = platform in ("instagram", "facebook") and any(
                k in str(e) for k in ("API access blocked", "Cannot call API for app"))
            if meta_lock:  # Meta paused the developer account (2026-10-28): John confirms, we post again
                retry = True
                self.tg.send("🔒 Meta وقفت التطبيق مؤقتًا، فالفيديو منزلش على Instagram و Facebook.\n"
                             "افتح developers.facebook.com من الموبايل أو الكمبيوتر واعمل Confirm Account.\n"
                             "بعدها الفيديو هينزل لوحده في التشغيلة الجاية.",
                             throttle_key="meta_lock", throttle_s=6 * 3600)
            prev = self.db.post(job["id"], platform)
            attempts = (prev["attempts"] if prev else 0) + 1
            status = "RETRY" if meta_lock or (retry and attempts < self.POST_MAX_ATTEMPTS) else "FAILED"
            log.error("%s upload of job %d failed (%s): %s", platform, job["id"], status, e)
            self.db.record_post(job["id"], platform, status, error=str(e)[:2000])
            key = f"{platform}_token" if not retry else f"{platform}_err"
            prefix = f"🔑 {platform} needs a person" if not retry else f"⚠️ {platform} upload failed"
            if not meta_lock:
                self.tg.send(f"{prefix}: {e}"[:900], throttle_key=key, throttle_s=3600)
            return status
        self.db.record_post(job["id"], platform, "DONE", publish_id=res.publish_id, url=res.url)
        return "DONE"

    def retry_posts(self) -> None:
        """Platforms that failed on an already-posted video get another go (up to 3 tries, 2 days)."""
        ups = self.uploaders()
        for row in self.db.posts_to_retry(self.POST_MAX_ATTEMPTS, time.time() - 2 * 86400):
            if row["platform"] in ups:
                job = self.db.job(row["job_id"])
                if self._post_one(job, self.story(job["id"]), row["platform"], ups[row["platform"]]) == "DONE":
                    p = self.db.post(job["id"], row["platform"])
                    self.tg.send(f"✅ {row['platform']} (retry): {job['title']}"
                                 + (f"\n{p['url']}" if p["url"] else ""))

    def upload_next(self) -> int | None:
        try:
            self.retry_posts()
        except Exception:
            log.exception("retry pass crashed")
        ready = self.db.postable_jobs()
        if not ready:
            self.tg.send("⚠️ Posting slot reached but no video is ready (buffer empty).",
                         throttle_key="buffer_empty", throttle_s=3 * 3600)
            return None
        job = ready[0]
        story = self.story(job["id"])
        results = {}
        for platform, up in self.uploaders().items():
            done = self.db.post(job["id"], platform)
            if done and done["status"] == "DONE":
                results[platform] = "DONE"
                continue
            results[platform] = self._post_one(job, story, platform, up)
        if "DONE" not in results.values():
            return None  # nothing went out: keep the job for the next slot
        first = next(self.db.post(job["id"], p) for p, r in results.items() if r == "DONE")
        self.db.set_status(job["id"], "UPLOADED", publish_id=first["publish_id"], post_url=first["url"],
                           uploaded_at=time.time())
        try:  # keep a copy for the weekly long YouTube video
            from .compose.weekly import archive
            archive(self, job, story)
        except Exception:
            log.exception("weekly archive of job %d failed", job["id"])
        try:  # "the new episode is out" story on Instagram + Facebook
            from .compose.story import teaser, vote_story
            teaser(self, self.db.job(job["id"]))
            vote_story(self, self.db.job(job["id"]), story)
        except Exception:
            log.exception("teaser story of job %d failed", job["id"])
        lines = []
        for p, r in results.items():
            row = self.db.post(job["id"], p)
            mark = {"DONE": "✅", "RETRY": "🔁", "FAILED": "❌"}[r]
            lines.append(f"{mark} {p}" + (f": {row['url']}" if r == "DONE" and row["url"] else ""))
        self.tg.send(f"📤 {story.title}\n" + "\n".join(lines))
        return job["id"]
