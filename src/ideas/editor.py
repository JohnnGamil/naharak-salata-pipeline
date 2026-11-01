"""Script editor: reads a finished script the way a viewer hears it and lists real story mistakes, so the
writer gets one more try before any picture or voice is made (John, 2026-11-01: "the script isn't even
right"; a lemon calling "الحقي يا ليمونة!" to herself, a sweet potato that is a boy in part 1 and a girl in
part 3, a part 3 without its hero). The proofreader fixes words; this one checks that the story holds up.
"""
from __future__ import annotations

import json
import logging

from ..llm import LLMUnavailable, extract_json

log = logging.getLogger(__name__)

PROMPT = """You are the script editor of an Egyptian TikTok channel for families: cute talking fruits and
vegetables, Egyptian colloquial Arabic. Read this script exactly as a viewer will hear it.

{script}

List only REAL mistakes a viewer would notice:
1. A character talks to themselves by their own name, or talks to someone who is not in this video.
2. A character's gender changes (ده/دي، هو/هي، طلع/طلعت) or does not match the cast list.
3. A line contradicts an earlier line, or makes no sense after the line before it.
4. A character the story is about disappears, or the ending does not follow from the story.
5. A line nobody understands the first time, or a word used for the wrong meaning.
6. Something scary or cruel for children actually happens to a character (really eaten, burned, hurt,
   dying). Jokes and fear of it are fine.
7. An insult or a rude word.
{vote_rule}Do NOT list style wishes, jokes you would write differently, or Egyptian spelling. "خخخخ" is our snore
sound (fine). Parts 2 and 3 open with a short recap of the part before (fine).

Return ONLY JSON: {{"problems": ["where (part/line) + what is wrong + how to fix it"]}} - an empty list if
the script is fine."""

VOTE_RULE = ("8. The vote question at the end of part 1 cannot be answered by someone who watched only part 1, "
             "or its choices are not shown in part 1.\n")


def _script(stories) -> str:
    out = []
    for k, st in enumerate(stories, 1):
        head = f"PART {k}" if len(stories) > 1 else "VIDEO"
        cast = ", ".join(f"{c.name} ({'female' if 'Salma' in c.voice else 'male'})" for c in st.characters)
        out.append(f"{head}: {st.title}\nCast: {cast}")
        out += [f"{n}. {st.char(sc.character_id).name}: {sc.line_ar}" for n, sc in enumerate(st.scenes, 1)]
        if st.vote:
            o = st.vote["options"]
            out.append(f"(vote after part 1: {st.vote['question_ar']} 1) {o[0]} 2) {o[1]})")
    return "\n".join(out)


def problems(llm, stories) -> list[str]:
    """Real story mistakes in the script(s); [] when it is fine or the editor is not reachable."""
    prompt = PROMPT.format(script=_script(stories), vote_rule=VOTE_RULE if any(st.vote for st in stories) else "")
    try:
        try:  # a strong model reads more carefully; a lite one still catches the obvious slips
            raw = llm.text(prompt, json_mode=True, temperature=0.2, writer=True)
        except LLMUnavailable:
            raw = llm.text(prompt, json_mode=True, temperature=0.2)
        data = extract_json(raw)
        rows = data if isinstance(data, list) else data.get("problems") or []
    except LLMUnavailable:
        return []
    except (ValueError, TypeError, KeyError, AttributeError, json.JSONDecodeError) as e:
        log.warning("script editor skipped: %s", str(e)[:200])
        return []
    found = [str(p).strip() for p in rows if str(p).strip()]
    if found:
        log.info("script editor found: %s", found)
    return found
