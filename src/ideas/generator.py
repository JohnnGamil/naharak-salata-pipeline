"""Idea generator: LLM -> validated Story JSON, with an offline fallback bank."""
from __future__ import annotations

import datetime as dt
import json
import logging
import random

from pydantic import ValidationError

from ..llm import LLM, LLMUnavailable, extract_json
from . import cast
from .offline_bank import SKITS
from ..config import get_settings
from .hook import sharpen
from .proofread import proofread
from .schema import FORMATS, MAX_WORDS, MIN_WORDS, VOICES, Story

log = logging.getLogger(__name__)

PROMPT = """You write viral TikTok skits for an Egyptian audience in the trending style of
"talking fruits & vegetables" AI videos (cute 3D Pixar-like produce characters who talk).

Write ONE new skit that BOTH kids and adults enjoy (think Pixar / Bakkar / Bougy & Tamtam):
- A clear mini-story: a funny problem -> it escalates -> a surprising twist or payoff at the end.
  Characters have strong, lovable personalities (the drama queen, the know-it-all, the scaredy-cat).
- Jokes work on two levels: silly and visual for kids, relatable Egyptian everyday life for adults
  (mom's cooking, the fridge, the market, prices, school, exams, Ramadan, summer, family visits).
- HOOK = the FIRST line (first 2 seconds), at most 9 words, said fast. Start IN the problem, never before it:
  a disaster already happening ("ماما لقت الفلوس!"), a secret about to come out, a line that talks to the
  viewer's own life ("لو عندك أخ صغير متكملش الفيديو ده"), an impossible claim, or a panic with a reason.
  It opens a question only the end answers. Never a greeting, never an introduction of who is talking.
  The first picture is the most striking one (extreme close-up of the shocked face, or the chaos itself).
- Line 2 lands within 3 seconds and makes it WORSE (raises the stakes) - nobody leaves after line 2.
- QUALITY BAR (we want the best videos on Egyptian TikTok, not filler): every line must earn its place
  with a joke, a reveal or a new problem; cut any line that only explains. Use SPECIFIC Egyptian home
  details people recognise instantly (طبق الضيوف، كيس الأكياس، الكنبة اللي في الأنتريه، جروب العيلة،
  فاتورة الكهربا، الريموت). Comedy comes from WHO the characters are (the lazy one solves it lazily, the
  drama queen makes it a catastrophe). Call back to an early line in the ending. The twist must be
  surprising but obvious in hindsight.
- Something changes every scene (new emotion, new action or new place) so the video never feels static.
- End with a punchline or twist, and sometimes a question to the viewers to make them comment.
- Genre/format: {fmt}  (drama = emotional twist, comedy = jokes, advice = useful kitchen/health tip,
  rivalry = two characters arguing who is better). Inside that, pick a fresh theme: a mystery (who ate
  the cheese?), a prank, a competition, a misunderstanding, a cooking disaster, a health/kitchen tip,
  a holiday or season moment, school, the market, a family visit...
- Language of every line_ar: natural EGYPTIAN colloquial Arabic (عامية مصرية), not Modern Standard Arabic.
  Write it the way Cairenes really talk, as if transcribing a funny Egyptian sitcom: إيه، ليه، فين، إمتى،
  دلوقتي، كده، بتاع، أوي، خالص، يعني، بقى، طب، ماشي، يا عم، يا جدعان، يا نهار أبيض، والنبي، إنت مالك.
  Use Egyptian verb forms (بقولك، هعمل، مش هقدر، ماتزعلش) and Egyptian negation (مش / ما...ش).
  NEVER use فصحى words like: ماذا، لماذا، الآن، سوف، لن، هذا، ليس، أريد، جداً، لكن، ذهب، رأى.
  Short punchy spoken sentences, real Egyptian jokes and expressions, family-friendly,
  no politics, no religion, no insults about people.
- This is a KIDS-FRIENDLY channel and TikTok moderation is strict: NEVER use words about violence,
  death, blood, weapons, crime words like قاتل/قتل/جريمة دم, drugs, alcohol, smoking, kissing or
  anything adult - not even as a joke (no "هموت من الضحك", no "هقتلك"). For a mystery use المتهم,
  اللغز, التحقيق, مين عملها. Nobody gets hurt; the worst that happens is funny chaos.
- {min_s}-{max_s} scenes. Total words across all line_ar between {min_w} and {max_w} (30-60 seconds of speech).
- One speaking character per scene. 1-3 characters, mostly from the MAIN CAST below; at most one guest.
  When a line talks about ماما or بابا and they are in the MAIN CAST, they can be IN the video (better
  than only talking about them). Never write human characters: the whole family is fruits and vegetables.
  Use each character's catchphrase about once when it fits. First scene must be a strong hook.
  Last scene: see the ending rules at the end (loop for a single video, cliffhanger for a series).
- Each character gets a voice: "ar-EG-ShakirNeural" (male) or "ar-EG-SalmaNeural" (female).
- character "look": copy the cast's look; for a brand-new character write an English visual description
  (cute 3D cartoon fruit/vegetable with a face).
- scene "visual_prompt_en": English description of ONE striking picture for that line: the character's
  pose, action and facial expression, the place (kitchen counter, inside the fridge, market stall, school
  lunchbox, frying pan...) and the camera (close-up / wide shot / low angle). Make each scene's picture
  different and fun to look at. Only that scene's speaking character in focus. No text in the image.
- If a character snores or sleeps, write the snore in the line as "خخخخ" (a real snore sound is added).
  Use "خخخخ" ONLY for snoring (never for a cough, a laugh or crying).
- Never invent news that sounds real: no fake weather forecasts, official announcements, prices, dates or
  "عاجل" breaking news. Jokes come from the characters, not from made-up facts.
- emotion: one of neutral, happy, angry, sad, excited, scared, sarcastic, surprised.
- caption_ar: short Egyptian Arabic TikTok caption with 1-2 emojis and a question that invites comments.
- hashtags: exactly 4, Arabic, no '#', no spaces (use _): 1 broad Egyptian one people really search
  (كوميديا_مصرية، ضحك، مصر), 2 specific to THIS story's topic (e.g. الجبنة_الرومي، مطبخ_ماما، الامتحانات),
  1 about the format/genre (حكايات_الخضار، كارتون). Never fyp/foryou/explore/viral style tags.

{bible}

{premise}

Do NOT repeat any of these recent titles or their jokes:
{recent}

{answer}
"""

