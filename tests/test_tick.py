"""The hourly GitHub runs, simulated: a fresh machine every hour, state only through (fake) R2,
a fake clock, fake platforms. Checks the posts go out on time, once, and survive a cut-off run."""
from __future__ import annotations

import datetime as dt
import shutil
import time
import types
from zoneinfo import ZoneInfo

import pytest

import src.main as main_mod
import src.pipeline as pl
import src.tick as tick
from src import state_sync
from src.config import Settings
from src.ideas.offline_bank import SKITS
from src.ideas.schema import Story

CAIRO = ZoneInfo("Africa/Cairo")
CRON_HOURS_UTC = range(4, 22)  # tick.yml: "17 4-21 * * *"


class Clock:
    def __init__(self, t: float):
        self.t = t

    def __call__(self) -> float:
        return self.t


class FakeUp:
    def __init__(self, name, log):
        self.name, self.log = name, log

    def upload(self, video, caption, **kw):
        from src.upload.base import UploadResult
        self.log.append((self.name, video.parent.name, time.time()))
        return UploadResult(publish_id="x", status="OK", url=None)


@pytest.fixture
def world(tmp_path, monkeypatch):
    clock = Clock(dt.datetime(2026, 11, 2, 4, 17, tzinfo=dt.timezone.utc).timestamp())
    monkeypatch.setattr(time, "time", clock)

    class FakeDT(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return dt.datetime.fromtimestamp(clock(), tz)

    shim = types.SimpleNamespace(datetime=FakeDT, date=dt.date, timedelta=dt.timedelta, time=dt.time)
    monkeypatch.setattr(main_mod, "dt", shim)
    monkeypatch.setattr(tick, "dt", shim)

    r2 = {}  # the bucket
    monkeypatch.setattr(state_sync, "_get", lambda s, name: r2.get(name))
    monkeypatch.setattr(state_sync, "_put", lambda s, name, data: r2.__setitem__(name, data))
    monkeypatch.setattr(pl, "cover_intro", lambda video, cover, out: shutil.copyfile(video, out) or out)

    posts, msgs, made = [], [], [0]
    monkeypatch.setattr(pl.Pipeline, "uploaders",
                        lambda self: {n: FakeUp(n, posts) for n in ("youtube", "instagram", "facebook", "tiktok_manual")})
    monkeypatch.setattr(main_mod.Telegram, "send", lambda self, m, **k: msgs.append(m) or True)

    def produce_one(self):  # one finished video per call
        made[0] += 1
        jid = self.db.new_job("fallback", status="QA_PASSED", title=f"v{made[0]}")
        jd = self.job_dir(jid)
        self._write(jd / "story.json", Story.model_validate(SKITS[made[0] % len(SKITS)]).model_dump())
        (jd / "final.mp4").write_bytes(b"mp4")
        (jd / "cover.jpg").write_bytes(b"jpg")
        self.db.update_job(jid, video_path=str(jd / "final.mp4"))
        return jid
    monkeypatch.setattr(pl.Pipeline, "produce_one", produce_one)

    runs = [0]

    def run_tick(argv=()):
        """One workflow run on a brand-new machine (empty data folder)."""
        runs[0] += 1
        s = Settings(data_dir=tmp_path / f"runner{runs[0]}", state_key="k" * 20, runner="github",
                     r2_account_id="a", r2_access_key="b", r2_secret_key="c", r2_bucket="d",
                     r2_public_base="https://x", telegram_bot_token="", posts_per_day=2,
                     post_windows="15:00-17:00,20:00-22:30", timezone="Africa/Cairo")
        for d in (s.data_dir, s.jobs_dir, s.cache_dir):
            d.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(tick, "get_settings", lambda: s)
        monkeypatch.setattr(main_mod, "get_settings", lambda: s)
        monkeypatch.setattr("sys.argv", ["tick", *argv])
        return tick.main()

    return types.SimpleNamespace(clock=clock, r2=r2, posts=posts, msgs=msgs, run=run_tick, tmp=tmp_path)


def _hourly(world, days: int):
    start = world.clock.t
    for day in range(days):
        for h in CRON_HOURS_UTC:
            world.clock.t = start + day * 86400 + (h - 4) * 3600
            world.run()


def test_two_days_of_hourly_runs_post_on_time_once(world):
    _hourly(world, 2)
    by_platform = {}
    for platform, job, ts in world.posts:
        by_platform.setdefault(platform, []).append((job, ts))
    for platform, rows in by_platform.items():
        assert len(rows) == 4, platform  # 2 a day
        assert len({j for j, _ in rows}) == 4, platform  # never the same video twice
        for _, ts in rows:
            local = dt.datetime.fromtimestamp(ts, CAIRO)
            m = local.hour * 60 + local.minute
            # inside a window, or at most one hourly run late
            assert 15 * 60 <= m <= 18 * 60 or 20 * 60 <= m <= 23 * 60 + 30, local
    mornings = [m for m in world.msgs if m.startswith("☀️")]
    assert len(mornings) == 2 and "GitHub" in mornings[0]


def test_a_run_cut_off_after_posting_never_posts_twice(world, monkeypatch):
    _hourly(world, 1)
    n = len(world.posts)
    real_push = state_sync.push
    calls = [0]

    def push_once(s):  # the runner dies right after saving the post
        calls[0] += 1
        if calls[0] == 1:
            real_push(s)
        else:
            raise SystemExit("runner stopped")
    monkeypatch.setattr(state_sync, "push", push_once)
    monkeypatch.setattr(tick.state_sync, "push", push_once)
    day2 = world.clock.t + 86400 - 17 * 3600  # next day 04:17 UTC
    for h in CRON_HOURS_UTC:
        world.clock.t = day2 + (h - 4) * 3600
        calls[0] = 0
        try:
            world.run()
        except SystemExit:
            pass
        if len(world.posts) > n:
            break
    first_post_run = len(world.posts)
    assert first_post_run - n == 4  # one video, four platforms
    monkeypatch.setattr(state_sync, "push", real_push)
    monkeypatch.setattr(tick.state_sync, "push", real_push)
    world.clock.t += 3600
    world.run()
    jobs = [j for _, j, _ in world.posts[n:]]
    assert len(jobs) == len(set(zip(jobs, [p for p, _, _ in world.posts[n:]])))  # no repeat per platform


def test_pc_backup_waits_while_github_is_alive_then_takes_over(world):
    world.run()  # GitHub run writes the lease
    owner_before = state_sync.lease(None)["owner"]
    assert owner_before == "github"
    world.clock.t += 3600
    assert world.run(["--backup"]) == 0
    assert state_sync.lease(None)["owner"] == "github"  # PC stayed idle
    world.clock.t += 4 * 3600  # GitHub silent for 4 h
    world.run(["--backup"])
    assert state_sync.lease(None)["owner"] == "pc"


def test_github_skips_while_the_pc_backup_is_mid_run(world):
    world.run()
    state_sync.write_lease(None, "pc", "running")
    world.clock.t += 600
    before = dict(world.r2)
    world.run()
    assert world.r2["state.bin"] == before["state.bin"]


def test_paused_production_ends_the_run_quickly(world, monkeypatch):
    calls = [0]

    def paused(self):  # quota wait: produce does nothing
        calls[0] += 1
    monkeypatch.setattr(main_mod.App, "produce", paused)
    world.run()
    assert calls[0] == 1
