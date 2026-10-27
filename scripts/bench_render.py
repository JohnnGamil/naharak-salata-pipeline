"""Time one full CPU render (fallback engine + compose) of a saved job, e.g. on a GitHub runner.

    python scripts/bench_render.py bench/job4
"""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.compose.composer import compose  # noqa: E402
from src.ideas.schema import Story  # noqa: E402
from src.video.base import SceneSpec  # noqa: E402
from src.video.fallback_engine import FallbackEngine  # noqa: E402


def local(base: Path, p: str) -> Path:
    """Saved jobs hold absolute Windows paths; find the same file inside the fixture folder."""
    name = Path(p.replace("\\", "/")).name
    hits = list(base.rglob(name))
    return hits[0]


def main(src: str) -> None:
    base = Path(src).resolve()
    work = base.parent / f"{base.name}_out"
    shutil.rmtree(work, ignore_errors=True)
    (work / "clips").mkdir(parents=True)
    story = Story.model_validate(json.loads((base / "story.json").read_text(encoding="utf-8")))
    imgs = json.loads((base / "images.json").read_text(encoding="utf-8"))
    audio = json.loads((base / "audio" / "audio.json").read_text(encoding="utf-8"))
    for a in audio:
        a["wav"] = str(local(base, a["wav"]))
    per_scene = imgs.get("__scenes__") or [imgs[sc.character_id] for sc in story.scenes]
    eng = FallbackEngine()
    t0 = time.time()
    clips = []
    for i, (sc, a) in enumerate(zip(story.scenes, audio)):
        spec = SceneSpec(index=i, character_id=sc.character_id, image=local(base, per_scene[i]["path"]),
                         meta=per_scene[i]["meta"], audio=Path(a["wav"]), duration=a["duration"],
                         emotion=sc.emotion, prompt=sc.visual_prompt_en)
        clips.append(eng.render_scene(spec, work / "clips" / f"scene_{i:02d}.mp4", 41))
        print(f"scene {i}: {time.time() - t0:.0f}s", flush=True)
    t1 = time.time()
    final = compose(story, audio, clips, work)
    t2 = time.time()
    secs = sum(a["duration"] for a in audio)
    print(f"RESULT video {secs:.0f}s long | scenes {t1 - t0:.0f}s + compose {t2 - t1:.0f}s = {t2 - t0:.0f}s -> {final}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "bench/job4")