STORY_SHAPE = """{{"title": "...", "format": "{fmt}",
 "characters": [{{"id": "tomato", "name": "طمطم", "voice": "ar-EG-ShakirNeural", "look": "..."}}],
 "scenes": [{{"character_id": "tomato", "line_ar": "...", "visual_prompt_en": "...", "emotion": "angry"}}],
 "caption_ar": "...", "hashtags": ["..."]}}"""

SINGLE_TASK = """LOOP ENDING (this video replays automatically on TikTok/Reels/Shorts; a seamless loop makes
people watch it twice without noticing - the strongest signal for the algorithm):
- The LAST line must flow straight back into the FIRST line, as if the video never ended. Write the last
  line as a set-up that the first line answers or continues, e.g. last: "...ومن ساعتها كل ما حد يفتح التلاجة
  بيسمع صوت بيقول" -> first: "مين اللي كل الجبنة؟!". Or the last line starts the same situation again.
- No goodbye, no "follow us", no question to the viewers in the last line (it breaks the loop); the comment
  question goes in caption_ar instead. Don't end on a full stop of the story - end mid-flow.
Return ONLY JSON in exactly this shape:
""" + STORY_SHAPE

SERIES_TASK = """This time write a MINI-SERIES of exactly 3 parts (3 separate videos posted one after another).
- This is our BIG show: big emotions, DRAMA, ACTION and FIGHTS like a Pixar movie or a Ramadan series -
  stakes that feel huge to tiny vegetables, cartoon slapstick (chases, falls, spills, flour explosions,
  getting stuck in the sink), shouting matches, betrayals, shocking reveals. Be wildly creative.
  Action words that are fine: خناقة، معركة، مطاردة، هروب، خطة، خيانة، كمين، انتقام الكوسة (playful).
  Still NEVER hitting/hurting/violence words (no ضرب/قتل/دم/سلاح...).
- One story arc: part 1 sets up the problem, part 2 makes it much worse, part 3 brings the payoff/twist.
- Parts 1 and 2 END on a CLIFFHANGER that makes it impossible not to watch the next part: a shocking reveal
  ("الخاين طلع... أنا؟!"), a character in danger (Mama opens the fridge door!),
  or a plan going totally wrong (Mama's footsteps at the kitchen door!). Cut at the most exciting moment. The characters never say "follow us" /
  "watch the next part" themselves: the channel adds that line after the story.
- Parts 2 and 3 START with a quick 1-line reminder of what happened, then a NEW hook.
- Part 3 is the BIGGEST episode, never a calm wrap-up: the climax (the biggest chase/battle/reveal), then the
  twist, then a short emotional or hilarious payoff. Each part escalates.
- A catchphrase appears AT MOST ONCE per part (never at the start of every line). Characters talk like real
  people reacting to each other, not like they're reading their slogan.
- VIEWERS DECIDE: part 1 gets a "vote" field: {{"question_ar": "a short Egyptian question", "options":
  ["choice 1", "choice 2"]}} - a real fork in the story (who is the traitor? which plan do they try? who
  goes into the freezer?). Both choices must lead somewhere fun. Write parts 2 and 3 as if choice 1 wins
  (they are rewritten later if choice 2 wins). Parts 2 and 3 have no "vote".
- Each part must still be funny on its own (someone who only sees one part still laughs).
- EACH part is a full video on its own: 5-7 scenes and at least 85 words of dialogue PER PART
  (short parts get rejected). Every part follows ALL the rules above (language etc.) and keeps the same
  characters (same ids, names, voices, looks). Each part gets its own title and caption.
Return ONLY JSON in exactly this shape:
{{"series_title": "short catchy Egyptian Arabic name of the series",
 "parts": [STORY, STORY, STORY]}}
where each STORY is:
""" + STORY_SHAPE


