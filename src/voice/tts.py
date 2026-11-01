"""Voice-over per scene, with word timings for subtitles: Gemini TTS first, edge-tts fallback."""
from __future__ import annotations

import asyncio
import json
import logging
import time
import wave
from pathlib import Path

import edge_tts

from ..util import ffprobe_duration, run
from ..ideas import cast
from ..video.base import QuotaWait
from . import gemini_tts

log = logging.getLogger(__name__)

# emotion -> (rate %, pitch Hz)
EMOTION = {
    "neutral": (0, 0),
    "happy": (6, 6),
    "excited": (12, 10),
    "angry": (8, -4),
    "sad": (-10, -6),
    "scared": (10, 8),
    "sarcastic": (-4, 2),
    "surprised": (6, 12),
}
GAP_S = 0.25  # silence after each line


HOOK_SPEED = 1.08


def char_pitches(story) -> dict[str, int]:
    """Characters sharing a voice get different pitch offsets so they sound distinct."""
    out, used = {}, {}
    for c in story.characters:
        n = used.get(c.voice, 0)
        out[c.id] = [0, 14, -10, 24][n % 4]
        used[c.voice] = n + 1
    return out


async def _synth(text: str, voice: str, rate: int, pitch: int, mp3: Path) -> list[dict]:
    com = edge_tts.Communicate(
        text, voice, rate=f"{rate:+d}%", pitch=f"{pitch:+d}Hz", boundary="WordBoundary"
    )
    words = []
    with open(mp3, "wb") as f:
        async for chunk in com.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] in ("WordBoundary", "SentenceBoundary"):
                words.append({
                    "text": chunk["text"],
                    "start": chunk["offset"] / 1e7,
                    "end": (chunk["offset"] + chunk["duration"]) / 1e7,
                })
    return words


def synth_line(text: str, voice: str, rate: int, pitch: int, wav: Path, attempts: int = 4) -> dict:
    mp3 = wav.with_suffix(".mp3")
    wav.parent.mkdir(parents=True, exist_ok=True)
    last = None
    for i in range(attempts):
        try:
            words = asyncio.run(_synth(text, voice, rate, pitch, mp3))
            if mp3.stat().st_size < 2000:
                raise RuntimeError("edge-tts returned no audio")
            break
        except Exception as e:  # network / service hiccups
            last = e
            log.warning("edge-tts attempt %d failed: %s", i + 1, e)
            time.sleep(3 * (i + 1))
    else:
        raise RuntimeError(f"edge-tts failed: {last}")
    # pad with a short tail of silence, normalise format
    run(["ffmpeg", "-y", "-v", "error", "-i", str(mp3), "-af", f"apad=pad_dur={GAP_S}",
         "-ar", "44100", "-ac", "1", str(wav)])
    mp3.unlink(missing_ok=True)
    return {"wav": str(wav), "duration": ffprobe_duration(wav), "words": words}


def gemini_voices(story) -> dict[str, str]:
    """Gemini voice name per character: the cast's own voice, guests a free one of their gender."""
    out: dict[str, str] = {}
    for c in story.characters:
        out[c.id] = cast.gemini_voice(c.id, c.voice, set(out.values()))
    return out


def gemini_semitones(story, voices: dict[str, str]) -> dict[str, int]:
    """Two characters that ended up on the same voice: shift the 2nd one's pitch."""
    out, used = {}, {}
    for c in story.characters:
        n = used.get(voices[c.id], 0)
        member = cast.BY_ID.get(c.id)
        out[c.id] = [0, 3, -3, 5][n % 4] + (member.pitch if member else 0)
        used[voices[c.id]] = n + 1
    return out


def _model_list(specs: str) -> list[str]:
    return [x.split(":")[0].strip() for x in specs.split(",") if x.strip()]


