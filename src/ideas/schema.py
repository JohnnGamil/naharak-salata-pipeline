"""Story JSON schema (validated with pydantic)."""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

VOICES = ("ar-EG-SalmaNeural", "ar-EG-ShakirNeural")
FORMATS = ("drama", "comedy", "advice", "rivalry")
EMOTIONS = ("neutral", "happy", "angry", "sad", "excited", "scared", "sarcastic", "surprised")

ARABIC_RE = re.compile(r"[؀-ۿ]")
# Egyptian TTS speaks roughly 2.3 words/second
WORDS_PER_SEC = 2.3
MIN_WORDS, MAX_WORDS = int(28 * WORDS_PER_SEC), int(62 * WORDS_PER_SEC)
# unmistakable Modern Standard Arabic words: more than one means the model drifted out of Egyptian
FUSHA = {"ماذا", "لماذا", "الآن", "سوف", "لن", "ليس", "ليست", "أريد", "جداً", "جدا", "لكن", "لكنني",
         "هذا", "هذه", "ذلك", "تلك", "الذي", "التي", "أين", "متى", "كيف", "نعم"}

# words TikTok's moderation (and parents) react to on a kids-friendly channel: violence, death,
# weapons, drugs/alcohol/smoking, adult topics. Matched on the word stem after stripping و/ف/ب/ل/ال
# and the Egyptian future ه. A hit rejects the story and the model rewrites it (e.g. "المتهم").
BANNED_STEMS = (
    "قتل", "قاتل", "مقتول", "قتيل", "جريمه", "مجرم", "جرايم", "دبح", "ذبح", "دماء", "دم", "دمه", "دمي", "موت", "ميت", "ميته",
    "يموت", "اموت", "تموت", "مات", "ماتت", "جثه", "جثة", "انتحار", "انتحر", "سلاح", "مسدس", "رصاص",
    "رصاصه", "قنبله", "قنبلة", "انفجار", "ارهاب", "خطف", "مخطوف", "عصابه", "عصابة", "مخدرات", "مخدر",
    "حشيش", "بانجو", "سجاره", "سجارة", "سجاير", "خمره", "خمرة", "بيره", "بيرة", "سكران", "جنس",
    "عريان", "بوسه", "بوسة", "دعاره", "قمار", "رهان", "تعذيب", "ضرب", "يضرب", "هضربك", "اضربك", "اضرب", "نضرب", "تضرب", "هضرب", "سلاحي",
    "اسلحه", "اسلحة", "سكين",
)
BANNED_EN = ("kill", "murder", "blood", "dead", "death", "gun", "weapon", "knife fight", "drug", "beer",
             "wine", "cigarette", "smoking", "sexy", "kiss")
# harmless words that only look like a banned stem + ending (السكينة = calm, ضربة حظ = a lucky shot)
ALLOWED = {"سكينه", "ضربه", "دمك", "دمها"}
_PREFIXES = ("وال", "بال", "فال", "كال", "لل", "ال", "و", "ف", "ب", "ل", "ه", "ح")


def banned_words(text: str) -> list[str]:
    hits = set()
    # letters only: the wider Arabic block also holds ؟ ، ؛ which glued onto words ("المجرم؟")
    text_ = re.sub(r"[ً-ْـ]", "", text)  # drop tashkeel and tatweel
    for w in re.findall(r"[ء-ي]+", text_):
        w = w.replace("ة", "ه").replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
        cands = {w}
        for p in ("", *_PREFIXES):
            if w.startswith(p) and w[len(p):] in ALLOWED:
                cands = set()
        if not cands:
            continue
        for p in _PREFIXES:
            if w.startswith(p) and len(w) - len(p) >= 2:
                cands.add(w[len(p):])
        # Egyptian endings: هقتلك / يموتوا / سلاحي / اضربوا / سلاحهم
        for suf in ("وها", "وهم", "وني", "كم", "هم", "ها", "نا", "وا", "ني", "ك", "و", "ه", "ي"):
            cands |= {c[:-len(suf)] for c in list(cands) if c.endswith(suf) and len(c) - len(suf) >= 3}
        stems = {b.replace("ة", "ه") for b in BANNED_STEMS}
        if cands & stems:
            hits.add(w)
    low = text.lower()
    hits |= {e for e in BANNED_EN if re.search(r"\b" + re.escape(e), low)}
    return sorted(hits)