BRAINSTORM = """You are the head writer of a fast-growing Egyptian TikTok channel of talking fruits and
vegetables (kids AND adults watch it). Goal: VIRAL reach, shares and new followers.
Format: {fmt}. {kind}
Right now in Egypt: {season}

{bible}

Brainstorm 10 DIFFERENT premises. What goes viral on Egyptian TikTok:
- "ده أنا بالظبط" relatable moments every Egyptian family lives (mom hiding the good food for guests,
  the fridge at 3am, the guests' plate, the bargaining at the market, the son who never helps, the
  exam night, the WhatsApp family group, the electricity bill, the cousin who brags).
- The produce fits the joke (the onion makes everyone cry, the lemon is sour/jealous, the potato is
  lazy on the sofa, the tomato is a drama queen whose price goes up and down).
- A shock in the first 2 seconds, a twist nobody expects, a last line people quote in the comments.
- Built-in comment bait: viewers take sides ("انتو مع مين؟"), guess the answer, or tag a friend.
- Easy to understand with no context (most viewers never saw the channel before).
Avoid these used ideas: {recent}
Family-friendly only: no violence, death, crime, drugs, romance, politics or religion.

Return ONLY JSON: {{"ideas": [{{"premise": "2-3 sentences in English", "hook_ar": "the first line in Egyptian Arabic",
 "twist": "the ending", "comment_bait": "why people will comment or share"}}]}}"""

JUDGE = """You are a tough TikTok editor who has seen millions of Egyptian videos. Score each idea 1-10 on:
hook (does the first line stop the scroll?), relatable (would an Egyptian say "ده أنا"?), twist,
shareable (would people send it to family/friends or tag someone?), comments (will people argue/answer?),
kids (does a 7-year-old enjoy it too?), cliffhanger (for a series: will people NEED the next part?
for a single video: is the ending a surprise?). Be harsh: most ideas are a 5.
Ideas:
{ideas}
Return ONLY JSON: {{"scores": [{{"i": 0, "hook": 0, "relatable": 0, "twist": 0, "shareable": 0, "comments": 0,
 "kids": 0, "cliffhanger": 0, "fix": "one change that would make it more viral"}}]}}"""


