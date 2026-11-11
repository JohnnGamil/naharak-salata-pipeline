"""Visual QA: sample frames -> Gemini vision JSON score."""
from __future__ import annotations

import io
import logging
import subprocess
from pathlib import Path

from PIL import Image

from ..llm import LLMUnavailable
from ..util import ffprobe_duration

log = logging.getLogger(__name__)

PROMPT = """You are the quality gate for an automated TikTok channel of cute talking fruit/vegetable
cartoon characters. These {n} frames are sampled in order from one video.
Characters expected: {chars}
Scenes (in order): {scenes}
Ignore the burned-in Arabic subtitles and the title text.
{engine_note}

Score each criterion 0-10 (10 = perfect):
- deformities: 10 = no melted/garbled/extra limbs or broken faces, 0 = badly deformed
- character_visible: a clear cartoon fruit/vegetable character is visible and in frame
- matches_scene: frames fit the characters/scenes described
- appropriate: family friendly, nothing violent/sexual/disturbing/offensive
- mouth: 10 = every speaking face has exactly ONE mouth, where a mouth belongs (under the eyes, on the
  face); 0 = a face shows two mouths, or a mouth drawn on a cheek, the forehead, an eye or off the face
- overall: would a viewer find this watchable and appealing
Return ONLY JSON: {{"deformities": n, "character_visible": n, "matches_scene": n, "appropriate": n,
"mouth": n, "overall": n, "problems": ["short notes"]}}"""


def sample_frames(path: Path, n: int = 6, width: int = 480) -> list[bytes]:
    dur = ffprobe_duration(path)
    frames = []
    for i in range(n):
        t = dur * (i + 0.5) / n
        p = subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", str(path), "-frames:v", "1",
             "-vf", f"scale={width}:-2", "-f", "image2pipe", "-vcodec", "mjpeg", "-q:v", "4", "-"],
            capture_output=True,
        )
        if p.returncode == 0 and p.stdout:
            im = Image.open(io.BytesIO(p.stdout)).convert("RGB")
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=85)
            frames.append(buf.getvalue())
    return frames


# The CPU engine animates one still per character (mouth flap, zoom, bounce): it cannot act out
# "faints" or "waves", so judging it on literal actions fails every video (seen: matches_scene 3-5).
FALLBACK_NOTE = ("This video is a simple 2D animation of still character pictures (zoom, bounce, and a "
                 "drawn mouth that opens and closes over the picture - that is our channel's style, never a "
                 "problem). Do NOT expect the characters to perform the scene actions; judge "
                 "matches_scene only on whether the right characters appear.")


def check(path: Path, story, llm, min_score: float, require: bool, engine: str = "kaggle") -> dict:
    frames = sample_frames(path, n=max(6, len(story.scenes) + 1))  # every scene seen (its mouth is checked)
    if not frames:
        return {"passed": False, "problems": ["could not sample frames"]}
    chars = "; ".join(f"{c.id} ({c.look})" for c in story.characters)
    scenes = " | ".join(f"{s.character_id}: {s.visual_prompt_en}" for s in story.scenes)
    try:
        res = llm.vision_json(frames, PROMPT.format(
            n=len(frames), chars=chars, scenes=scenes, engine_note=FALLBACK_NOTE if engine == "fallback" else ""))
    except LLMUnavailable as e:
        log.warning("visual QA unavailable: %s", e)
        return {"passed": not require, "skipped": True, "problems": [] if not require else [str(e)]}
    except Exception as e:
        log.warning("visual QA error: %s", e)
        return {"passed": not require, "skipped": True, "problems": [f"vision error: {e}"]}
    if isinstance(res, list):  # some models wrap the object in a list
        res = res[0] if res else {}
    keys = ("deformities", "character_visible", "matches_scene", "appropriate", "overall")
    scores = {k: float(res.get(k, 0)) for k in keys}
    scores["mouth"] = float(res.get("mouth", 10))
    problems = list(res.get("problems") or [])
    passed = scores["overall"] >= min_score and scores["appropriate"] >= 7 and scores["character_visible"] >= 5
    if scores["mouth"] < 6:  # 2026-11-11 video 44: a second mouth on a cheek went out
        passed = False
        problems.append(f"mouth misplaced (score {scores['mouth']:.0f})")
    if not passed:
        problems.insert(0, f"visual scores {scores}")
    return {"passed": passed, "scores": scores, "problems": problems}
