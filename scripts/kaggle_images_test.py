"""Try the Kaggle character-picture kernel on the characters of one job (nothing is cached).

    python -m scripts.kaggle_images_test --job 2
"""
from __future__ import annotations

import argparse
import sys

from src.config import get_settings
from src.db import DB
from src.pipeline import Pipeline
from src.util import setup_logging


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", type=int, required=True)
    args = ap.parse_args()
    s = get_settings()
    setup_logging(s.data_dir, "INFO")
    pipe = Pipeline(s, DB(s.db_path))
    story = pipe.story(args.job)
    best = pipe.images._kaggle_batch(story.characters, s.data_dir / "kaggle_images_test")
    for cid, path in best.items():
        print(cid, "->", path)
    return 0 if best else 1


if __name__ == "__main__":
    sys.exit(main())