# Big-story genres for the 3-part series (John, 2026-10-28: "drama, action, fights - very creative, aim
# for a million views"). Action is cartoon slapstick only: chases, falls, flour clouds, never hitting.
SERIES_GENRES = [
    "HEIST: the produce plan a crazy mission to steal back something (the last piece of cake, the TV remote, "
    "mom's secret recipe book) from the top shelf / the freezer, with a plan, a traitor and a chase",
    "GREAT ESCAPE: the salad bowl is going to be made tonight! the vegetables plan a breakout from the fridge "
    "before mom comes back from the market; it goes wrong in funny ways; last-second twist",
    "BIG RIVALRY / FIGHT: two characters' war for something (best room in the fridge, who's mom's favourite, "
    "who leads the salad); the fridge splits into two teams, a food-fight battle (flour, water, bubbles), "
    "then they must team up against a bigger problem",
    "BETRAYAL DRAMA: the best friend secretly joins the other side; tears, a shocking reveal, a reunion",
    "COMPETITION SHOW: a talent show / race / cooking contest inside the kitchen with eliminations, a judge, "
    "a cheater, and an underdog who wins",
    "MYSTERY: something vanished or a weird thing happens at night; suspects, fake clues, a twist nobody saw",
    "NEW ENEMY: a terrifying new arrival (the blender, the cat, mom's diet plan, the mold in the corner, the "
    "new fancy imported fruit who thinks he's better than Egyptian produce) and the gang must stop it",
    "LOST & FOUND ADVENTURE: one character falls behind the fridge / gets taken to the market by mistake / "
    "ends up in the school lunchbox; the others go on a dangerous adventure to bring them back",
]


def season_hint(today: dt.date | None = None) -> str:
    """What Egyptians are living through this month (topical ideas travel further)."""
    d = today or dt.date.today()
    m = d.month
    notes = {
        1: "winter cold, mid-year exams, mid-year school break",
        2: "winter, second school term starts, Ramadan is coming (shopping for ياميش)",
        3: "spring, Mother's Day 21 March, Ramadan/Eid season",
        4: "spring, شم النسيم (فسيخ, رنجة, بصل, بيض ملون), Eid",
        5: "hot weather starts, end-of-year exams, ثانوية عامة stress",
        6: "summer, ثانوية عامة exams, the beach trip, mangoes are back",
        7: "peak summer heat, the beach (الساحل/إسكندرية), mangoes and watermelon, results day",
        8: "summer holiday, the beach, watermelon, getting bored at home",
        9: "back to school, buying supplies and uniforms, school lunchbox",
        10: "school routine, homework, weather getting nicer, guavas and dates season",
        11: "autumn, first-term exams coming, oranges and sweet potato (بطاطا) carts",
        12: "winter, cold nights, hot drinks, sweet potato carts, first-term exams, New Year",
    }[m]
    return f"{d:%B %Y}: {notes}. Ramadan ~ Feb, Eid al-Adha ~ May/June (only light, non-religious jokes)."


def pick_format(recent: list[str], weights: dict[str, float] | None = None) -> str:
    """Rotate: the format used least recently (never used first). With the 2-weekly review's weights
    the formats our viewers watch most come round more often (a weighted draw by age x weight)."""
    if weights:
        age = {f: min(recent.index(f) if f in recent else len(FORMATS), len(FORMATS)) + 1 for f in FORMATS}
        return random.choices(FORMATS, [(age[f] * weights.get(f, 1.0)) ** 2 for f in FORMATS])[0]
    order = {f: (recent.index(f) if f in recent else 999) for f in FORMATS}
    best = max(order.values())
    return random.choice([f for f, i in order.items() if i == best])


