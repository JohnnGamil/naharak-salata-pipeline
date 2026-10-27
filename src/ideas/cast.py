"""The show's "bible": a recurring main cast with fixed personality, catchphrase, relationships,
look and voice, plus seasonal / random guests. The story prompt is built from this so every
episode feels like the same series (agreed with John 2026-10-27).

voice = the Gemini prebuilt voice name (shared by all Gemini TTS models); edge = gender for the
edge-tts fallback. Guests get a free voice of their gender from GUEST_VOICES.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

MALE, FEMALE = "ar-EG-ShakirNeural", "ar-EG-SalmaNeural"


@dataclass(frozen=True)
class CastMember:
    id: str
    name: str
    edge: str
    voice: str
    personality: str
    catchphrase: str
    look: str
    relations: str = ""
    seasons: tuple[str, ...] = field(default_factory=tuple)  # guests only: when they may appear


MAIN = [
    CastMember(
        "lemon", "ليمونة", FEMALE, "Leda",
        "drama queen: every small thing is a catastrophe, cries and exaggerates, but has a big heart",
        "يا لهوي على حظي!",
        "a bright yellow lemon with big glossy brown eyes, long eyelashes, a small green leaf on top, "
        "tiny arms and legs, shiny textured peel",
        "best friend of cucumber; tomato is secretly in love with her",
    ),
    CastMember(
        "tomato", "طمطم", MALE, "Puck",
        "kind, naive and goofy: believes everything he hears, always gets the wrong idea, very loyal",
        "بجد؟! طب إزاي؟",
        "a round shiny red tomato with a green star-shaped stem on top, big round green eyes, "
        "rosy cheeks, tiny arms and legs, cheerful",
        "loves lemon; potato's best friend and favourite victim of his pranks",
    ),
    CastMember(
        "cucumber", "خيارة", FEMALE, "Kore",
        "know-it-all: gives advice and 'scientific facts' about everything (often wrong), confident",
        "اسمعي مني أنا، أنا فاهمة كل حاجة",
        "a tall slim green cucumber with small bumps, a little white flower on her head, "
        "big brown eyes, confident smile, tiny arms and legs",
        "lemon's best friend; argues with potato all the time",
    ),
    CastMember(
        "potato", "بطاطس", MALE, "Umbriel",
        "lazy and sarcastic: only wants to sleep and eat, makes dry jokes, secretly the smartest",
        "سيبوني أنام يا جماعة",
        "a chubby brown potato with small dark spots, sleepy half-closed eyes, thick eyebrows, "
        "tiny arms and legs",
        "tomato's best friend, cucumber's rival",
    ),
    CastMember(
        "garlic", "تيتا ثومة", FEMALE, "Gacrux",
        "the wise old grandma of the kitchen: solves every problem with old Egyptian proverbs and "
        "home remedies, loving but strict",
        "اسمعوا كلام تيتا يا ولاد",
        "an old white garlic bulb grandma with small round glasses, a tiny headscarf, gentle wrinkled "
        "smile, kind eyes, tiny arms and legs",
        "everybody's grandma; the only one potato listens to",
    ),
    CastMember(
        "onion", "بصلة", MALE, "Enceladus",
        "very sensitive: cries at everything and makes everyone around him cry too (running joke)",
        "أنا مش بعيط.. ده البصل!",
        "a round golden-brown onion with a sprout on top, big watery blue eyes, trembling smile, "
        "tiny arms and legs",
        "tries to be tough to impress cucumber, always fails",
    ),
]

# guests: a new face now and then; "any" = all year
GUESTS = [
    CastMember("mango", "مانجا", FEMALE, "", "show-off summer celebrity, thinks she is the queen of fruits",
               "أنا المانجا يا حبيبي!", "a golden-orange mango with a tiny crown, sunglasses on her head",
               seasons=("summer",)),
    CastMember("watermelon", "بطيخة", MALE, "", "big, loud and generous, the life of every summer party",
               "اللي ياكل مني يدعيلي!", "a big round striped green watermelon with a huge smile",
               seasons=("summer",)),
    CastMember("date", "بلحة", MALE, "", "calm Ramadan guest, kind and wise, loves iftar time",
               "كل سنة وانتوا طيبين!", "a glossy brown date with a small white cap and gentle eyes",
               seasons=("ramadan",)),
    CastMember("strawberry", "فراولاية", FEMALE, "", "the new girl in the fridge: shy, sweet, everybody wants to be her friend",
               "أنا جديدة هنا..", "a small red strawberry with a green leafy cap and big shy eyes",
               seasons=("any",)),
    CastMember("eggplant", "بتنجان", MALE, "", "dramatic poet who speaks like an old movie star",
               "يا سلام على الحياة!", "a glossy purple eggplant with a curly mustache and a little hat",
               seasons=("any",)),
    CastMember("carrot", "جزرة", MALE, "", "sporty and hyper, always training and challenging others",
               "يلا بينا نتمرن!", "an orange carrot with a green leafy top tied like a headband, sporty",
               seasons=("any",)),
    CastMember("pepper", "فلفلاية", FEMALE, "", "hot-tempered and spicy, gets angry fast but forgives fast",
               "متخلنيش أتعصب!", "a shiny red chili pepper girl with fiery eyebrows",
               seasons=("any",)),
    CastMember("banana", "موزة", MALE, "", "clumsy, always slipping and falling, laughs at himself",
               "اتزحلقت تاني!", "a curved yellow banana with a few brown spots and a goofy grin",
               seasons=("any",)),
]

GUEST_VOICES = {MALE: ["Charon", "Fenrir", "Algenib", "Achird", "Orus"],
                FEMALE: ["Aoede", "Zephyr", "Sulafat", "Despina", "Callirrhoe"]}

BY_ID = {c.id: c for c in MAIN + GUESTS}


def season(today: dt.date | None = None) -> str:
    """Rough season for picking guests (Ramadan moves ~11 days a year; 2027: about Feb 8 - Mar 9)."""
    today = today or dt.date.today()
    ramadan = {2026: (dt.date(2026, 2, 18), dt.date(2026, 3, 19)),
               2027: (dt.date(2027, 2, 8), dt.date(2027, 3, 9)),
               2028: (dt.date(2028, 1, 28), dt.date(2028, 2, 26))}.get(today.year)
    if ramadan and ramadan[0] <= today <= ramadan[1]:
        return "ramadan"
    return "summer" if today.month in (6, 7, 8, 9) else "any"


def guests_now(today: dt.date | None = None) -> list[CastMember]:
    s = season(today)
    return [g for g in GUESTS if "any" in g.seasons or s in g.seasons]


def gemini_voice(char_id: str, edge_voice: str, taken: set[str]) -> str:
    """Voice for a character: the cast's own, else a free guest voice of the right gender."""
    member = BY_ID.get(char_id)
    if member and member.voice:
        return member.voice
    pool = GUEST_VOICES.get(edge_voice, GUEST_VOICES[MALE])
    # stable per id so a returning guest keeps the same voice
    free = [v for v in pool if v not in taken] or pool
    return free[sum(map(ord, char_id)) % len(free)]


def bible_text() -> str:
    lines = ["MAIN CAST (reuse them; keep personality, catchphrase and relationships):"]
    for c in MAIN:
        g = "male" if c.edge == MALE else "female"
        lines.append(f'- id "{c.id}", name "{c.name}", {g}, voice "{c.edge}": {c.personality}. '
                     f'Catchphrase: "{c.catchphrase}". {c.relations}.')
    lines.append("POSSIBLE GUESTS right now (at most one per episode, optional):")
    for g in guests_now():
        gg = "male" if g.edge == MALE else "female"
        lines.append(f'- id "{g.id}", name "{g.name}", {gg}, voice "{g.edge}": {g.personality}. '
                     f'Catchphrase: "{g.catchphrase}". Look: {g.look}.')
    return "\n".join(lines)
