"""Unit tests: story JSON validation, RTL subtitles, QA logic, state machine resume, scheduling."""
from __future__ import annotations

import copy
import datetime as dt
import json
import random
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from src.db import DB
from src.ideas.generator import IdeaGenerator, pick_format
from src.ideas.offline_bank import SKITS
from src.ideas.schema import Story
from src.llm import LLMUnavailable, extract_json
from src.compose import subtitles
from src.qa.audio_check import normalize_ar, similarity
from src.upload.tiktok_api import MB, chunk_plan


# --- story JSON -------------------------------------------------------------------
def test_offline_bank_all_valid():
    for s in SKITS:
        Story.model_validate(s)


def test_story_rejects_unknown_character():
    bad = copy.deepcopy(SKITS[0])
    bad["scenes"][0]["character_id"] = "banana"
    with pytest.raises(ValueError):
        Story.model_validate(bad)


def test_story_rejects_too_short_and_non_arabic():
    short = copy.deepcopy(SKITS[0])
    short["scenes"] = short["scenes"][:3]
    for sc in short["scenes"]:
        sc["line_ar"] = "أهلا"
    with pytest.raises(ValueError):
        Story.model_validate(short)
    eng = copy.deepcopy(SKITS[0])
    eng["scenes"][0]["line_ar"] = "hello there"
    with pytest.raises(ValueError):
        Story.model_validate(eng)


def test_story_normalises_ids_tags_emotion():
    s = copy.deepcopy(SKITS[0])
    s["characters"][0]["id"] = "Tomato"
    for sc in s["scenes"]:
        if sc["character_id"] == "tomato":
            sc["character_id"] = "TOMATO"
    s["scenes"][0]["emotion"] = "furious"
    s["hashtags"] = ["#fyp", "fyp", "مطبخ مصري"]
    st = Story.model_validate(s)
    assert st.characters[0].id == "tomato"
    assert st.scenes[0].emotion == "neutral"
    assert st.hashtags == ["fyp", "مطبخ_مصري"]


def test_extract_json_fences_and_prose():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('Sure! here: {"a": [1, 2]} hope it helps') == {"a": [1, 2]}


class FakeLLM:
    def __init__(self, replies):
        self.replies = list(replies)

    def text(self, prompt, json_mode=True, temperature=1.0):
        if prompt.startswith(("You are the head writer", "You are a tough TikTok editor", "You are a strict proofreader")):
            raise LLMUnavailable("no brainstorm in tests")
        if not self.replies:
            raise LLMUnavailable("none left")
        return self.replies.pop(0)


def test_generator_retries_then_validates(tmp_path):
    db = DB(tmp_path / "t.db")
    good = json.dumps(SKITS[2], ensure_ascii=False)
    gen = IdeaGenerator(FakeLLM(["not json", '{"title": "x"}', good]), db)
    assert gen.generate().title == SKITS[2]["title"]


def test_generator_falls_back_offline(tmp_path):
    db = DB(tmp_path / "t.db")
    st = IdeaGenerator(FakeLLM([]), db).generate()
    assert st.title in {s["title"] for s in SKITS}


def test_library_keeps_recurring_character_look(tmp_path):
    db = DB(tmp_path / "t.db")
    db.upsert_character("tomato", name_ar="طمطم", voice="ar-EG-ShakirNeural", look="a specific stored look xyz")
    st = IdeaGenerator(FakeLLM([json.dumps(SKITS[0], ensure_ascii=False)]), db).generate()
    assert st.char("tomato").look == "a specific stored look xyz"
    assert st.char("tomato").name == "طمطم"


def test_format_rotation():
    assert pick_format(["drama", "comedy", "advice"]) == "rivalry"
    assert pick_format(["drama", "comedy", "advice", "rivalry"]) == "rivalry"


# --- subtitles ----------------------------------------------------------------------
def test_clean_strips_punctuation_and_tags():
    assert subtitles.clean("بصي، يا {\\b1}بصلة!؟") == "بصي يا b1بصلة"


