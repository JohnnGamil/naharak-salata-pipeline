"""Synthesize royalty-free background loops into assets/music (no licensing questions at all).

Usage: python scripts/make_music.py
"""
from __future__ import annotations

import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

SR = 44100
OUT = Path(__file__).resolve().parent.parent / "assets" / "music"

NOTE = {n: i for i, n in enumerate(["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"])}


def freq(name: str, octave: int) -> float:
    return 440.0 * 2 ** ((NOTE[name] + 12 * (octave + 1) - 69) / 12)


def pluck(f: float, dur: float, bright: float = 0.5, seed: int = 0) -> np.ndarray:
    """Karplus-Strong plucked string."""
    n = int(SR * dur)
    period = max(2, int(SR / f))
    rng = np.random.default_rng(seed)
    buf = rng.uniform(-1, 1, period)
    out = np.empty(n)
    decay = 0.996 - 0.004 * (1 - bright)
    for i in range(n):
        out[i] = buf[i % period]
        buf[i % period] = decay * 0.5 * (buf[i % period] + buf[(i + 1) % period])
    return out


def tone(f: float, dur: float, harmonics=(1, 0.5, 0.25), attack=0.01, release=0.2) -> np.ndarray:
    t = np.arange(int(SR * dur)) / SR
    y = sum(a * np.sin(2 * np.pi * f * (k + 1) * t) for k, a in enumerate(harmonics))
    env = np.minimum(1, t / attack) * np.exp(-t / max(release, 1e-3))
    return y * env


def kick(dur=0.25):
    t = np.arange(int(SR * dur)) / SR
    return np.sin(2 * np.pi * (50 + 90 * np.exp(-t * 30)) * t) * np.exp(-t * 14)


def hat(dur=0.05, seed=0):
    t = np.arange(int(SR * dur)) / SR
    return np.random.default_rng(seed).uniform(-1, 1, t.size) * np.exp(-t * 90) * 0.3


def place(track, clip, at):
    i = int(at * SR)
    j = min(track.size, i + clip.size)
    if i < track.size:
        track[i:j] += clip[: j - i]


def song(bpm: int, progression, scale_notes, seed: int, length_s: float = 64.0, swing=0.0):
    beat = 60 / bpm
    total = np.zeros(int(SR * length_s))
    rng = np.random.default_rng(seed)
    bar = 4 * beat
    bars = int(length_s / bar)
    for b in range(bars):
        root, quality = progression[b % len(progression)]
        third = 3 if quality == "m" else 4
        chord = [freq(root, 3) * 2 ** (s / 12) for s in (0, third, 7)]
        t0 = b * bar
        place(total, 0.35 * tone(chord[0] / 2, bar, (1, 0.3), release=bar * 0.6), t0)  # bass
        for step in range(8):  # plucked arpeggio
            f = chord[step % 3] * (2 if step >= 4 else 1)
            off = (swing * beat / 2) if step % 2 else 0
            place(total, 0.22 * pluck(f, 0.6, seed=seed + b * 8 + step), t0 + step * beat / 2 + off)
        for k in range(4):
            if k in (0, 2):
                place(total, 0.5 * kick(), t0 + k * beat)
            place(total, hat(seed=b * 4 + k), t0 + k * beat + beat / 2)
        if b % 2 == 1:  # little melody every other bar
            for k in range(4):
                n = scale_notes[rng.integers(len(scale_notes))]
                place(total, 0.12 * tone(freq(n, 5), beat * 0.9, (1, 0.2, 0.1), release=0.25), t0 + k * beat)
    # fade in/out, normalise
    fade = int(SR * 1.5)
    total[:fade] *= np.linspace(0, 1, fade)
    total[-fade:] *= np.linspace(1, 0, fade)
    return total / (np.max(np.abs(total)) + 1e-9) * 0.8


def save(y: np.ndarray, path: Path):
    wav = path.with_suffix(".wav")
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((y * 32767).astype(np.int16).tobytes())
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(wav), "-c:a", "aac", "-b:a", "128k", str(path)],
                   check=True)
    wav.unlink()


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    major = ["C", "D", "E", "G", "A"]
    minor = ["A", "C", "D", "E", "G"]
    tracks = {
        "happy_kitchen.m4a": (112, [("C", ""), ("G", ""), ("A", "m"), ("F", "")], major, 1, 0.15),
        "silly_bounce.m4a": (126, [("F", ""), ("C", ""), ("D", "m"), ("A#", "")], major, 2, 0.25),
        "soft_drama.m4a": (84, [("A", "m"), ("F", ""), ("C", ""), ("G", "")], minor, 3, 0.0),
    }
    for name, (bpm, prog, scale, seed, swing) in tracks.items():
        save(song(bpm, prog, scale, seed, swing=swing), OUT / name)
        print("wrote", OUT / name)


if __name__ == "__main__":
    sys.exit(main())
