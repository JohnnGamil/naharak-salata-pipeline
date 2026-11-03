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
    pitch: int = 0  # semitones on the Gemini voice (the little brother sounds like a kid)
    debut: str = ""  # arrivals only: the premise of the episode that brings them in


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

# The family grows over time (John, 2026-11-01: "the videos keep saying mama - where is mama? new
# characters with time, top quality"). One arrival every DEBUT_EVERY_DAYS, each in its own debut
# episode; from then on they are main cast. The parents come first: almost every video talks about
# them already. Each arrival has a voice nobody else uses.
ARRIVALS = [
    CastMember(
        "mama", "ماما قرنبيطة", FEMALE, "Sulafat",
        "the classic Egyptian mom of the kitchen: sees everything (انتوا فاكريني مش واخدة بالي؟), keeps "
        "the best food for the guests, compares everyone to the neighbours' kids, says she won't talk again "
        "and then talks for ten minutes, secretly the softest heart in the house",
        "أنا مش هتكلم تاني!",
        "a white cauliflower mom whose florets look like big hair rollers, a flowery house apron, "
        "a wooden spoon in her hand, sharp knowing eyes, one raised eyebrow, tiny arms and legs",
        "mom of tomato, potato and little zucchini; married to Baba Cabbage; obeys only Teita Garlic",
        debut="MAMA ARRIVES: the gang says 'ماما هتعرف' in every episode, and today Mama Cauliflower finally "
              "walks in, back early from her sister's, and finds the kitchen in total chaos. A big entrance "
              "(the door opens, everyone freezes), she uncovers every secret in seconds, and the twist: she "
              "knew all along",
    ),
    CastMember(
        "baba", "بابا كرنب", MALE, "Algenib",
        "the Egyptian dad: calm until the electricity bill comes (مين اللي سايب النور والع؟), falls asleep "
        "in front of the TV and wakes up saying he was watching, answers every request with 'ask your mom', "
        "tells the same story about his youth, makes dad jokes nobody laughs at",
        "اسألوا أمكوا!",
        "a big round green cabbage dad with a thick black mustache, reading glasses on his nose, a folded "
        "newspaper under his arm and the TV remote in his hand, tiny arms and legs",
        "married to Mama Cauliflower; the kids ask him first because he says yes without listening",
        debut="BABA'S BIG DAY: the electricity bill arrives and Baba Cabbage turns detective to find who left "
              "the AC and the lights on all month; every kid blames another; the twist is relatable (it was "
              "Baba himself, asleep in front of the TV)",
    ),
    CastMember(
        "zucchini", "كوسة الصغير", MALE, "Fenrir",
        "the youngest kid: mischievous, asks 'ليه؟' a hundred times, repeats every secret in front of Mama "
        "at the worst moment, cries fake tears to get what he wants",
        "أنا هقول لماما!",
        "a tiny light-green baby zucchini kid with a backwards cap, a gap-toothed grin, big curious eyes, "
        "a school bag bigger than himself, tiny arms and legs",
        "little brother of tomato and potato; Mama's favourite (everybody knows it)",
        pitch=4,
        debut="THE LITTLE BROTHER: the gang hides a big secret from Mama (the broken plate, the hidden "
              "chocolate) and little Zucchini overhears; all episode they bribe him to keep quiet, and at "
              "the last second he says the one sentence that exposes everything",
    ),
    CastMember(
        "okra", "طنط بامية", FEMALE, "Laomedeia",
        "the nosy neighbour from the shelf upstairs: knows the news of the whole building before it "
        "happens, visits for five minutes and stays three hours, spreads gossip that turns out wrong",
        "سمعتوا اللي حصل؟",
        "a slim green okra auntie with big golden earrings, a colourful headscarf, one hand always on her "
        "hip, curious narrowed eyes, tiny arms and legs",
        "Mama's friend and rival; Teita does not trust her news",
        debut="THE NEIGHBOUR: Tant Okra visits with 'breaking news' about the fridge (a rumour that someone "
              "is getting married, or that the vegetables get cooked tomorrow); the gossip spreads shelf to "
              "shelf and grows at every step; the twist shows what she really heard",
    ),
    CastMember(
        "avocado", "أفوكادو ابن خالتك", MALE, "Sadachbia",
        "the show-off cousin back from abroad: mixes English words into every sentence (so basically يعني), "
        "brags about life outside, looks down on Egyptian food until Teita's cooking wins him over",
        "إحنا برا بنعمل كده!",
        "a shiny dark-green avocado cousin with stylish sunglasses, a tiny designer jacket and a phone "
        "always in his hand, a confident smirk, tiny arms and legs",
        "the cousin every Egyptian family has; Mama keeps saying 'شوفوا ابن خالتكوا' to the kids",
        debut="THE COUSIN FROM ABROAD: the avocado cousin visits and compares everything to 'outside'; the "
              "kids plan to make him admit Egyptian food is the best; the twist makes him the biggest fan "
              "of Mama's mahshi",
    ),
    CastMember(
        "guava", "جدو جوافة", MALE, "Alnilam",
        "the grandpa: hard of hearing, so he gets every sentence wrong (the running joke), starts every "
        "story with 'في أيامنا...', secretly helps the kids against Mama",
        "في أيامنا كان الكيلو بقرش!",
        "an old round yellow-green guava grandpa with a white mustache, a flat cap, a wooden walking cane "
        "and thick glasses, gentle smile, tiny arms and legs",
        "Teita Garlic's husband; the two argue like a comedy duo",
        debut="GRANDPA VISITS: Gedo Guava comes to stay and hears every sentence wrong; one tiny "
              "misunderstanding (a trip heard as a wedding) snowballs into whole-family chaos; the twist "
              "is warm and funny",
    ),
]
DEBUT_EVERY_DAYS = 9

