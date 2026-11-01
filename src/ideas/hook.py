"""Hook doctor: the first line decides whether anyone watches (John, 2026-11-01: "close with the hook
more and more, very very strong").

After a script is written, a separate call writes 8 new first lines with proven scroll-stopping
patterns, a harsh judge scores them together with the current line, and the winner replaces the
first line only when it beats it clearly. The new line must still lead into line 2 and, for a loop
video, still follow from the last line. Series parts 2 and 3 keep their opening recap.
"""
from __future__ import annotations

import json
import logging

from ..llm import LLMUnavailable, extract_json

log = logging.getLogger(__name__)

MAX_HOOK_WORDS = 9
MIN_GAIN = 3.0  # points the new hook must win by (a coin-flip change is not worth the risk)

PATTERNS = """Proven scroll-stoppers for Egyptian TikTok (use a different one in each candidate):
1. Start in the middle of the disaster: "ماما لقت الفلوس اللي في الكنبة!"
2. A secret about to come out: "أنا عرفت ماما بتخبي الكحك فين"
3. Talk to the viewer about THEIR life: "لو عندك أخ صغير، متكملش الفيديو ده"
4. An impossible claim: "البطاطس دي اتجوزت امبارح ومحدش عزمنا"
5. A scream or panic with a reason: "الحقوا! حد فتح التلاجة الساعة ٣ الفجر!"
6. Ask a question everyone has an answer to: "مين فيكوا اتزنب علشان طبق الضيوف؟"
7. The confession: "أنا اللي كلت الجبنة.. وهحكيلكوا ليه"
8. The countdown / stakes: "فاضل دقيقة واحدة وماما توصل البيت"."""

DOCTOR = """You are the hook specialist of a viral Egyptian TikTok channel of talking fruits and vegetables.
The first line (the first 2 seconds) decides if people keep watching. Here is the script:

{script}

Current first line (said by {speaker}): "{first}"
{loop}
Write 8 NEW first lines for {speaker}, each a different pattern from this list:
{patterns}

Rules for every candidate:
- At most {max_w} words, Egyptian colloquial Arabic (عامية), easy for a child, said fast and urgently.
- It must fit THIS story and flow straight into line 2: "{second}".
- It creates a question in the viewer's head that only the rest of the video answers.
- Never a greeting, never "follow us", no violence, death, weapons, drugs or adult words.
- It must be TRUE to the story: nothing happens in the hook that does not happen in the video (no fake
  shock), and the character says only what they would really say at that moment.
- Don't give away the twist.

Then score ALL 9 lines (the current one is number 0) from 1 to 10 for: stop (would a stranger stop
scrolling?), curiosity (must they watch to the end?), fit (TRUE to the story, flows into line 2, still loops?),
clarity (understood instantly with no context?). Be harsh: most hooks are a 5.

Return ONLY JSON: {{"candidates": ["...", "..."], "scores": [{{"i": 0, "stop": 0, "curiosity": 0, "fit": 0,
"clarity": 0}}]}}  (scores for i = 0..8, where 1..8 are your candidates in order)"""

LOOP_NOTE = ('This video LOOPS: its last line is "{last}" and it flows back into the first line, so the new '
             "first line must still continue that last line naturally.")


def _total(sc: dict) -> float:
    # stopping the scroll counts twice (it is what the hook is for), and so does fitting the story:
    # a shocking line the video never pays off loses people at second 5
    return sum(float(sc.get(k) or 0) for k in ("stop", "stop", "curiosity", "fit", "fit", "clarity"))


def sharpen(llm, story, loop: bool = True):
    """Return the story with a stronger first line when one clearly wins (else unchanged)."""
    from .schema import Story
    first, second = story.scenes[0], story.scenes[1] if len(story.scenes) > 1 else story.scenes[0]
    speaker = story.char(first.character_id).name
    script = "\n".join(f"{story.char(sc.character_id).name}: {sc.line_ar}" for sc in story.scenes)
    try:
        raw = llm.text(DOCTOR.format(script=script, speaker=speaker, first=first.line_ar, second=second.line_ar,
                                     loop=LOOP_NOTE.format(last=story.scenes[-1].line_ar) if loop else "",
                                     patterns=PATTERNS, max_w=MAX_HOOK_WORDS),
                       json_mode=True, temperature=1.0)
        data = extract_json(raw)
        cands = [str(c).strip() for c in data.get("candidates") or [] if str(c).strip()]
        scores = {int(s["i"]): _total(s) for s in data.get("scores") or []
                  if isinstance(s, dict) and str(s.get("i", "")).lstrip("-").isdigit()}
    except LLMUnavailable:
        return story
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as e:
        log.warning("hook doctor skipped: %s", str(e)[:200])
        return story
    base = scores.get(0, 0.0)
    ranked = sorted(((scores.get(k + 1, 0.0), c) for k, c in enumerate(cands)
                     if len(c.split()) <= MAX_HOOK_WORDS + 2), reverse=True)
    for score, line in ranked:
        if score < base + MIN_GAIN:
            break
        data = story.model_dump()
        data["scenes"][0]["line_ar"] = line
        try:
            new = Story.model_validate(data)  # word count, banned words and فصحى are checked again
        except ValueError as e:
            log.info("hook candidate rejected (%s): %s", str(e)[:80], line)
            continue
        log.info("hook: %r (%.0f) -> %r (%.0f)", first.line_ar, base, line, score)
        return new
    log.info("hook kept (%.0f pts): %r", base, first.line_ar)
    return story
