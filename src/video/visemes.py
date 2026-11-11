"""Mouth shapes from the words being said (John, 2026-11-11: "the mouth being right is the most important
thing").

The mouth used to only open wider on loud sound, always the same oval. Now Whisper's word times (the same
model that times the captions) tell us which letter is said when, and every Arabic letter has a mouth shape:
lips shut on م/ب, the lower lip under the teeth on ف, round on و, wide on ي, open on ا/ع/ح/ه. Loudness still
decides how far the mouth opens, and silence closes it.
"""
from __future__ import annotations

import logging

import numpy as np

log = logging.getLogger(__name__)

# rest, lips shut, small open, open, wide open, smile-wide, round, teeth on lip
SHAPES = ("X", "M", "B", "A", "A2", "E", "O", "F")

_LETTER = {}
for _c in "مب":
    _LETTER[_c] = "M"
_LETTER["ف"] = "F"
_LETTER["و"] = "O"
for _c in "يى":
    _LETTER[_c] = "E"
for _c in "اأإآعحهة":
    _LETTER[_c] = "A"
LONG = {"ا", "و", "ي", "ى", "آ"}  # vowels last longer than the consonants around them


def letter_shape(ch: str) -> str:
    return _LETTER.get(ch, "B")


def timeline(words: list[dict]) -> list[tuple[float, float, str]]:
    """[(start, end, shape)] for every letter, letters spread inside their word (vowels weigh 1.6)."""
    out = []
    for w in words:
        letters = [c for c in str(w.get("text", "")) if "ء" <= c <= "ي"]
        t0, t1 = float(w["start"]), float(w["end"])
        if not letters or t1 <= t0:
            continue
        wts = np.array([1.6 if c in LONG else 1.0 for c in letters])
        edges = t0 + (t1 - t0) * np.concatenate([[0], np.cumsum(wts) / wts.sum()])
        for k, c in enumerate(letters):
            out.append((float(edges[k]), float(edges[k + 1]), letter_shape(c)))
    return out


def frame_shapes(tl: list[tuple[float, float, str]], rms: np.ndarray, fps: int) -> list[str]:
    """One shape per frame: the letter said then, sized by loudness, shut in silence, no 1-frame flicker."""
    n = len(rms)
    out = []
    j = 0
    for i in range(n):
        t = (i + 0.5) / fps
        while j < len(tl) and tl[j][1] <= t:
            j += 1
        shape = tl[j][2] if j < len(tl) and tl[j][0] <= t else None
        lv = float(rms[i])
        if lv < 0.08:
            s = "X"
        elif shape is None:  # sound Whisper put no word on (a laugh, a breath): open by loudness
            s = "A2" if lv > 0.75 else "A" if lv > 0.4 else "B"
        elif shape == "A":
            s = "A2" if lv > 0.75 else "A" if lv > 0.3 else "B"
        elif shape == "B" and lv > 0.85:
            s = "A"
        else:
            s = shape
        out.append(s)
    # lips stay shut only briefly while there is sound (a long م on a stretched word looked like a mute
    # mouth): after 3 frames it opens a little
    i = 0
    while i < n:
        k = i
        while k < n and out[k] == out[i]:
            k += 1
        if out[i] in ("M", "F"):
            for m in range(i + 3, k):
                if float(rms[m]) > 0.2:
                    out[m] = "B"
        i = k
    # a shape shown for a single frame flickers: it takes its neighbour's shape, except a lip closure
    # (م/ب) which is short in real speech too and is what makes it read as talking
    for i in range(1, n - 1):
        if out[i] != out[i - 1] and out[i] != out[i + 1] and out[i] != "M":
            out[i] = out[i - 1]
    return out


def words_for_scene(wav, line: str, duration: float, settings) -> list[dict] | None:
    """Script words with the times Whisper heard them (None when it could not listen)."""
    try:
        from ..compose.align import heard_words, match
        heard = heard_words(wav, settings.whisper_model, settings.models_dir)
        return match(line, heard, duration) or heard or None
    except Exception as e:  # noqa: BLE001 - the loudness mouth still works without it
        log.warning("mouth shapes skipped: %s", e)
        return None