def test_chunks_short_and_no_overlap():
    words = [{"text": w, "start": i * 0.4, "end": i * 0.4 + 0.35}
             for i, w in enumerate("من غيري مفيش ولا طبخة في مصر كلها تنفع يا حبيبتي".split())]
    scenes = [{"offset": 0, "duration": 5.0, "line": "", "words": words},
              {"offset": 5.0, "duration": 3.0, "line": "أهلا بيكم يا جماعة في المطبخ", "words": []}]
    ev = subtitles.build_events(scenes)
    for e in ev:
        assert len(e["text"].split()) <= subtitles.MAX_WORDS
    for a, b in zip(ev, ev[1:]):
        assert a["end"] <= b["start"] + 1e-9, "captions must never stack"
    assert all(e["start"] >= 5.0 for e in ev if "المطبخ" in e["text"])


def test_ass_no_mid_line_override_tags(tmp_path):
    words = [{"text": "يا", "start": 0.1, "end": 0.3}, {"text": "جماعة", "start": 0.3, "end": 0.8}]
    out = subtitles.write_ass([{"offset": 0, "duration": 2, "line": "", "words": words}],
                              tmp_path / "s.ass", hook="عنوان!")
    for line in out.read_text(encoding="utf-8-sig").splitlines():
        if line.startswith("Dialogue:"):
            text = line.split(",", 9)[9]
            body = text.split("}", 1)[1] if text.startswith("{") else text
            assert "{" not in body and "\\" not in body  # tags only at the very start


# --- QA logic ---------------------------------------------------------------------
def test_arabic_normalisation_and_similarity():
    assert normalize_ar("إزيك يا أحمد، عاملة إيه؟") == normalize_ar("ازيك يا احمد عامله ايه")
    script = "يا جماعة أنا البطاطس وعندي اعتراف مهم"
    assert similarity(script, "يا جماعه انا البطاطس وعندى اعتراف مهم") > 0.9
    assert similarity(script, "الطماطم والبصل في الحلة") < 0.5
    assert similarity(script, "") == 0.0


def test_tech_check_on_generated_clip(tmp_path):
    import subprocess
    from src.qa.tech_check import check
    v = tmp_path / "v.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=1080x1920:r=30:d=16",
                    "-f", "lavfi", "-i", "sine=f=440:d=16", "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "aac", "-shortest", str(v)], check=True)
    assert check(v)["passed"]
    s = tmp_path / "silent.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=720x1280:r=30:d=5",
                    "-c:v", "libx264", "-preset", "ultrafast", str(s)], check=True)
    r = check(s)
    assert not r["passed"] and any("duration" in p for p in r["problems"]) and "no audio stream" in r["problems"]


# --- state machine ----------------------------------------------------------------
class StubPipeline:
    """Pipeline whose stages just record calls; stage_clips crashes once to simulate a kill."""

    def __new__(cls, settings, db):
        from src.pipeline import Pipeline

        class P(Pipeline):
            def __init__(self):
                self.s, self.db, self.calls, self.crash = settings, db, [], True
                self.tg = type("T", (), {"send": lambda *a, **k: None})()

            def _stage(self, name, nxt):
                def f(job_id):
                    self.calls.append(name)
                    self.db.set_status(job_id, nxt)
                return f

            def stage_idea(self, j): self._stage("idea", "IDEA")(j)
            def stage_images(self, j, force_new=False): self._stage("images", "IMAGES")(j)
            def stage_audio(self, j): self._stage("audio", "AUDIO")(j)

            def stage_clips(self, j):
                if self.crash:
                    self.crash = False
                    raise KeyboardInterrupt  # "container killed"
                self._stage("clips", "CLIPS")(j)

            def stage_compose(self, j): self._stage("compose", "COMPOSED")(j)
            def stage_qa(self, j): self._stage("qa", "QA_PASSED")(j)

        return P()


def test_resume_after_crash(tmp_path):
    from src.config import Settings
    settings = Settings(data_dir=tmp_path, max_retries=2)
    db = DB(tmp_path / "t.db")
    p = StubPipeline(settings, db)
    job = db.new_job("fallback")
    with pytest.raises(KeyboardInterrupt):
        p.run_job(job)
    assert db.job(job)["status"] == "AUDIO"  # last completed stage survives the crash
    assert p.run_job(job) == "QA_PASSED"
    assert p.calls == ["idea", "images", "audio", "clips", "compose", "qa"]  # nothing redone


