"""Arabic proofreading of every text we show or speak (John, 2026-10-28: "no more Arabic mistakes").

Gemini writes fast and sometimes leaves typos ("استستنوا"), broken grammar ("مين حصل معاكوا كده") or a
word that means nothing. A second, careful pass reads the title, the post caption, every spoken line and
the vote question and fixes only what is wrong, keeping the Egyptian dialect, the meaning, the jokes
and the length. A fix that changes a line too much is refused (that would be a rewrite, not a
correction), and when the LLM is down the texts are kept as they are.
"""
from __future__ import annotations

import json
import logging
from difflib import SequenceMatcher

from ..llm import LLMUnavailable, extract_json
from .schema import Story

log = logging.getLogger(__name__)

PROMPT = """You are a strict proofreader of Egyptian colloquial Arabic (عامية مصرية) for a kids-friendly TikTok
channel of talking fruits and vegetables. Below are texts from our videos (JSON list of {{"id", "text"}}).

For EACH text fix ONLY real mistakes:
- typos and repeated/missing letters (e.g. "استستنوا" -> "استنوا"), wrong or missing hamza/taa marbuta where
  it changes the reading, words glued together or split wrongly;
- broken grammar or an unnatural phrase no Egyptian would say (e.g. "مين حصل معاكوا كده؟" -> "حد حصل معاه
  كده قبل كده؟"), wrong gender/number agreement (a female character: "طلعت", not "طلع");
- a word that does not exist or means something else.
- a missing article that sounds wrong ("طلعت ليمونة هي اللي" -> "طلعت الليمونة هي اللي").
Egyptian spelling IS correct here, never "fix" it to فصحى: تلاجة (not ثلاجة), كده, إزاي, دلوقتي, قولنا,
اتابعوا, إحنا, أوي, جبنة رومي / الجبنة الرومي, مش, عشان.
Keep: the Egyptian dialect (never turn it into فصحى), the meaning, the joke, the emojis, punctuation style,
the names, and about the same length. Do not add hashtags. If a text is already correct, return it unchanged.

Texts:
{items}

Return ONLY JSON: {{"texts": [{{"id": "...", "text": "corrected text"}}, ...]}} with every id, same order."""


def _fields(st: Story, k: int) -> list[tuple[str, str]]:
    out = [(f"{k}.title", st.title), (f"{k}.caption", st.caption_ar)]
    out += [(f"{k}.line{i}", sc.line_ar) for i, sc in enumerate(st.scenes)]
    if st.vote:
        out.append((f"{k}.vote_q", st.vote.get("question_ar", "")))
    return [(i, t) for i, t in out if t]


def _close(a: str, b: str) -> bool:
    return SequenceMatcher(None, a, b, autojunk=False).ratio() >= 0.6


def proofread(llm, stories: list[Story]) -> list[Story]:
    """Same stories with the Arabic fixed. Never raises; on any problem the originals are returned."""
    items = [(i, t) for k, st in enumerate(stories) for i, t in _fields(st, k)]
    try:
        raw = llm.text(PROMPT.format(items=json.dumps([{"id": i, "text": t} for i, t in items], ensure_ascii=False)),
                       json_mode=True, temperature=0.1)
        got = {str(x["id"]): str(x["text"]).strip() for x in extract_json(raw)["texts"]}
    except (LLMUnavailable, ValueError, KeyError, TypeError) as e:
        log.warning("proofreading skipped: %s", e)
        return stories
    out = []
    for k, st in enumerate(stories):
        d = st.model_dump()
        changed = []
        for i, old in _fields(st, k):
            new = got.get(i)
            if not new or new == old or not _close(old, new):
                continue
            field = i.split(".", 1)[1]
            if field == "title":
                d["title"] = new
            elif field == "caption":
                d["caption_ar"] = new
            elif field == "vote_q":
                d["vote"]["question_ar"] = new
            else:
                d["scenes"][int(field[4:])]["line_ar"] = new
            changed.append(f"{old} -> {new}")
        try:
            fixed = Story.model_validate(d)  # the banned-word and length rules still apply
        except ValueError as e:
            log.warning("proofread story %d rejected, kept the original: %s", k, str(e)[:200])
            fixed = st
            changed = []
        for c in changed:
            log.info("proofread: %s", c)
        out.append(fixed)
    return out
