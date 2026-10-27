"""Audio QA: faster-whisper (CPU, int8) transcript vs script similarity."""
from __future__ import annotations

import logging
import os
import re
import subprocess
import threading
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

log = logging.getLogger(__name__)

_MODEL = None
_LOCK = threading.Lock()

DIACRITICS = re.compile(r"[ؐ-ًؚ-ٰٟۖ-ۭـ]")
NON_LETTERS = re.compile(r"[^ء-ي0-9a-z\s]")


def normalize_ar(text: str) -> str:
    """Spelling-insensitive Arabic: no diacritics/tatweel, unify alef/yaa/taa marbuta/hamza seats."""
    t = DIACRITICS.sub("", text.lower())
    t = re.sub("[إأآٱ]", "ا", t)
    t = t.replace("ى", "ي").replace("ة", "ه").replace("ؤ", "و").replace("ئ", "ي")
    t = NON_LETTERS.sub(" ", t)
    return " ".join(t.split())


def similarity(expected: str, got: str) -> float:
    a, b = normalize_ar(expected), normalize_ar(got)
    if not a or not b:
        return 0.0
    chars = SequenceMatcher(None, a.replace(" ", ""), b.replace(" ", ""), autojunk=False).ratio()
    words = SequenceMatcher(None, a.split(), b.split(), autojunk=False).ratio()
    return max(chars, words)


def _model(name: str, root: Path):
    global _MODEL
    with _LOCK:
        if _MODEL is None:
            from faster_whisper import WhisperModel

            _MODEL = WhisperModel(name, device="cpu", compute_type="int8", download_root=str(root))
        return _MODEL


def load_audio_16k(path: Path) -> np.ndarray:
    """Decode with the ffmpeg CLI (avoids PyAV version mismatches inside faster-whisper)."""
    p = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", "16000",
                        "-f", "s16le", "-"], capture_output=True, check=True)
    return np.frombuffer(p.stdout, dtype=np.int16).astype(np.float32) / 32768.0


def transcribe(path: Path, model_name: str, root: Path) -> str:
    model = _model(model_name, root)
    segs, _ = model.transcribe(load_audio_16k(path), language="ar", beam_size=1, vad_filter=False,
                               condition_on_previous_text=False)
    return " ".join(s.text.strip() for s in segs)


def check(path: Path, script_text: str, threshold: float, model_name: str, root: Path) -> dict:
    got = transcribe(path, model_name, root)
    sim = similarity(script_text, got)
    return {"passed": sim >= threshold, "similarity": round(sim, 3), "transcript": got[:2000],
            "problems": [] if sim >= threshold else [f"transcript similarity {sim:.2f} < {threshold}"]}