def test_stage_errors_end_in_failed(tmp_path, monkeypatch):
    from src.config import Settings
    import src.pipeline as pl
    monkeypatch.setattr(pl.time, "sleep", lambda s: None)
    settings = Settings(data_dir=tmp_path, max_retries=2)
    db = DB(tmp_path / "t.db")
    p = StubPipeline(settings, db)

    def boom(j):
        raise RuntimeError("down")
    p.stage_audio = boom
    job = db.new_job("fallback")
    assert p.run_job(job) == "FAILED"
    assert db.job(job)["failed_stage"] == "AUDIO"


# --- scheduling / upload helpers --------------------------------------------------
def test_plan_slots_inside_windows():
    from src.main import plan_slots
    tz = ZoneInfo("Africa/Cairo")
    day = dt.date(2026, 11, 1)
    for n in (1, 2, 3, 5):
        slots = plan_slots(day, n, [(13 * 60, 15 * 60), (20 * 60, 23 * 60)], tz, random.Random(n))
        assert len(slots) == n
        for ts in slots:
            t = dt.datetime.fromtimestamp(ts, tz)
            minute = t.hour * 60 + t.minute
            assert 13 * 60 <= minute < 15 * 60 or 20 * 60 <= minute < 23 * 60


def test_chunk_plan_matches_media_transfer_guide():
    assert chunk_plan(3 * MB) == (3 * MB, 1)
    size = 50_000_123
    cs, n = chunk_plan(size, 10_000_000)
    assert (cs, n) == (10_000_000, 5)  # example from TikTok's docs
    cs, n = chunk_plan(25 * MB + 7)
    assert n == 2


# --- image sources ----------------------------------------------------------------
def test_image_sources_fall_through_to_procedural(tmp_path, monkeypatch):
    from src.config import Settings
    from src.ideas.schema import Story
    from src.images import pollinations as pl
    settings = Settings(data_dir=tmp_path, cloudflare_account_id="a", cloudflare_api_token="t")
    db = DB(tmp_path / "t.db")
    gen = pl.ImageGen(settings, db)
    tried = []

    def cf_down(*a, **k):
        tried.append("cloudflare")
        raise RuntimeError("down")

    def horde_down(*a, **k):
        tried.append("horde")
        raise RuntimeError("down")
    monkeypatch.setattr(pl, "cloudflare_image", cf_down)
    monkeypatch.setattr(pl, "horde_image", horde_down)
    story = Story.model_validate(SKITS[0])
    res = gen.copy_into(tmp_path / "job", story)
    assert set(res) == {c.id for c in story.characters}
    assert all(v["meta"]["source"] == "procedural" for v in res.values())
    n = len(story.characters)  # every API for every character first, Horde last (no Kaggle configured)
    assert tried == ["cloudflare"] * n + ["horde"] * n


# --- voice ------------------------------------------------------------------------
def test_story_rejects_fusha():
    bad = copy.deepcopy(SKITS[0])
    bad["scenes"][0]["line_ar"] = "ماذا تريد مني الآن يا صديقي " + bad["scenes"][0]["line_ar"]
    with pytest.raises(ValueError, match="فصحى"):
        Story.model_validate(bad)


def test_gemini_word_timings_cover_speech():
    import numpy as np
    from src.voice import gemini_tts
    rate = 24000
    pcm = np.zeros(rate * 3, dtype=np.int16)
    pcm[int(0.5 * rate):int(2.5 * rate)] = (np.sin(np.arange(2 * rate) / 5) * 8000).astype(np.int16)
    t0, t1 = gemini_tts.speech_span(pcm, rate)
    assert abs(t0 - 0.5) < 0.05 and abs(t1 - 2.5) < 0.05
    words = gemini_tts.spread_words("يا نهار أبيض على اللي جرالي", t0, t1)
    assert len(words) == 6 and words[0]["start"] == t0 and abs(words[-1]["end"] - t1) < 1e-6
    assert all(a["end"] <= b["start"] + 1e-9 for a, b in zip(words, words[1:]))