def _gemini_story(story, adir: Path, settings) -> list[dict]:
    voices = gemini_voices(story)
    shifts = gemini_semitones(story, voices)
    # model order comes from the settings (per gender), the voice from the cast
    spec_of = {c.id: ",".join(f"{m}:{voices[c.id]}" for m in _model_list(
        settings.gemini_tts_female if "Salma" in c.voice else settings.gemini_tts_male))
        for c in story.characters}
    pieces: dict[int, tuple] = {}
    used_spec: dict[int, str] = {}
    for spec in dict.fromkeys(spec_of.values()):  # one request per character for the whole skit
        idx = [i for i, sc in enumerate(story.scenes) if spec_of[sc.character_id] == spec]
        used, got = gemini_tts.synth_lines(settings.gemini_api_key, spec, [story.scenes[i].line_ar for i in idx],
                                           adir / f"voice_{len(pieces):02d}_raw.wav")
        pieces.update(zip(idx, got))
        used_spec.update({i: used for i in idx})
    out = []
    for i, sc in enumerate(story.scenes):
        ch = story.char(sc.character_id)
        pcm, rate = pieces[i]
        raw = adir / f"scene_{i:02d}_raw.wav"
        with wave.open(str(raw), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(pcm.tobytes())
        t0, t1 = gemini_tts.speech_span(pcm, rate)
        # trim the silence the pause-split left around the line, keep a short lead-in
        lead = min(t0, 0.08)
        wav = adir / f"scene_{i:02d}.wav"
        af = f"atrim=start={t0 - lead:.3f}:end={t1 + 0.05:.3f},asetpts=PTS-STARTPTS,apad=pad_dur={GAP_S}"
        speed = HOOK_SPEED if i == 0 else 1.0  # the hook is said fast and urgent (John)
        if speed != 1.0:
            af = af.replace(",apad=", f",atempo={speed},apad=")
        st = shifts[ch.id]
        if st:
            f = 2 ** (st / 12)  # pitch up/down without changing speed
            af = f"asetrate={rate * f:.0f},aresample={rate},atempo={1 / f:.5f},{af}"
        run(["ffmpeg", "-y", "-v", "error", "-i", str(raw), "-af", af, "-ar", "44100", "-ac", "1", str(wav)])
        raw.unlink(missing_ok=True)
        out.append({"wav": str(wav), "duration": ffprobe_duration(wav), "scene": i, "voice": used_spec[i],
                    "words": gemini_tts.spread_words(sc.line_ar, lead / speed, (lead + t1 - t0) / speed)})
    for f in adir.glob("voice_*_raw.wav"):
        f.unlink(missing_ok=True)
    return out


def synthesize_story(story, job_dir: Path, extra_pitch: dict[str, int] | None = None,
                     settings=None) -> list[dict]:
    adir = job_dir / "audio"
    adir.mkdir(parents=True, exist_ok=True)
    if settings is not None and settings.tts_engine == "gemini" and settings.gemini_api_key:
        try:  # all lines or none: one video never mixes Gemini and edge-tts voices
            out = _gemini_story(story, adir, settings)
            (adir / "audio.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
            return out
        except gemini_tts.TTSQuota as e:
            # every Gemini voice model is out of today's free requests: wait for the reset rather
            # than posting a video with the robotic fallback voice
            raise QuotaWait(f"Gemini voices used up for today: {e}") from e
        except Exception as e:
            log.warning("gemini tts failed, using edge-tts for this video: %s", e)
    pitches = char_pitches(story)
    if extra_pitch:
        for k, v in extra_pitch.items():
            pitches[k] = pitches.get(k, 0) + v
    out = []
    for i, sc in enumerate(story.scenes):
        ch = story.char(sc.character_id)
        rate, pitch = EMOTION.get(sc.emotion, (0, 0))
        info = synth_line(sc.line_ar, ch.voice, rate, pitch + pitches[ch.id], adir / f"scene_{i:02d}.wav")
        info["scene"] = i
        out.append(info)
    (adir / "audio.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


OUTRO_TEXT = "تابعونا عشان تشوفوا الجزء الجاي"


def synth_outro(story, adir: Path, settings, text: str = OUTRO_TEXT) -> dict:
    """One extra closing line, said by the last scene's character (series parts before the last)."""
    sc = story.scenes[-1]
    ch = story.char(sc.character_id)
    wav = adir / "outro.wav"
    raw = adir / "outro_raw.wav"
    if settings is not None and settings.tts_engine == "gemini" and settings.gemini_api_key:
        voice = gemini_voices(story)[ch.id]
        specs = ",".join(f"{m}:{voice}" for m in _model_list(
            settings.gemini_tts_female if "Salma" in ch.voice else settings.gemini_tts_male))
        try:
            _, pieces = gemini_tts.synth_lines(settings.gemini_api_key, specs, [text], raw)
            pcm, rate = pieces[0]
            with wave.open(str(raw), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(rate)
                w.writeframes(pcm.tobytes())
            t0, t1 = gemini_tts.speech_span(pcm, rate)
            run(["ffmpeg", "-y", "-v", "error", "-i", str(raw), "-af",
                 f"atrim=start={max(0, t0 - 0.05):.3f}:end={t1 + 0.05:.3f},asetpts=PTS-STARTPTS,apad=pad_dur=0.6",
                 "-ar", "44100", "-ac", "1", str(wav)])
            raw.unlink(missing_ok=True)
            return {"wav": str(wav), "duration": ffprobe_duration(wav), "text": text,
                    "words": gemini_tts.spread_words(text, 0.05, 0.05 + (t1 - t0))}
        except gemini_tts.TTSQuota as e:
            raise QuotaWait(f"Gemini voices used up for today: {e}") from e
    info = synth_line(text, ch.voice, 6, 0, wav)
    info["text"] = text
    return info
