"""Kaggle helpers.

    python -m scripts.kaggle_setup --check      # credentials + CLI work
    python -m scripts.kaggle_setup --weights    # one-time: cache fp16 model weights as kernel output
    python -m scripts.kaggle_setup --test-one   # run ONE scene of the latest job on the GPU
"""
from __future__ import annotations

import argparse
import sys
import time

from src.config import get_settings
from src.db import DB
from src.pipeline import Pipeline
from src.util import setup_logging


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--weights", action="store_true")
    ap.add_argument("--test-one", action="store_true")
    ap.add_argument("--job", type=int, help="job id to take the scene from (default: latest with audio)")
    args = ap.parse_args()
    s = get_settings()
    setup_logging(s.data_dir, "INFO")
    pipe = Pipeline(s, DB(s.db_path))
    k = pipe.kaggle
    if not s.kaggle_enabled:
        print("Set KAGGLE_USERNAME and KAGGLE_KEY (or KAGGLE_API_TOKEN) in .env")
        return 1
    if args.check or not (args.weights or args.test_one):
        print(k.cli("kernels", "list", "--mine", "--page-size", "5"))
        print("status of clip kernel:", k.status_safe())
    if args.weights:
        ref = k.push_weights_kernel()
        print(f"pushed {ref}. It runs ~20-30 min on CPU. When it shows 'complete' on kaggle.com,")
        print(f"put KAGGLE_WEIGHTS_KERNEL={ref} in .env")
    if args.test_one:
        rows = pipe.db.q("SELECT id FROM jobs WHERE status NOT IN ('NEW','IDEA','IMAGES') ORDER BY id DESC LIMIT 1")
        job_id = args.job or (rows[0]["id"] if rows else None)
        if job_id is None:
            print("no job with audio yet - run `python -m src.main --once` first")
            return 1
        spec = pipe._specs(job_id)[:1]
        out = s.data_dir / "kaggle_test" / "clips"
        t = time.time()
        clips = k.generate(spec, out, seed=int(t))
        print(f"done in {(time.time() - t) / 60:.1f} min -> {clips}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