def test_gemini_tts_failure_falls_back_to_edge(tmp_path, monkeypatch):
    from src.config import Settings
    from src.voice import tts
    calls = []

    def gem_down(*a, **k):
        raise RuntimeError("quota")

    def fake_edge(text, voice, rate, pitch, wav, attempts=4):
        calls.append(voice)
        return {"wav": str(wav), "duration": 1.0, "words": []}
    monkeypatch.setattr(tts.gemini_tts, "synth", gem_down)
    monkeypatch.setattr(tts, "synth_line", fake_edge)
    story = Story.model_validate(SKITS[0])
    settings = Settings(data_dir=tmp_path, gemini_api_key="k")
    from src.video.base import QuotaWait
    with pytest.raises(QuotaWait):  # all Gemini voice models out of quota: wait, no robot voice
        tts.synthesize_story(story, tmp_path, settings=settings)

    def gem_broken(*a, **k):
        raise KeyError("unexpected reply")
    monkeypatch.setattr(tts, "_gemini_story", gem_broken)
    out = tts.synthesize_story(story, tmp_path, settings=settings)
    assert len(out) == len(story.scenes) == len(calls)  # other errors: every line on edge-tts


def test_split_one_recording_at_pauses():
    import numpy as np
    from src.voice import gemini_tts
    rate = 24000

    def tone(s):
        return (np.sin(np.arange(int(s * rate)) / 4) * 9000).astype(np.int16)

    def gap(s):
        return np.zeros(int(s * rate), dtype=np.int16)
    texts = ["يا نهار أبيض على اللي جرالي", "طب وأنا مالي", "بص بقى يا عم أنا هقولك الحكاية كلها من الأول"]
    # short in-line breath (0.12 s) must not be mistaken for the line break (0.6 s)
    pcm = np.concatenate([gap(0.2), tone(1.0), gap(0.12), tone(0.8), gap(0.6), tone(0.9), gap(0.7),
                          tone(1.5), gap(0.1), tone(1.4), gap(0.3)])
    segs = gemini_tts.split_at_pauses(pcm, rate, texts)
    assert [round(len(s) / rate, 1) for s in segs] == [pytest.approx(2.4, abs=0.3), pytest.approx(1.6, abs=0.3),
                                                      pytest.approx(3.7, abs=0.3)]
    with pytest.raises(ValueError):  # 4 lines but only 3 real pauses
        gemini_tts.split_at_pauses(pcm[: int(2 * rate)], rate, texts + ["x"])


# --- cast + series ----------------------------------------------------------------
def test_cast_voices_unique_and_guests_stable():
    from src.ideas import cast
    from src.voice.tts import gemini_voices
    story = Story.model_validate(SKITS[0])
    v = gemini_voices(story)
    assert len(set(v.values())) == len(v)
    assert cast.gemini_voice("lemon", cast.FEMALE, set()) == "Leda"
    assert cast.gemini_voice("tomato", cast.MALE, set()) == "Puck"
    g1 = cast.gemini_voice("kiwi", cast.MALE, set())
    assert g1 == cast.gemini_voice("kiwi", cast.MALE, set()) and g1 in cast.GUEST_VOICES[cast.MALE]


def test_series_parts_post_in_order_and_failure_stops_series(tmp_path):
    from src.config import Settings
    from src.pipeline import Pipeline
    db = DB(tmp_path / "t.db")
    ids = [db.new_job("fallback", status="QA_PASSED", series_id="s1", series_title="سر التلاجة",
                      part=k, parts=3) for k in (1, 2, 3)]
    solo = db.new_job("fallback", status="QA_PASSED")
    assert [j["id"] for j in db.postable_jobs()] == [ids[0], solo]  # parts 2-3 wait for part 1
    db.set_status(ids[0], "UPLOADED")
    assert [j["id"] for j in db.postable_jobs()][0] == ids[1]  # a running series goes first
    p = Pipeline(Settings(data_dir=tmp_path), db)
    p.tg = type("T", (), {"send": lambda *a, **k: None})()
    db.set_status(ids[1], "COMPOSED")
    p._fail(ids[1], "QA", "boom")
    assert db.job(ids[2])["status"] == "FAILED" and db.job(ids[2])["failed_stage"] == "SERIES"
    story = Story.model_validate(SKITS[0])
    cap = Pipeline.caption(db.job(ids[0]), story)
    assert cap.startswith("سر التلاجة | الجزء 1 من 3") and "#سر_التلاجة" in cap and "الجزء الجاي" in cap


