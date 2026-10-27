"""One pass of the whole pipeline, for the hourly GitHub Actions run (and the PC backup):
pull the shared state, plan slots, post anything due, make videos for up to `--budget` minutes,
push the state back.

    python -m src.tick                 # GitHub runner
    python -m src.tick --backup        # PC: only runs when GitHub has been quiet for --stale hours
"""
from __future__ import annotations

import argparse
import logging
import sys
import time

from . import state_sync
from .config import get_settings

log = logging.getLogger("tick")


def run(budget_min: float) -> None:
    from .main import App  # after the state pull: App opens the DB

    app = App()
    end = time.time() + budget_min * 60
    app.ensure_slots()
    app.post_tick()
    app.pipe.retry_posts()
    while time.time() < end:
        if time.time() + 60 < end:
            app.post_tick()  # a slot may fall due while a long video is being made
        before = app.db.unfinished_jobs()
        ready = len(app.db.ready_jobs())
        if ready >= app.s.buffer_size and not before:
            break
        app.produce()
        after = app.db.unfinished_jobs()
        if after and before and after[0]["id"] == before[0]["id"] and after[0]["status"] == before[0]["status"]:
            break  # waiting on a quota (QuotaWait) or backing off: nothing more to do this hour
    app.post_tick()
    app.health()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=float, default=40, help="minutes of video making")
    ap.add_argument("--backup", action="store_true")
    ap.add_argument("--stale", type=float, default=3, help="hours of GitHub silence before the PC acts")
    args = ap.parse_args()
    s = get_settings()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
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
        run(args.budget)
    finally:
        state_sync.push(s)
        state_sync.write_lease(s, who, "done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