class IdeaGenerator:
    def __init__(self, llm: LLM, db, max_attempts: int = 4):
        self.llm, self.db, self.max_attempts = llm, db, max_attempts
        self._debut = None  # the family member whose debut episode is being written

    def _rested(self, n: int = 6) -> list[str]:
        """Main characters missing from the last n videos (everyone gets screen time)."""
        seen: set[str] = set()
        for row in self.db.q("SELECT story_json FROM jobs WHERE story_json IS NOT NULL ORDER BY id DESC LIMIT ?",
                             (n,)):
            try:
                seen |= {c["id"] for c in json.loads(row["story_json"]).get("characters", [])}
            except (ValueError, TypeError, KeyError):
                pass
        return [f'{c.name} ("{c.id}")' for c in cast.main_cast(self.db) if c.id not in seen]

    def _bible(self) -> str:
        return cast.bible_text(self.db, self._rested())

    def _debut_premise(self) -> str:
        """Every DEBUT_EVERY_DAYS a new family member gets their own entrance episode."""
        from zoneinfo import ZoneInfo
        today = dt.datetime.now(ZoneInfo(get_settings().timezone)).date()
        member = cast.next_debut(self.db, today)
        if member is None:
            return ""
        self._debut = member
        g = "male" if member.edge == cast.MALE else "female"
        return (f"USE THIS PREMISE - A NEW CHARACTER JOINS THE SHOW, so this is a special debut episode: "
                f"{member.debut}.\nThe new character MUST be in it with a big entrance and lines of their own: "
                f'id "{member.id}", name "{member.name}", {g}, voice "{member.edge}", look: {member.look}. '
                f"Personality: {member.personality}. Catchphrase (once): \"{member.catchphrase}\". "
                f"Relations: {member.relations}. Viewers must love them by the end and want to see them again. "
                "Add 1-2 of the main cast who react to them.")

    def _library_text(self) -> str:
        chars = self.db.characters()[:10]
        if not chars:
            return "(none yet)"
        return "\n".join(
            json.dumps({"id": c["id"], "name": c["name_ar"], "voice": c["voice"], "look": c["look"]},
                       ensure_ascii=False)
            for c in chars
        )

    def best_premise(self, fmt: str, recent: list[str], series: bool) -> str:
        """Brainstorm 10 premises, let a harsh 'editor' pass score them, keep the winner (+ its fix).
        Returns prompt text for the writer, or "" when the LLM is not available."""
        kind = ("A 3-part mini-series, our BIG show: drama, action and (cartoon, no-hitting) fights with huge "
                "stakes for tiny vegetables, and killer cliffhangers after parts 1 and 2. Genre for this series: "
                + random.choice(SERIES_GENRES) if series else "One standalone 30-60 second video.")
        loved = self.db.get("what_worked") or []
        if loved:  # from the 2-weekly views review
            kind += (" Our audience LOVED these recent videos (learn what made them work, never copy them): "
                     + "; ".join(loved[:5]) + ".")
        occasion = ""
        if not series:  # an Egyptian occasion beats the daily trend
            from zoneinfo import ZoneInfo
            from .occasions import premise_for
            occasion = premise_for(self.db, ZoneInfo(get_settings().timezone))
            if occasion:
                return occasion
            debut = self._debut_premise()
            if debut:
                return debut
        if not series:  # standalones ride today's trend when there is a safe one
            from .trends import todays_trend
            tr = todays_trend(self.llm, self.db)
            if tr:
                kind += (f" TODAY'S TREND in Egypt: {tr['topic_en']} (people are searching '{tr['trend']}'). "
                         f"Possible angle: {tr['angle']}. At least 5 of the 10 premises must be about this "
                         "trend (never about a real person). The video posts today, so it must feel like today.")
        try:
            raw = self.llm.text(BRAINSTORM.format(fmt=fmt, kind=kind, season=season_hint(),
                                                  bible=self._bible(), recent="; ".join(recent[:30]) or "none"),
                                json_mode=True, temperature=1.1)
            ideas = [i for i in extract_json(raw).get("ideas", []) if isinstance(i, dict) and i.get("premise")]
            if not ideas:
                return ""
            listing = "\n".join(f"{k}. {json.dumps(i, ensure_ascii=False)}" for k, i in enumerate(ideas))
            scores = extract_json(self.llm.text(JUDGE.format(ideas=listing), json_mode=True,
                                                temperature=0.3)).get("scores", [])
            keys = ("hook", "relatable", "twist", "shareable", "comments", "kids", "cliffhanger")

            def total(sc: dict) -> float:
                return sum(float(sc.get(k) or 0) for k in keys) + float(sc.get("hook") or 0)  # hook counts twice
            ranked = sorted((sc for sc in scores if isinstance(sc, dict) and 0 <= int(sc.get("i", -1)) < len(ideas)),
                            key=total, reverse=True)
            if not ranked:
                return ""
            win = ranked[0]
            idea = ideas[int(win["i"])]
            log.info("viral pick (%.0f pts): %s", total(win), idea["premise"][:120])
            return ("USE THIS PREMISE (it won a viral-potential contest against 9 others):\n"
                    + json.dumps(idea, ensure_ascii=False)
                    + f"\nEditor's note to make it even more viral: {win.get('fix', '')}\n"
                    +"Keep its hook, twist and comment bait. Topical context: " + season_hint())
        except LLMUnavailable:
            return ""
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as e:
            log.warning("viral brainstorm skipped: %s", str(e)[:200])
            return ""

    def _prompt(self, fmt: str, recent: list[str], task: str, premise: str = "") -> str:
        return PROMPT.format(
            fmt=fmt, premise=premise, min_s=4, max_s=7, min_w=MIN_WORDS + 6, max_w=MAX_WORDS - 15,
            bible=self._bible() + "\n\nOther characters already seen on the channel:\n" + self._library_text(),
            recent="\n".join(f"- {t}" for t in recent) or "(none yet)",
            answer=task.replace("{fmt}", fmt).replace("{{", "{").replace("}}", "}"),
        )

    def generate_series(self) -> tuple[str, list[Story]] | None:
        """A 3-part mini-series in one LLM call (so the parts stay consistent). None = no LLM."""
        recent = self.db.recent_titles(50)
        fmt = pick_format(self.db.recent_formats(len(FORMATS)), self.db.get("format_weights"))
        prompt = self._prompt(fmt, recent, SERIES_TASK, self.best_premise(fmt, recent, True))
        feedback = ""
        for attempt in range(1, self.max_attempts + 1):
            try:
                raw = self.llm.text(prompt + feedback, json_mode=True, temperature=1.0)
                data = extract_json(raw)
                parts = data.get("parts") if isinstance(data, dict) else None
                if not isinstance(parts, list) or len(parts) != 3:
                    raise ValueError("need exactly 3 parts in 'parts'")
                stories = []
                for k, part in enumerate(parts, 1):
                    try:
                        stories.append(self._apply_library(Story.model_validate(part)))
                    except ValidationError as e:
                        raise ValueError(f"part {k}: {e}") from e
                stories[0] = sharpen(self.llm, stories[0], loop=False)
                stories = proofread(self.llm, stories)
                title = str(data.get("series_title") or stories[0].title)[:80]
                return title, stories
            except LLMUnavailable as e:
                log.warning("no LLM for a series: %s", e)
                return None
            except (ValidationError, ValueError, json.JSONDecodeError) as e:
                log.warning("series attempt %d invalid: %s", attempt, str(e)[:400])
                feedback = f"\n\nYour previous answer was rejected: {str(e)[:400]}\nFix it and answer again."
        return None

    def generate(self) -> Story:
        self._debut = None
        recent = self.db.recent_titles(50)
        fmt = pick_format(self.db.recent_formats(len(FORMATS)), self.db.get("format_weights"))
        prompt = self._prompt(fmt, recent, SINGLE_TASK, self.best_premise(fmt, recent, False))
        feedback = ""
        for attempt in range(1, self.max_attempts + 1):
            try:
                raw = self.llm.text(prompt + feedback, json_mode=True, temperature=1.0)
                story = Story.model_validate(extract_json(raw))
                if story.title in recent:
                    raise ValueError("title repeats a recent video")
                if self._debut is not None and self._debut.id not in {c.id for c in story.characters}:
                    raise ValueError(f"the new character {self._debut.id!r} must be in this episode")
                story = proofread(self.llm, [sharpen(self.llm, story, loop=True)])[0]
                if self._debut is not None:
                    from zoneinfo import ZoneInfo
                    cast.mark_joined(self.db, self._debut,
                                     dt.datetime.now(ZoneInfo(get_settings().timezone)).date())
                return self._apply_library(story)
            except LLMUnavailable as e:
                log.warning("no LLM for ideas: %s", e)
                break
            except (ValidationError, ValueError, json.JSONDecodeError) as e:
                log.warning("idea attempt %d invalid: %s", attempt, str(e)[:400])
                feedback = f"\n\nYour previous answer was rejected: {str(e)[:400]}\nFix it and answer again."
        log.warning("using offline skit bank")
        return self.offline(recent)

    def offline(self, recent: list[str]) -> Story:
        unused = [s for s in SKITS if s["title"] not in recent]
        skit = random.choice(unused or SKITS)
        return self._apply_library(Story.model_validate(skit))

    def _apply_library(self, story: Story) -> Story:
        """Cast members always get their bible name/gender/look; other reused characters keep their
        stored look/voice so the series stays consistent."""
        for c in story.characters:
            member = cast.BY_ID.get(c.id)
            known = self.db.character(c.id)
            if member:
                c.name, c.voice = member.name, member.edge
                c.look = known["look"] if known else member.look
            elif known:
                c.name, c.voice, c.look = known["name_ar"], known["voice"], known["look"]
            elif c.voice not in VOICES:
                c.voice = VOICES[1]
        return story