GUEST_VOICES = {MALE: ["Charon", "Achird", "Orus", "Iapetus", "Rasalgethi"],
                FEMALE: ["Aoede", "Zephyr", "Despina", "Callirrhoe", "Autonoe"]}

BY_ID = {c.id: c for c in MAIN + GUESTS + ARRIVALS}


def joined(db) -> list[CastMember]:
    """Arrivals whose debut episode was already written: main cast from then on."""
    have = (db.get("cast_joined") or {}) if db is not None else {}
    return [a for a in ARRIVALS if a.id in have]


def next_debut(db, today: dt.date) -> CastMember | None:
    """The next family member to bring in, once the last debut is DEBUT_EVERY_DAYS old (or none yet)."""
    have = db.get("cast_joined") or {}
    waiting = [a for a in ARRIVALS if a.id not in have]
    if not waiting:
        return None
    last = max((dt.date.fromisoformat(d) for d in have.values()), default=None)
    if last and (today - last).days < DEBUT_EVERY_DAYS:
        return None
    return waiting[0]


def mark_joined(db, member: CastMember, today: dt.date) -> None:
    have = db.get("cast_joined") or {}
    have[member.id] = today.isoformat()
    db.put("cast_joined", have)


def main_cast(db=None) -> list[CastMember]:
    return MAIN + joined(db)


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


def bible_text(db=None, rested: list[str] | None = None, tired: list[str] | None = None) -> str:
    lines = ["MAIN CAST (reuse them; keep their relationships; the personality is their starting point, "
             "surprise us with a new side of them):"]
    for c in main_cast(db):
        g = "male" if c.edge == MALE else "female"
        lines.append(f'- id "{c.id}", name "{c.name}", {g}, voice "{c.edge}": {c.personality}. '
                     f'Catchphrase: "{c.catchphrase}". {c.relations}.')
    if rested:
        lines.append("SCREEN TIME: these main characters were NOT in the last videos; give one of them a "
                     "big role this time: " + ", ".join(rested))
    if tired:
        lines.append("TIRED (said their catchphrase in the last videos - not this time, and no repeat of "
                     "their usual habit as the joke): " + ", ".join(tired))
    lines.append("POSSIBLE GUESTS right now (at most one per episode, optional):")
    for g in guests_now():
        gg = "male" if g.edge == MALE else "female"
        lines.append(f'- id "{g.id}", name "{g.name}", {gg}, voice "{g.edge}": {g.personality}. '
                     f'Catchphrase: "{g.catchphrase}". Look: {g.look}.')
    return "\n".join(lines)
