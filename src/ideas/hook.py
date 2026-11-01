"""Hook doctor: the first line decides whether anyone watches (John, 2026-11-01: "close with the hook
more and more, very very strong").

After a script is written, one call writes 8 new first lines, a SEPARATE judge call (which never
learns which line is new) checks each against the whole script and scores it, and the winner replaces
the first line only when it is true to the story and beats the old line clearly. Series parts 2 and 3
keep their opening recap.

First version (2026-11-01) let the writer score its own lines: it copied the example lines word for word
("لو عندك أخ صغير، متكملش الفيديو ده" on a potato-and-chicken video) and broke line 2 ("دي رجعت..."
with no ماما left to point at). So: no copyable examples, the line must share a word with the story,
and the judge sees the script exactly as the viewer would hear it.
"""
from __future__ import annotations

import json
import logging
import random
import re

from ..llm import LLMUnavailable, extract_json

log = logging.getLogger(__name__)

MAX_HOOK_WORDS = 9
MIN_GAIN = 3.0  # points the new hook must win by (a coin-flip change is not worth the risk)

PATTERNS = """Scroll-stopping openings (use a different one in each candidate; describe THIS story, never a
generic line that could open any video):
1. Start in the middle of this story's problem, as it is happening.
2. A secret of this story that is about to come out.
3. Speak to the viewer about the same thing in their own home.
4. A crazy but true claim about what happens in this story.
5. A panic, with this story's reason in the same line.
6. A question about this story's situation that every Egyptian has an answer to.
7. A confession of what this character really did in this story.
8. The clock is ticking: what happens in this story if they don't hurry."""

WRITER = """You are the hook specialist of a viral Egyptian TikTok channel of talking fruits and vegetables.
The first line (the first 2 seconds) decides if people keep watching. Here is the script:

{script}

Write 8 NEW first lines for {speaker} (who says line 1 now: "{first}"), each a different pattern:
{patterns}

Rules for every candidate:
- At most {max_w} words, Egyptian colloquial Arabic (عامية), easy for a child, said fast and urgently.
- It names something concrete from THIS script (a character, an object, the problem).
- Line 2 ("{second}") must still make sense right after it: if line 2 says دي/ده/هي/هو/دول, the new
  line must say who or what that is.
{loop}- TRUE to the story: nothing happens in it that does not happen in the video, no confession of
  something the character did not do, no character who is not in the video.
- Never a greeting, never "follow us", no violence, death, weapons, drugs, insults or adult words.
- Don't give away the twist.

Return ONLY JSON: {{"candidates": ["...", "..."]}}"""

JUDGE = """You are a strict TikTok script editor for an Egyptian family channel. Below is a script with its
first line missing, then {n} possible first lines. Read each one as the opening of THIS script, the way a
viewer will hear it.

{rest}

Possible first lines (said by {speaker}):
{options}

For each option give:
- "true": 1 only if everything it says really happens or is true in this script, it does not reveal the
  twist, and the script still makes sense after it (line 2 still understood, nothing points at
  someone who was not mentioned); otherwise 0.
- "stop" 1-10: would a stranger stop scrolling?
- "curiosity" 1-10: must they watch to the end to know what happens?
- "clarity" 1-10: understood instantly with no context?
Be harsh: most lines are a 5.

Return ONLY JSON: {{"scores": [{{"k": "A", "true": 1, "stop": 0, "curiosity": 0, "clarity": 0}}]}}"""

LOOP_NOTE = ('- This video LOOPS: its last line "{last}" plays right before the first line again, so the new '
             "first line must still follow it naturally.\n")

_STOP = {"انا", "انت", "انتي", "احنا", "انتوا", "ده", "دي", "دول", "هو", "هي", "هم", "في", "من", "على", "علي",
         "عشان", "علشان", "لما", "لو", "بس", "مش", "ايه", "ليه", "مين", "كل", "اللي", "يا", "ولا", "وانا", "الحقوا",
         "الحقوني", "دلوقتي", "حد", "حاجه", "اوي", "خالص", "كده", "بقى", "هنا", "عند", "عندك", "مع", "والله"}


