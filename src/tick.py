"""One pass of the whole pipeline, for the hourly GitHub Actions run (and the PC backup):
pull the shared state, plan slots, post anything due, make videos for up to `--budget` minutes,
push the state back.

    python -m src.tick                 # GitHub runner
    python -m src.tick --backup        # PC: only runs when GitHub has been quiet for --stale hours
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
import sys
import time

from . import state_sync
from .config import get_settings

log = logging.getLogger("tick")


def _posted(app) -> int:
    return app.db.q("SELECT COUNT(*) AS n FROM posts WHERE status='DONE'")[0]["n"]


def morning(app, who: str) -> None:
    """First run of the Cairo day: one Telegram line that the machine is alive + today's plan."""
    today = dt.datetime.now(app.tz).date().isoformat()
    if app.db.get("morning_msg_day") == today:
        return
    app.db.put("morning_msg_day", today)
    slots = app.db.q("SELECT due_ts FROM post_slots WHERE day=? ORDER BY due_ts", (today,))
    times = " و ".join(dt.datetime.fromtimestamp(r["due_ts"], app.tz).strftime("%H:%M") for r in slots)
    ready = len(app.db.ready_jobs())
    making = app.db.unfinished_jobs()
    where = "GitHub" if who == "github" else "الكمبيوتر (GitHub واقف)"
    event = os.environ.get("GITHUB_EVENT_NAME", "")
    if who == "github":
        where += " - اشتغل لوحده في ميعاده ✅" if event == "schedule" else " (تشغيل يدوي)"
    app.tg.send(f"☀️ نهارك سلطة شغالة على {where}\n"
                f"مواعيد النهارده: حوالي {times or '-'} (كل فيديو بينزل في خلال ساعة من ميعاده)\n"
                f"فيديوهات جاهزة: {ready} | تحت التحضير: {len(making)}")


def run(budget_min: float, s, who: str) -> None:
    from .main import App  # after the state pull: App opens the DB

    app = App()
    end = time.time() + budget_min * 60
    app.ensure_slots()
    morning(app, who)

    def post_and_save() -> None:
        n = _posted(app)
        app.post_tick()
        if _posted(app) != n:  # save at once: a run cut off later must never post the same video twice
            state_sync.push(s)

    post_and_save()
    app.pipe.retry_posts()
    def snapshot():
        return ([(j["id"], j["status"]) for j in app.db.unfinished_jobs()], len(app.db.ready_jobs()),
                app.db.q("SELECT MAX(id) AS m FROM jobs")[0]["m"])

    while time.time() < end:
        before = snapshot()
        if before[1] >= app.s.buffer_size and not before[0]:
            break
        app.produce()
        if snapshot() == before:
            break  # waiting on a quota (QuotaWait) or backing off: nothing more to do this hour
        state_sync.push(s)  # keep finished stages even if the runner is stopped
        post_and_save()  # a slot may fall due while a long video is being made
    post_and_save()
    try:
        from .compose.weekly import maybe_post_weekly
        if maybe_post_weekly(app.pipe):
            state_sync.push(s)
    except Exception:
        log.exception("weekly video failed")
    app.health()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=float, default=40, help="minutes of video making")
    ap.add_argument("--backup", action="store_true")
    ap.add_argument("--stale", type=float, default=3, help="hours of GitHub silence before the PC acts")
    args = ap.parse_args()
    s = get_settings()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # its lines carry signed R2 links
    if not state_sync.enabled(s):
        log.error("STATE_KEY / R2 not set")
        return 2
    who = s.runner
    if args.backup:
        ls = state_sync.lease(s)
        if ls.get("owner") == "github" and time.time() - ls.get("ts", 0) < args.stale * 3600:
            log.info("GitHub ran %.0f min ago - PC stays idle", (time.time() - ls["ts"]) / 60)
            return 0
        who = "pc"
    else:
        ls = state_sync.lease(s)
        if ls.get("owner") == "pc" and ls.get("note") == "running" and time.time() - ls.get("ts", 0) < 3600:
            log.info("the PC backup is in the middle of a run - skipping this hour")
            return 0
    state_sync.write_lease(s, who, "running")
    state_sync.pull(s)
    try:
        run(args.budget, s, who)
    finally:
        state_sync.push(s)
        state_sync.write_lease(s, who, "done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