def clean_tag(t: str) -> str:
    """A hashtag that platforms really link: letters (Arabic/Latin), digits and _ only - a space,
    dash, dot, apostrophe or emoji cuts the link at that point. Not digits-only, at most 30 chars."""
    t = re.sub(r"[\s\-.]+", "_", str(t).strip().lstrip("#"))
    t = re.sub(r"[ً-ْـ]", "", t)  # tashkeel/tatweel
    t = re.sub(r"[^0-9A-Za-z_ء-ي٠-٩]", "", t).strip("_")
    t = re.sub(r"_+", "_", t)
    return "" if not t or t.isdigit() else t[:30]


class Character(BaseModel):
    id: str = Field(min_length=2, max_length=24)
    name: str = Field(min_length=1, max_length=30)
    voice: Literal["ar-EG-SalmaNeural", "ar-EG-ShakirNeural"]
    look: str = Field(min_length=10, max_length=400)

    @field_validator("id")
    @classmethod
    def slug(cls, v: str) -> str:
        v = re.sub(r"[^a-z0-9_]", "_", v.strip().lower()).strip("_")
        if not v:
            raise ValueError("empty id")
        return v


class Scene(BaseModel):
    character_id: str
    line_ar: str = Field(min_length=2, max_length=260)
    visual_prompt_en: str = Field(min_length=5, max_length=500)
    emotion: str = "neutral"

    @field_validator("character_id")
    @classmethod
    def slug(cls, v: str) -> str:
        return re.sub(r"[^a-z0-9_]", "_", v.strip().lower()).strip("_")

    @field_validator("emotion")
    @classmethod
    def emo(cls, v: str) -> str:
        v = v.strip().lower()
        return v if v in EMOTIONS else "neutral"

    @field_validator("line_ar")
    @classmethod
    def arabic(cls, v: str) -> str:
        v = " ".join(v.split())
        if not ARABIC_RE.search(v):
            raise ValueError("line_ar must be Arabic")
        return v


class Story(BaseModel):
    title: str = Field(min_length=2, max_length=120)
    format: str
    characters: list[Character] = Field(min_length=1, max_length=4)
    scenes: list[Scene] = Field(min_length=3, max_length=8)
    caption_ar: str = Field(min_length=2, max_length=1500)
    hashtags: list[str] = Field(default_factory=list, max_length=12)
    # series part 1 only: {"question_ar": "...", "options": ["...", "..."]} - viewers pick part 2 (vote.py)
    vote: dict | None = None

    @field_validator("vote")
    @classmethod
    def vote_ok(cls, v):
        if v is None:
            return None
        opts = [str(o).strip() for o in (v.get("options") or [])][:2]
        q = str(v.get("question_ar") or "").strip()
        if len(opts) != 2 or not all(opts) or not q:
            return None  # a broken vote is dropped, never a reason to reject the story
        return {"question_ar": q, "options": opts}

    @field_validator("format")
    @classmethod
    def fmt(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in FORMATS:
            raise ValueError(f"format must be one of {FORMATS}")
        return v

    @field_validator("hashtags")
    @classmethod
    def tags(cls, v: list[str]) -> list[str]:
        out = []
        for t in v:
            t = clean_tag(t)
            if t and t not in out:
                out.append(t)
        return out

    @model_validator(mode="after")
    def check(self) -> "Story":
        ids = {c.id for c in self.characters}
        if len(ids) != len(self.characters):
            raise ValueError("duplicate character ids")
        for s in self.scenes:
            if s.character_id not in ids:
                raise ValueError(f"scene uses unknown character {s.character_id!r}")
        fusha = sorted({w for sc in self.scenes for w in re.findall(r"[ء-ي]+", sc.line_ar) if w in FUSHA})
        if len(fusha) > 1:
            raise ValueError(f"lines use فصحى words {fusha}; rewrite them in Egyptian colloquial "
                             f"(e.g. إيه/ليه/دلوقتي/مش/بس/ده/دي/أوي)")
        bad = banned_words(" ".join([self.title, self.caption_ar, " ".join(self.hashtags)]
                                    + [sc.line_ar for sc in self.scenes]))
        if bad:
            raise ValueError(f"uses words TikTok flags on a kids channel {bad}; rewrite without any "
                             f"violence, death, weapons, drugs or adult words (say المتهم not القاتل)")
        words = self.word_count
        if not MIN_WORDS <= words <= MAX_WORDS:
            raise ValueError(
                f"script has {words} words; need {MIN_WORDS}-{MAX_WORDS} for a 30-60 s video"
            )
        return self

    @property
    def word_count(self) -> int:
        return sum(len(s.line_ar.split()) for s in self.scenes)

    def char(self, cid: str) -> Character:
        return next(c for c in self.characters if c.id == cid)

    @property
    def full_caption(self) -> str:
        tags = " ".join(f"#{t}" for t in self.hashtags)
        return f"{self.caption_ar}\n\n{tags}".strip()[:2000]