def _words(text: str) -> set[str]:
    t = re.sub(r"[ً-ْـ]", "", text).replace("أ", "ا").replace("إ", "ا").replace("آ", "ا").replace("ة", "ه")
    out = set()
    for w in re.findall(r"[ء-ي]+", t):
        for p in ("وال", "بال", "فال", "لل", "ال"):
            if w.startswith(p) and len(w) - len(p) >= 3:
                w = w[len(p):]
                break
        if len(w) >= 3 and w not in _STOP:
            out.add(w)
    return out


def grounded(line: str, story) -> bool:
    """The hook mentions something that is in the story (a stem match on any content word)."""
    rest = " ".join([sc.line_ar for sc in story.scenes[1:]] + [c.name for c in story.characters]
                    + [story.title])
    have = _words(rest)
    return any(w in have or any(h.startswith(w[:4]) or w.startswith(h[:4]) for h in have if len(h) >= 4)
               for w in _words(line) if len(w) >= 3)


def _total(sc: dict) -> float:
    # stopping the scroll counts twice: it is what the hook is for
    return sum(float(sc.get(k) or 0) for k in ("stop", "stop", "curiosity", "clarity"))


def sharpen(llm, story, loop: bool = True):
    """Return the story with a stronger first line when one clearly wins (else unchanged)."""
    from .schema import Story
    first, second = story.scenes[0], story.scenes[1] if len(story.scenes) > 1 else story.scenes[0]
    speaker = story.char(first.character_id).name

    def say(sc):
        return f"{story.char(sc.character_id).name}: {sc.line_ar}"

    script = "\n".join(say(sc) for sc in story.scenes)
    try:
        raw = llm.text(WRITER.format(script=script, speaker=speaker, first=first.line_ar, second=second.line_ar,
                                     loop=LOOP_NOTE.format(last=story.scenes[-1].line_ar) if loop else "",
                                     patterns=PATTERNS, max_w=MAX_HOOK_WORDS),
                       json_mode=True, temperature=1.0)
        cands = [str(c).strip() for c in extract_json(raw).get("candidates") or [] if str(c).strip()]
        cands = [c for c in dict.fromkeys(cands) if c != first.line_ar and len(c.split()) <= MAX_HOOK_WORDS + 2]
        kept = [c for c in cands if grounded(c, story)]
        if len(kept) < len(cands):
            log.info("hook candidates about nothing in the story dropped: %s", [c for c in cands if c not in kept])
        if not kept:
            return story
        lines = [first.line_ar, *kept]
        order = list(range(len(lines)))
        random.shuffle(order)  # the judge never knows which line is the original
        keys = [chr(ord("A") + k) for k in range(len(lines))]
        options = "\n".join(f'{keys[k]}. "{lines[i]}"' for k, i in enumerate(order))
        rest = "1. (first line goes here)\n" + "\n".join(f"{n}. {say(sc)}" for n, sc in enumerate(story.scenes[1:], 2))
        if loop:
            rest += "\n(then the video loops back to line 1)"
        raw = llm.text(JUDGE.format(n=len(lines), rest=rest, speaker=speaker, options=options),
                       json_mode=True, temperature=0.2)
        by_key = {str(s.get("k", "")).strip().upper()[:1]: s for s in extract_json(raw).get("scores") or []
                  if isinstance(s, dict)}
    except LLMUnavailable:
        return story
    except (ValueError, TypeError, KeyError, AttributeError, json.JSONDecodeError) as e:
        log.warning("hook doctor skipped: %s", str(e)[:200])
        return story
    scored = {}
    for k, i in enumerate(order):
        sc = by_key.get(keys[k])
        if sc is not None:
            scored[i] = (int(float(sc.get("true") or 0)) == 1, _total(sc))
    if 0 not in scored:  # the judge skipped the original: no fair comparison
        return story
    base = scored[0][1]
    ranked = sorted(((s, lines[i]) for i, (ok, s) in scored.items() if i and ok), reverse=True)
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
