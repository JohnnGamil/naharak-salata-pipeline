"""Entrypoint: APScheduler loop (produce buffer, post on schedule, health checks).

    python -m src.main             # run forever (what the container does)
    python -m src.main --once      # produce one video now (no upload) and exit
    python -m src.main --post-now  # upload the oldest ready video now
    python -m src.main --status    # print jobs / buffer / quota
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import random
import signal
import sys
import time
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler

from .config import get_settings
from .db import DB
from .notify.telegram import Telegram
from .pipeline import Pipeline
from .util import setup_logging

log = logging.getLogger("main")
MAX_LATE_S = 4 * 3600  # a slot missed by more than this (machine was off) is skipped


def plan_slots(day: dt.date, n: int, windows: list[tuple[int, int]], tz: ZoneInfo,
               rnd: random.Random | None = None) -> list[float]:
    """Spread n posts over the windows (round robin), a random WHOLE hour inside each sub-window: the
    hourly run starts on the hour, so a 21:00 slot really posts at ~21:00 (John, 2026-10-28)."""
    rnd = rnd or random.Random()
    per_window: dict[int, int] = {}
    for k in range(n):
        per_window[k % len(windows)] = per_window.get(k % len(windows), 0) + 1
    out = []
    for wi, count in per_window.items():
        start, end = windows[wi]
        if end <= start:
            end += 24 * 60
        span = (end - start) / count
        for j in range(count):
            lo, hi = start + span * j, start + span * (j + 1)
            hours = [h for h in range(int(lo // 60), int(hi // 60) + 1) if lo <= h * 60 < hi]
            minute = 60 * rnd.choice(hours) if hours else 60 * int((lo + hi) / 2 // 60)
            base = dt.datetime.combine(day, dt.time(0, 0), tzinfo=tz)
            out.append((base + dt.timedelta(minutes=minute)).timestamp())
    return sorted(out)


class App:
    def __init__(self):
        self.s = get_settings()
        setup_logging(self.s.data_dir, self.s.log_level)
        self.db = DB(self.s.db_path)
        self.tg = Telegram(self.s.telegram_bot_token, self.s.telegram_chat_id, self.db)
        self.pipe = Pipeline(self.s, self.db, self.tg)
        self.tz = ZoneInfo(self.s.timezone)
        self.started = time.time()

    # --- jobs -----------------------------------------------------------------
    def ensure_slots(self) -> None:
        today = dt.datetime.now(self.tz).date()
        for day in (today, today + dt.timedelta(days=1)):
            key = day.isoformat()
            if self.db.q("SELECT 1 FROM post_slots WHERE day=? LIMIT 1", (key,)):
                continue
            for ts in plan_slots(day, self.s.posts_per_day, self.s.windows, self.tz):
                self.db.x("INSERT OR IGNORE INTO post_slots(day, due_ts) VALUES(?,?)", (key, ts))
            log.info("planned %d slots for %s", self.s.posts_per_day, key)

    def produce(self) -> None:
        ready = len(self.db.ready_jobs())
        if ready >= self.s.buffer_size and not self.db.unfinished_jobs():
            return
        backoff_until = self.db.get("produce_backoff_until", 0)
        if time.time() < backoff_until:
            return
        job_id = self.pipe.produce_one()
        if self.db.job(job_id)["status"] == "FAILED":
            fails = self.db.get("consecutive_failures", 0) + 1
            self.db.put("consecutive_failures", fails)
            if fails >= 3:  # something systemic: slow down instead of burning quotas
                wait = min(6 * 3600, 900 * 2 ** (fails - 3))
                self.db.put("produce_backoff_until", time.time() + wait)
                self.tg.send(f"⚠️ {fails} runs failed in a row - pausing production for {wait // 60} min",
                             throttle_key="fail_streak", throttle_s=3600)
        else:
            self.db.put("consecutive_failures", 0)

    def post_tick(self) -> None:
        now = time.time()
        due = self.db.q("SELECT * FROM post_slots WHERE status='PENDING' AND due_ts<=? ORDER BY due_ts", (now,))
        for slot in due:
            if now - slot["due_ts"] > MAX_LATE_S:
                self.db.x("UPDATE post_slots SET status='MISSED' WHERE id=?", (slot["id"],))
                continue
            job_id = self.pipe.upload_next()
            if job_id:
                self.db.x("UPDATE post_slots SET status='DONE', job_id=? WHERE id=?", (job_id, slot["id"]))
            break  # at most one post per tick; a failed slot is retried next minute until MAX_LATE_S

    def health(self) -> None:
        # hourly GitHub runs start fresh each time: count from the first run ever, kept in the DB
        first = self.db.get("first_seen_ts") or self.started
        if not self.db.get("first_seen_ts"):
            self.db.put("first_seen_ts", first)
        last = self.db.last_upload_ts() or first
        if time.time() - last > 24 * 3600 and time.time() - first > 24 * 3600:
            hours = (time.time() - last) / 3600
            self.tg.send(f"🚨 No successful upload in {hours:.0f} h. Check the logs.",
                         throttle_key="no_upload", throttle_s=6 * 3600)
        warn = self.db.get("quota_warning")
        if warn:
            self.tg.send(f"📊 Quota: {warn}", throttle_key="quota", throttle_s=24 * 3600)
            self.db.put("quota_warning", None)

    # --- run ------------------------------------------------------------------
    def serve(self) -> None:
        sched = BlockingScheduler(timezone=self.tz)
        sched.add_job(self.ensure_slots, "cron", hour=0, minute=1, id="plan")
        sched.add_job(self.produce, "interval", minutes=2, id="produce", max_instances=1, coalesce=True,
                      next_run_time=dt.datetime.now(self.tz) + dt.timedelta(seconds=5))
        sched.add_job(self.post_tick, "interval", minutes=1, id="post", max_instances=1, coalesce=True)
        sched.add_job(self.health, "interval", hours=1, id="health", max_instances=1, coalesce=True)
        self.ensure_slots()

        def stop(*_):
            log.info("shutting down")
            sched.shutdown(wait=False)

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        self.tg.send(f"🟢 Pipeline started: {self.s.posts_per_day} posts/day, windows {self.s.post_windows}, "
                     f"engine {self.s.video_engine}, buffer {self.s.buffer_size}",
                     throttle_key="startup", throttle_s=600)
        sched.start()

    def status(self) -> None:
        for r in self.db.q("SELECT id,status,engine,attempts,title,error FROM jobs ORDER BY id DESC LIMIT 15"):
            print(dict(r))
        print("ready:", len(self.db.ready_jobs()))
        print("kaggle GPU h (7d):", round(self.pipe.kaggle.hours_used_week(), 2))
        for r in self.db.q("SELECT * FROM post_slots ORDER BY due_ts DESC LIMIT 6"):
            print(dict(r), dt.datetime.fromtimestamp(r["due_ts"], self.tz).strftime("%Y-%m-%d %H:%M"))


def backup_loop() -> int:
    """Since 2026-10-28 GitHub Actions runs the channel; the PC (start_pipeline.bat at logon) only
    steps in through src.tick --backup when GitHub has been quiet for 3 hours."""
    import subprocess
    setup_logging(get_settings().data_dir, "INFO")
    log.info("PC backup mode: checking GitHub every 30 min")
    while True:
        subprocess.run([sys.executable, "-m", "src.tick", "--backup"])
        time.sleep(30 * 60)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--post-now", action="store_true")
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()
    s = get_settings()
    if not (args.once or args.post_now or args.status) and s.state_key and s.runner == "pc":
        return backup_loop()
    app = App()
    if args.once:
        job_id = app.pipe.produce_one()
        print(app.db.job(job_id)["status"], app.db.job(job_id)["video_path"])
    elif args.post_now:
        print(app.pipe.upload_next())
    elif args.status:
        app.status()
    else:
        app.serve()
    return 0


if __name__ == "__main__":
    sys.exit(main())
