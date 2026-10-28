"""Caption timings measured from the voice itself (John, 2026-10-28: "the words on screen come late").

The TTS gives no word times, so they used to be spread by letter count over the line, which drifts
whenever a character speeds up, pauses or stretches a word. Whisper listens to each scene's wav with
word timestamps; the script's words (correct spelling, what we show) are matched letter by letter to
what Whisper heard, and each script word takes the time of its matched letters. Words Whisper missed
are placed between their neighbours. Any failure keeps the old spread timings.
"""
from __future__ import annotations

import logging
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np

from ..qa.audio_check import _model, load_audio_16k, normalize_ar

log = logging.getLogger(__name__)


def heard_words(wav: str | Path, model_name: str, root: Path) -> list[dict]:
    segs, _ = _model(model_name, root).transcribe(load_audio_16k(Path(wav)), language="ar", beam_size=5,
                                                  vad_filter=False, word_timestamps=True,
                                                  condition_on_previous_text=False)
    return [{"text": w.word, "start": float(w.start), "end": float(w.end)} for s in segs for w in (s.words or [])]


def match(script: str, heard: list[dict], duration: float) -> list[dict] | None:
    """Script words with times taken from the heard words. None when too little matches."""
    words = script.split()
    if not words or not heard:
        return None
    # one time per heard letter, spread inside its word
    h_chars, h_times = [], []
    for w in heard:
        t = normalize_ar(w["text"]).replace(" ", "")
        for k, ch in enumerate(t):
            h_chars.append(ch)
            h_times.append(w["start"] + (w["end"] - w["start"]) * k / max(1, len(t)))
    h_times.append(heard[-1]["end"])
    s_chars, owner = [], []
    for i, w in enumerate(words):
        for ch in normalize_ar(w).replace(" ", ""):
            s_chars.append(ch)
            owner.append(i)
    if not s_chars or not h_chars:
        return None
    sm = SequenceMatcher(None, s_chars, h_chars, autojunk=False)
    times = [None] * len(s_chars)
    hit = 0
    for a, b, n in sm.get_matching_blocks():
        for k in range(n):
            times[a + k] = h_times[b + k]
        hit += n
    if hit < 0.5 * len(s_chars):
        return None
    starts = [None] * len(words)
    for ci, t in enumerate(times):
        if t is not None and starts[owner[ci]] is None:
            starts[owner[ci]] = t
    # words with no matched letter: in between their neighbours
    known = [i for i, t in enumerate(starts) if t is not None]
    first, last_end = heard[0]["start"], min(duration, heard[-1]["end"])
    xs = np.interp(range(len(words)), known, [starts[i] for i in known]) if known else None
    out = []
    for i, w in enumerate(words):
        st = starts[i] if starts[i] is not None else float(xs[i])
        if i == 0:
            st = min(st, first)
        out.append({"text": w, "start": float(st)})
    for i, w in enumerate(out):  # monotonic, each word ends where the next starts
        if i and w["start"] < out[i - 1]["start"] + 0.04:
            w["start"] = out[i - 1]["start"] + 0.04
    for i, w in enumerate(out):
        w["end"] = out[i + 1]["start"] if i + 1 < len(out) else max(w["start"] + 0.2, last_end)
    return out


def aligned(wav: str | Path, script: str, duration: float, fallback: list[dict], settings) -> list[dict]:
    try:
        got = match(script, heard_words(wav, settings.whisper_model, settings.models_dir), duration)
    except Exception as e:  # noqa: BLE001 - captions must never stop a video
        log.warning("caption alignment skipped: %s", e)
        got = None
    return got or fallback