def test_plan_next_makes_a_series_then_standalones(tmp_path):
    from src.config import Settings
    from src.pipeline import Pipeline
    db = DB(tmp_path / "t.db")
    p = Pipeline(Settings(data_dir=tmp_path, standalone_between_series=1), db)
    parts = [copy.deepcopy(SKITS[i]) for i in (0, 1, 2)]
    p.ideas = IdeaGenerator(FakeLLM([json.dumps({"series_title": "مسلسل", "parts": parts}, ensure_ascii=False)]), db)
    first = p.plan_next()
    rows = db.q("SELECT id, status, part FROM jobs ORDER BY id")
    assert [r["part"] for r in rows] == [1, 2, 3] and all(r["status"] == "IDEA" for r in rows)
    assert first == rows[0]["id"] and (p.job_dir(first) / "story.json").exists()
    solo = p.plan_next()  # next one is a standalone episode
    assert db.job(solo)["series_id"] is None and db.job(solo)["status"] == "NEW"


def test_banned_words_reject_story_but_keep_harmless_words():
    from src.ideas.schema import banned_words
    assert banned_words("تابعوا الجزء التاني عشان تشوفوا القاتل") == ["القاتل"]
    assert banned_words("هقتلك يا بطاطس") and banned_words("هموت من الضحك") and banned_words("the killer")
    assert banned_words("دمك خفيف أوي، دماغي وجعتني، هدومي اتبلت يا مدام") == []
    bad = copy.deepcopy(SKITS[0])
    bad["caption_ar"] = "مين المجرم؟ 🧀"
    with pytest.raises(ValueError, match="TikTok"):
        Story.model_validate(bad)


# --- multi-platform posting -------------------------------------------------------
class FakeUp:
    def __init__(self, name, fails=0, retryable=True):
        self.name, self.fails, self.retryable, self.calls = name, fails, retryable, 0

    def upload(self, video, caption, **kw):
        from src.upload.base import UploadError, UploadResult
        self.calls += 1
        if self.calls <= self.fails:
            raise UploadError(f"{self.name} down", retryable=self.retryable)
        return UploadResult(publish_id=f"{self.name}-1", status="OK", url=f"https://{self.name}/1")


@pytest.fixture(autouse=True)
def _no_trend_feed(monkeypatch):
    """No network in tests: no trend of the day."""
    import src.ideas.trends as tr
    monkeypatch.setattr(tr, "todays_trend", lambda llm, db, today=None: None)


@pytest.fixture(autouse=True)
def _no_ffmpeg_cover_intro(monkeypatch):
    """Upload tests use fake mp4 bytes: skip the real ffmpeg cover-intro render."""
    import shutil
    import src.pipeline as pl
    monkeypatch.setattr(pl, "cover_intro", lambda video, cover, out: shutil.copyfile(video, out) or out)


def _ready_job(tmp_path, db, p):
    jid = db.new_job("fallback", status="QA_PASSED", title="t")
    p._write(p.job_dir(jid) / "story.json", Story.model_validate(SKITS[0]).model_dump())
    v = tmp_path / "v.mp4"
    v.write_bytes(b"x")
    (tmp_path / "cover.jpg").write_bytes(b"jpg")  # every post needs its designed cover
    db.update_job(jid, video_path=str(v))
    return jid


def test_post_to_every_platform_and_retry_the_failed_one(tmp_path, monkeypatch):
    from src.config import Settings
    from src.pipeline import Pipeline
    db = DB(tmp_path / "t.db")
    p = Pipeline(Settings(data_dir=tmp_path), db)
    sent = []
    p.tg = type("T", (), {"send": lambda self, m, **k: sent.append(m)})()
    ups = {"tiktok": FakeUp("tiktok"), "youtube": FakeUp("youtube", fails=1), "instagram": FakeUp("instagram")}
    monkeypatch.setattr(p, "uploaders", lambda: ups)
    jid = _ready_job(tmp_path, db, p)
    assert p.upload_next() == jid
    assert db.job(jid)["status"] == "UPLOADED"
    assert db.post(jid, "youtube")["status"] == "RETRY" and db.post(jid, "tiktok")["status"] == "DONE"
    assert any("🔁 youtube" in m for m in sent)
    p.upload_next()  # next slot: the retry pass fixes youtube (no new video ready)
    assert db.post(jid, "youtube")["status"] == "DONE" and ups["tiktok"].calls == 1


