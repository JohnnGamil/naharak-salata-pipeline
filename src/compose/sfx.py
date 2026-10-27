"""Synthesized sound effects (no licensing): a whoosh on every cut, a pop on the hook, and a real
snore under any scene whose line snores ("خخخ" / شخير - John, 2026-10-28)."""
from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

SR = 44100


def whoosh(dur: float = 0.38, seed: int = 0) -> np.ndarray:
    n = int(SR * dur)
    rng = np.random.default_rng(seed)
    noise = rng.standard_normal(n)
    # sweep a one-pole low-pass from dark to bright and back: the "air rushing past" sound
    t = np.linspace(0, 1, n)
    cutoff = 0.02 + 0.35 * np.sin(np.pi * t) ** 2
    out, y = np.empty(n), 0.0
    for i in range(n):
        y += cutoff[i] * (noise[i] - y)
        out[i] = y
    env = np.sin(np.pi * t) ** 1.5
    out *= env
    return out / (np.abs(out).max() + 1e-9)


def pop(dur: float = 0.09) -> np.ndarray:
    t = np.arange(int(SR * dur)) / SR
    freq = 950 * np.exp(-t * 28) + 260  # quick downward chirp
    phase = 2 * np.pi * np.cumsum(freq) / SR
    return np.sin(phase) * np.exp(-t * 45)


SNORE_MARKS = ("خخخ", "شخير", "بيشخر", "بتشخر", "zzz", "Zzz")


def wants_snore(line: str) -> bool:
    return any(m in line for m in SNORE_MARKS)


# real recording: "Snoring of old man" by Saaabaaato, Wikimedia Commons, CC0 (no attribution needed);
# the 3 strongest snores, cleaned and placed close together (John found the synthesized one fake)
SNORE_WAV = Path(__file__).resolve().parents[2] / "assets" / "sfx" / "snore.wav"


def snore(dur: float, seed: int = 0) -> np.ndarray:
    """The real snore clip, looped to fill `dur` seconds (silence if the asset is missing)."""
    n = int(SR * dur)
    if not SNORE_WAV.exists():
        return np.zeros(n)
    with wave.open(str(SNORE_WAV)) as w:
        clip = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768
    out = np.resize(clip, n)  # repeats the clip
    fade = min(n, int(SR * 0.15))
    out[-fade:] *= np.linspace(1, 0, fade)
    return out


def mix_into(voice_wav: Path, cut_times: list[float], level: float = 0.22,
             snores: list[tuple[float, float]] | None = None) -> None:
    """Add a pop at t=0, a whoosh centred just before each cut and a snore over each (start, length)
    span, in place."""
    with wave.open(str(voice_wav)) as w:
        rate, ch, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        data = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768
    if rate != SR or ch != 1 or width != 2:
        return  # composer always writes 44.1 kHz mono s16; anything else: leave the voice untouched
    events = [(0.0, pop(), level * 1.2)]
    events += [(max(0.0, t - 0.2), whoosh(seed=i), level) for i, t in enumerate(cut_times)]
    events += [(a, snore(d, seed=i), level * 2.5) for i, (a, d) in enumerate(snores or []) if d > 0.5]
    for start, snd, lv in events:
        a = int(start * SR)
        b = min(len(data), a + len(snd))
        if b > a:
            data[a:b] += snd[: b - a] * lv
    data = np.clip(data, -1, 1)
    with wave.open(str(voice_wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((data * 32767).astype("<i2").tobytes())
