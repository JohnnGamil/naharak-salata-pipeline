"""Gemini text-to-speech: far more natural Egyptian delivery than edge-tts (free on the AI Studio key).

Chosen by ear (2026-10-27): male = gemini-3.8-flash-tts "Puck", female = gemini-2.5-flash-preview-tts
"Leda". Newer models (3.x) read any instruction aloud, so they get the bare lines; only 2.5 takes a
style prefix.
Gemini returns no word timings, so caption timings are spread over the detected speech span.
The free tier allows only ~10 requests a day per TTS model, so each voice reads ALL its lines in one
request (one line per paragraph) and the audio is cut at the longest pauses.
"""
from __future__ import annotations

import base64
import io
import logging
import time
import wave
from pathlib import Path

import httpx
import numpy as np

log = logging.getLogger(__name__)

URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
STYLE = ("Say this Arabic line in a natural Egyptian Cairene accent (Egyptian colloquial, like a funny "
         "Egyptian actor), {emotion}, casual pace. Speak ONLY the line:\n")
EMOTION_EN = {
    "neutral": "relaxed and friendly", "happy": "happy and warm", "excited": "excited and energetic",
    "angry": "annoyed and angry", "sad": "sad and dramatic", "scared": "scared and panicking",
    "sarcastic": "sarcastic and teasing", "surprised": "shocked and surprised",
}


class TTSQuota(Exception):
    pass


def takes_style(model: str) -> bool:
    return model.startswith("gemini-2.5")


def parse_voice(spec: str) -> tuple[str, str]:
    model, _, voice = spec.partition(":")
    return model, voice or "Puck"


def _pcm_from(part: dict) -> tuple[np.ndarray, int]:
    raw = base64.b64decode(part["data"])
    if raw[:4] == b"RIFF":
        with wave.open(io.BytesIO(raw)) as w:
            rate, frames = w.getframerate(), w.readframes(w.getnframes())
    else:  # audio/L16;codec=pcm;rate=24000
        mime = part.get("mimeType", "")
        rate = int(mime.split("rate=")[1].split(";")[0]) if "rate=" in mime else 24000
        frames = raw
    return np.frombuffer(frames, dtype="<i2"), rate