def test_nothing_posted_keeps_the_job(tmp_path, monkeypatch):
    from src.config import Settings
    from src.pipeline import Pipeline
    db = DB(tmp_path / "t.db")
    p = Pipeline(Settings(data_dir=tmp_path), db)
    p.tg = type("T", (), {"send": lambda *a, **k: None})()
    monkeypatch.setattr(p, "uploaders", lambda: {"tiktok": FakeUp("tiktok", fails=9, retryable=False)})
    jid = _ready_job(tmp_path, db, p)
    assert p.upload_next() is None
    assert db.job(jid)["status"] == "QA_PASSED" and db.post(jid, "tiktok")["status"] == "FAILED"


def test_youtube_metadata_marks_shorts_and_ai():
    from src.upload.youtube_api import YouTubeUploader
    m = YouTubeUploader.metadata("لغز الجبنة <ج1>", "desc", ["a" * 40, "b"], "private", False, "24")
    assert m["snippet"]["title"].endswith("#Shorts") and "<" not in m["snippet"]["title"]
    assert m["status"]["containsSyntheticMedia"] is True and len(m["snippet"]["tags"][0]) == 30


def test_channel_hashtag_on_every_caption():
    from src.pipeline import Pipeline
    story = Story.model_validate(SKITS[0])
    assert "#نهارك_سلطة" in Pipeline.caption({}, story, "نهارك سلطة")
    job = {"series_id": "s", "series_title": "لغز الجبنة", "part": 3, "parts": 3}
    cap = Pipeline.caption(job, story, "نهارك سلطة")
    assert "#لغز_الجبنة" in cap and "#نهارك_سلطة" in cap and "الجزء الجاي" not in cap  # last part: no "follow"


def test_hashtags_stay_under_each_platform_limit_and_are_linkable():
    import re
    from src.pipeline import Pipeline
    story = Story.model_validate({**SKITS[0], "hashtags": ["fyp", "كوميديا مصرية", "مطبخ-ماما", "ضحك", "مصر",
                                                            "اكسبلور", "الجبنة", "خضار", "😂"]})
    job = {"series_id": "s", "series_title": "لغز الجبنة", "part": 1, "parts": 3}
    for platform, limit in Pipeline.TAG_LIMIT.items():
        tags = re.findall(r"#\S+", Pipeline.caption(job, story, "نهارك سلطة", platform))
        assert len(tags) <= limit, platform
        assert tags[0] == "#لغز_الجبنة" and "#نهارك_سلطة" in tags and "#fyp" not in tags
        assert all(re.fullmatch(r"#[0-9A-Za-z_ء-ي]+", t) for t in tags), tags


def test_quota_wait_pauses_job_instead_of_failing(tmp_path):
    from src.config import Settings
    from src.video.base import QuotaWait
    settings = Settings(data_dir=tmp_path, max_retries=2)
    db = DB(tmp_path / "t.db")
    p = StubPipeline(settings, db)

    def out_of_quota(j):
        raise QuotaWait("pictures used up")
    p.stage_images = out_of_quota
    job = db.new_job("fallback")
    assert p.run_job(job) == "WAITING"
    assert db.job(job)["status"] == "IDEA" and db.get("produce_backoff_until", 0) > 0


def test_no_cover_no_post(tmp_path, monkeypatch):
    from src.config import Settings
    from src.pipeline import Pipeline
    import src.pipeline as pl
    db = DB(tmp_path / "t.db")
    p = Pipeline(Settings(data_dir=tmp_path), db)
    p.tg = type("T", (), {"send": lambda *a, **k: None})()
    up = FakeUp("instagram")
    monkeypatch.setattr(p, "uploaders", lambda: {"instagram": up})
    monkeypatch.setattr(pl, "cover_for_job", lambda *a, **k: None)  # cover can't be made right now
    jid = _ready_job(tmp_path, db, p)
    (tmp_path / "cover.jpg").unlink()
    assert p.upload_next() is None and up.calls == 0
    assert db.post(jid, "instagram")["status"] == "RETRY"


def test_vote_reads_1_2_in_any_form():
    from src.ideas.vote import choice_of
    opts = ["الطماطم", "الخيارة"]
    assert choice_of("1", opts) == 0 and choice_of("٢ طبعا", opts) == 1
    assert choice_of("أكيد الخيارة 😂", opts) == 1 and choice_of("واحد", opts) == 0
    assert choice_of("1 و 2", opts) is None and choice_of("حلو اوي", opts) is None and choice_of("12", opts) is None


