"""Make the 'dryrun' test copy for tick.yml (dry=true): the real state + one ready video whose post
slot is due now. The real state is only read.

    python scripts/dry_seed.py
"""
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import state_sync  # noqa: E402
from src.config import Settings, get_settings  # noqa: E402
from src.db import DB  # noqa: E402

real = get_settings()
tmp = Path(tempfile.mkdtemp(prefix="dryseed_"))
s = Settings(data_dir=tmp, state_ns="dryrun")
for d in (s.data_dir, s.jobs_dir, s.cache_dir):
    d.mkdir(parents=True, exist_ok=True)
blob = state_sync._get(real, "state.bin")
state_sync.unpack(s, state_sync._fernet(s.state_key).decrypt(blob))
db = DB(s.db_path)
src_job = real.jobs_dir / "00004"
jid = db.new_job("fallback", status="QA_PASSED", title="DRY RUN test video")
jd = s.jobs_dir / f"{jid:05d}"
jd.mkdir(parents=True, exist_ok=True)
for f in ("story.json", "final.mp4", "cover.jpg", "images.json"):
    shutil.copy(src_job / f, jd / f)
db.update_job(jid, video_path=str(jd / "final.mp4"))
db.x("UPDATE jobs SET status='FAILED' WHERE status NOT IN ('UPLOADED','FAILED') AND id<>?", (jid,))  # no production
db.x("DELETE FROM post_slots")
db.x("INSERT INTO post_slots(day, due_ts) VALUES(?,?)", (time.strftime("%Y-%m-%d"), time.time() - 120))
db.put("produce_backoff_until", time.time() + 6 * 3600)
del db
state_sync.push(s)
state_sync.write_lease(s, "pc", "seeded dry run")
print("dry run seeded: job", jid)
shutil.rmtree(tmp, ignore_errors=True)
