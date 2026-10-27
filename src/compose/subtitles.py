"""RTL-safe Arabic .ass subtitles rendered by libass.

Rules (learned the hard way on Arabic + libass):
- never put override tags in the MIDDLE of an Arabic line - libass splits bidi runs and
  scrambles word order. Tags only at the very start of a dialogue line.
- one caption on screen at a time (no stacking, no speaker label): each chunk ends where the next begins.
- no punctuation in captions (neutral chars at line ends get mirrored to the wrong side).
"""
from __future__ import annotations

import re
from pathlib import Path

from ..config import CAPTION_FONT

PUNCT_RE = re.compile(r"[،؛؟۔.,!?;:\"'()\[\]«»…\-–—]+")
TAG_RE = re.compile(r"[{}\\]")
MAX_WORDS, MAX_CHARS = 3, 15

HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,{font},108,&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,0,0,0,0,100,100,0,0,1,7,3,2,60,60,400,178
Style: Hook,{font},78,&H0000E5FF,&H0000E5FF,&H00000000,&HA0000000,0,0,0,0,100,100,0,0,3,8,0,8,70,70,230,178

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def clean(text: str) -> str:
    text = TAG_RE.sub("", text)
    text = PUNCT_RE.sub(" ", text)
    return " ".join(text.split())


def ts(t: float) -> str:
    t = max(0.0, t)
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def words_for_line(line: str, words: list[dict], duration: float) -> list[dict]:
    """Use TTS word boundaries; if missing, spread words by character length."""
    good = [w for w in words if clean(w["text"])]
    if good:
        return [{"text": clean(w["text"]), "start": w["start"], "end": w["end"]} for w in good]
    toks = clean(line).split()
    total = sum(len(t) for t in toks) or 1
    out, t = [], 0.05
    speak = max(0.5, duration - 0.3)
    for tok in toks:
        d = speak * len(tok) / total
        out.append({"text": tok, "start": t, "end": t + d})
        t += d
    return out


def chunk_words(words: list[dict]) -> list[dict]:
    chunks, cur = [], []
    for w in words:
        trial = " ".join(x["text"] for x in cur + [w])
        if cur and (len(cur) >= MAX_WORDS or len(trial) > MAX_CHARS):
            chunks.append(cur)
            cur = []
        cur.append(w)
    if cur:
        chunks.append(cur)
    return [{"text": " ".join(x["text"] for x in c), "start": c[0]["start"], "end": c[-1]["end"]}
            for c in chunks]


def build_events(scenes: list[dict]) -> list[dict]:
    """scenes: [{offset, duration, line, words, speaker}] -> non-overlapping caption events."""
    events = []
    for sc in scenes:
        chunks = chunk_words(words_for_line(sc["line"], sc["words"], sc["duration"]))
        scene_end = sc["offset"] + sc["duration"]
        for i, c in enumerate(chunks):
            start = sc["offset"] + c["start"]
            nxt = sc["offset"] + chunks[i + 1]["start"] if i + 1 < len(chunks) else scene_end - 0.05
            end = min(max(sc["offset"] + c["end"] + 0.25, start + 0.35), nxt, scene_end - 0.05)
            events.append({"start": start, "end": end, "text": c["text"], "speaker": sc.get("speaker")})
    # guarantee no overlap after rounding
    for a, b in zip(events, events[1:]):
        if a["end"] > b["start"]:
            a["end"] = b["start"]
    return [e for e in events if e["end"] - e["start"] >= 0.05]


def write_ass(scenes: list[dict], out: Path, hook: str | None = None, hook_s: float = 2.2,
              font: str = CAPTION_FONT) -> Path:
    lines = [HEADER.format(font=font)]
    if hook:
        lines.append(
            f"Dialogue: 1,{ts(0)},{ts(hook_s)},Hook,,0,0,0,,{{\\fad(150,250)}}{clean(hook)}\n"
        )
    for e in build_events(scenes):
        # animation tags only at the START of the line (RTL-safe)
        lines.append(
            f"Dialogue: 2,{ts(e['start'])},{ts(e['end'])},Caption,,0,0,0,,"
            # bounce in (70% -> 112% -> 100%): every new caption "pops", like hand-edited TikToks
            f"{{\\fad(40,0)\\fscx70\\fscy70\\t(0,90,\\fscx112\\fscy112)\\t(90,170,\\fscx100\\fscy100)}}"
            f"{e['text']}\n"
        )
    out.write_text("".join(lines), encoding="utf-8-sig")
    return out