def test_series_parts_wait_for_the_vote_then_follow_it(tmp_path, monkeypatch):
    import time as _t
    import src.ideas.vote as vote
    from src.config import Settings
    from src.pipeline import Pipeline
    db = DB(tmp_path / "t.db")
    p = Pipeline(Settings(data_dir=tmp_path), db)
    sent = []
    p.tg = type("T", (), {"send": lambda self, m, **k: sent.append(m)})()
    base = Story.model_validate(SKITS[0])
    p1 = base.model_copy(update={"vote": {"question_ar": "مين الخاين؟", "options": ["الطماطم", "الخيارة"]}})
    ids = [db.new_job("fallback", status="UPLOADED" if k == 1 else "IDEA", title=f"p{k}",
                      story_json=(p1 if k == 1 else base).model_dump_json(), series_id="s1",
                      series_title="لغز", part=k, parts=3) for k in (1, 2, 3)]
    db.update_job(ids[0], uploaded_at=_t.time())
    assert vote.gate(p, db.job(ids[1]))  # just posted: wait for votes
    db.update_job(ids[0], uploaded_at=_t.time() - 19 * 3600)
    monkeypatch.setattr(vote, "tally", lambda pipe, job, story: [3, 9])
    new = SKITS[1]
    p.llm = type("L", (), {"text": lambda self, prompt, **k: json.dumps({"parts": [new, new]}, ensure_ascii=False)})()
    assert not vote.gate(p, db.job(ids[1]))  # voting over: counted and rewritten
    assert db.job(ids[1])["title"] == new["title"] and "الخيارة: 9" in sent[-1]
    assert not vote.gate(p, db.job(ids[2]))  # only once
    cap = Pipeline.caption(db.job(ids[0]), p1, "نهارك سلطة")
    assert "اكتب 1 لو الطماطم و 2 لو الخيارة" in cap


def test_weekly_week_runs_friday_evening_to_friday_evening():
    import datetime as _dt
    from src.compose.weekly import description, week_key
    fri = _dt.datetime(2026, 10, 30, 17, 0)  # a Friday
    assert week_key(fri) == "2026-10-30"
    assert week_key(fri.replace(hour=19)) == "2026-11-06"  # after the compile: next week
    assert week_key(_dt.datetime(2026, 11, 1, 12)) == "2026-11-06"  # Sunday
    d = description([(0, "أ"), (41.5, "ب"), (83, "ج")], "نهارك سلطة")
    assert "0:00 أ" in d and "0:41 ب" in d and "1:23 ج" in d


def test_caption_times_follow_the_heard_voice():
    from src.compose.align import match
    heard = [{"text": "يانهر", "start": 0.0, "end": 0.4}, {"text": "اسود", "start": 0.5, "end": 0.9},
             {"text": "الجبنى", "start": 2.3, "end": 2.7}]
    got = match("يا نهار أسود قالب الجبنة", heard, 3.0)
    assert [w["text"] for w in got] == ["يا", "نهار", "أسود", "قالب", "الجبنة"]
    assert abs(got[2]["start"] - 0.5) < 0.05 and abs(got[4]["start"] - 2.3) < 0.05
    assert all(a["start"] < b["start"] for a, b in zip(got, got[1:]))
    assert match("كلام تاني خالص", heard, 3.0) is None  # nothing matches -> keep the old timings


def test_proofread_fixes_small_mistakes_and_refuses_rewrites():
    from src.ideas.proofread import proofread
    st = Story.model_validate(SKITS[0])
    line0 = st.scenes[0].line_ar
    reply = {"texts": [{"id": "0.title", "text": st.title}, {"id": "0.caption", "text": st.caption_ar + "!"},
                       {"id": "0.line0", "text": "كلام مختلف خالص مالوش أي علاقة باللي فات ده خالص يعني"}]}

    class One:
        def text(self, prompt, json_mode=True, temperature=1.0):
            return json.dumps(reply, ensure_ascii=False)
    out = proofread(One(), [st])[0]
    assert out.caption_ar == st.caption_ar + "!"
    assert out.scenes[0].line_ar == line0  # too different -> refused
