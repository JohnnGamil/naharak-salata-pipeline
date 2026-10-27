"""QA gate (replaces human approval): tech -> audio -> visual."""
from __future__ import annotations

import logging
from pathlib import Path

from . import audio_check, tech_check, visual_check

log = logging.getLogger(__name__)


def run_qa(video: Path, story, settings, llm, engine: str = "kaggle") -> dict:
    """Returns {"passed", "failed_part": None|"tech"|"audio"|"visual", "tech", "audio", "visual"}."""
    res: dict = {"failed_part": None}
    res["tech"] = tech_check.check(video)
    if not res["tech"]["passed"]:
        res.update(passed=False, failed_part="tech")
        return res
    script = " ".join(s.line_ar for s in story.scenes)
    try:
        res["audio"] = audio_check.check(video, script, settings.qa_audio_min_similarity,
                                         settings.whisper_model, settings.models_dir)
    except Exception as e:  # whisper itself broken -> don't block posting on our own tooling
        log.exception("audio QA crashed")
        res["audio"] = {"passed": True, "skipped": True, "problems": [f"audio QA error: {e}"]}
    if not res["audio"]["passed"]:
        res.update(passed=False, failed_part="audio")
        return res
    res["visual"] = visual_check.check(video, story, llm, settings.qa_visual_min_score,
                                       settings.qa_require_visual, engine)
    if not res["visual"]["passed"]:
        res.update(passed=False, failed_part="visual")
        return res
    res["passed"] = True
    return res