def synth(key: str, spec: str, text: str, emotion: str, out_wav: Path, attempts: int = 5,
          raw_prompt: bool = False) -> tuple[np.ndarray, int]:
    model, voice = parse_voice(spec)
    prompt = text if (raw_prompt or not takes_style(model)) else STYLE.format(
        emotion=EMOTION_EN.get(emotion, "natural")) + text
    body = {"contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseModalities": ["AUDIO"],
                                 "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}}}}
    last = ""
    for i in range(attempts):
        try:
            r = httpx.post(URL.format(model=model), json=body, headers={"x-goog-api-key": key}, timeout=120)
        except httpx.HTTPError as e:
            last = str(e)
            time.sleep(5 * (i + 1))
            continue
        if r.status_code == 200:
            try:
                part = r.json()["candidates"][0]["content"]["parts"][0]["inlineData"]
                pcm, rate = _pcm_from(part)
            except (KeyError, IndexError, ValueError) as e:
                last = f"bad reply: {e}"
                continue
            if len(pcm) < rate * 0.3:
                last = "audio too short"
                continue
            out_wav.parent.mkdir(parents=True, exist_ok=True)
            with wave.open(str(out_wav), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(rate)
                w.writeframes(pcm.tobytes())
            return pcm, rate
        last = f"{r.status_code} {r.text[:200]}"
        if r.status_code == 429:
            wait, daily = _quota_info(r)
            if daily and wait > 120:
                raise TTSQuota(last)
            time.sleep(min(max(wait, 20), 90))  # per-minute limit: wait as long as Google asks
        elif r.status_code == 400 and "generate text" in r.text and not prompt.startswith("Say "):
            # very short lines ("أهلا") make the model think it should chat: give it the reading frame
            prompt = STYLE.format(emotion=EMOTION_EN.get(emotion, "natural")) + text
            body["contents"][0]["parts"][0]["text"] = prompt
        elif r.status_code >= 500:
            time.sleep(10 * (i + 1))
        else:
            break
    raise TTSQuota(f"gemini tts failed: {last}") if "429" in last else RuntimeError(f"gemini tts failed: {last}")


def _quota_info(r) -> tuple[float, bool]:
    """(seconds Google asks us to wait, whether a per-day quota is in the violations)."""
    wait, daily = 30.0, False
    try:
        for d in r.json()["error"].get("details", []):
            for v in d.get("violations", []):
                daily |= "PerDay" in (v.get("quotaId") or "")
            if "retryDelay" in d:
                wait = float(str(d["retryDelay"]).rstrip("s") or 30)
    except Exception:
        pass
    return wait, daily


BATCH_STYLE = ("Read these Arabic lines in a natural Egyptian Cairene accent (Egyptian colloquial, like "
               "a funny Egyptian actor), expressive, with the emotion of each line, and a clear pause "
               "between lines. Speak ONLY the lines:\n")


def split_at_pauses(pcm: np.ndarray, rate: int, texts: list[str], win_s: float = 0.02) -> list[np.ndarray]:
    """Cut one recording of len(texts) lines at its len(texts)-1 longest silences."""
    n = len(texts)
    if n == 1:
        return [pcm]
    w = max(1, int(rate * win_s))
    frames = pcm[: len(pcm) // w * w].astype(np.float32).reshape(-1, w)
    rms = np.sqrt((frames ** 2).mean(axis=1))
    quiet = rms < max(rms.max() * 0.05, 150)
    runs, start = [], None
    for i, q in enumerate(quiet):
        if q and start is None:
            start = i
        elif not q and start is not None:
            runs.append((start, i))
            start = None
    on = np.nonzero(~quiet)[0]
    if not len(on):
        raise ValueError("no speech")
    inner = [r for r in runs if r[0] > on[0] and r[1] <= on[-1]]  # pauses between speech only
    if len(inner) < n - 1:
        raise ValueError(f"found {len(inner)} pauses for {n} lines")
    cuts = sorted(inner, key=lambda r: r[1] - r[0], reverse=True)[: n - 1]
    bounds = [0] + sorted(((a + b) // 2) * w for a, b in cuts) + [len(pcm)]
    segs = [pcm[bounds[i]:bounds[i + 1]] for i in range(n)]
    # sanity: each piece's length should roughly follow its text length
    lens = np.array([len(sg) for sg in segs], dtype=float)
    chars = np.array([len(t) for t in texts], dtype=float)
    ratio = (lens / lens.sum()) / (chars / chars.sum())
    if ratio.min() < 0.3 or ratio.max() > 3.0:
        raise ValueError(f"pause split looks wrong (length ratios {np.round(ratio, 2).tolist()})")
    return segs


def synth_lines(key: str, specs: str, texts: list[str], out_wav: Path) -> tuple[str, list[tuple[np.ndarray, int]]]:
    """All lines of one voice in a single request, one PCM piece per line. specs = "model:voice,..."
    tried in order (next one when a model's daily quota is used up or its audio can't be split)."""
    last: Exception | None = None
    for spec in [x.strip() for x in specs.split(",") if x.strip()]:
        model, _ = parse_voice(spec)
        joined = "\n\n".join(texts)  # 3.x models pause at paragraphs by themselves
        if takes_style(model):
            joined = BATCH_STYLE + joined
        try:
            pcm, rate = synth(key, spec, joined, "neutral", out_wav, raw_prompt=True)
            return spec, [(sg, rate) for sg in split_at_pauses(pcm, rate, texts)]
        except (TTSQuota, ValueError, RuntimeError) as e:
            log.warning("tts %s: %s", spec, str(e)[:200])
            last = e
    raise TTSQuota(f"no Gemini TTS model left: {last}")


def speech_span(pcm: np.ndarray, rate: int, win_s: float = 0.02) -> tuple[float, float]:
    """First/last time the signal is clearly above the noise floor."""
    n = max(1, int(rate * win_s))
    frames = pcm[: len(pcm) // n * n].astype(np.float32).reshape(-1, n)
    rms = np.sqrt((frames ** 2).mean(axis=1))
    thr = max(rms.max() * 0.08, 200)
    on = np.nonzero(rms > thr)[0]
    if not len(on):
        return 0.0, len(pcm) / rate
    return on[0] * win_s, (on[-1] + 1) * win_s


def spread_words(text: str, start: float, end: float) -> list[dict]:
    """Word timings spread by length over [start, end] (each word weighs its letters + 2)."""
    words = text.split()
    weights = np.array([len(w) + 2 for w in words], dtype=float)
    edges = start + (end - start) * np.concatenate([[0], np.cumsum(weights) / weights.sum()])
    return [{"text": w, "start": float(edges[i]), "end": float(edges[i + 1])} for i, w in enumerate(words)]
