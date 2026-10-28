"""FFmpeg assembly: fit clips to line durations, concat, burn Arabic subs, duck music."""
from __future__ import annotations

import json
import logging
import random
import wave
from pathlib import Path

import numpy as np

from ..config import FONTS_DIR, MUSIC_DIR
from ..util import ff_path, ffprobe_duration, ffprobe_json, run
from ..video.base import FPS, H, W
from . import sfx
from . import align
from .subtitles import write_ass

log = logging.getLogger(__name__)

MUSIC_BY_FORMAT = {"drama": "soft_drama", "comedy": "silly_bounce", "rivalry": "silly_bounce",
                   "advice": "happy_kitchen"}
COVER = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1,fps={FPS}"


def n_frames(seconds: float) -> int:
    return max(1, round(seconds * FPS))


def fit_clip(src: Path, duration: float, out: Path, preset: str = "veryfast") -> Path:
    """Make `src` exactly `duration` long at 1080x1920/30fps.

    Shorter clips: slow-mo up to 1.6x, beyond that a forward+reverse ping-pong loop.
    Longer clips: trimmed.
    """
    frames = n_frames(duration)
    info = ffprobe_json(src)
    vs = next(s for s in info["streams"] if s["codec_type"] == "video")
    clip_len = float(info["format"].get("duration") or vs.get("duration") or duration)
    exact = (int(vs["width"]), int(vs["height"])) == (W, H) and abs(clip_len - frames / FPS) < 0.5 / FPS
    if exact:
        run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-an", "-c", "copy", str(out)])
        return out
    common = ["-an", "-c:v", "libx264", "-preset", preset, "-crf", "20", "-pix_fmt", "yuv420p",
              "-frames:v", str(frames)]
    ratio = duration / max(clip_len, 0.1)
    if ratio <= 1.0:
        vf = COVER
        run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-vf", vf, *common, str(out)])
    elif ratio <= 1.6:
        vf = f"setpts={ratio:.4f}*PTS,{COVER}"
        run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-vf", vf, *common, str(out)])
    else:
        fc = f"[0:v]{COVER},split[a][b];[b]reverse[r];[a][r]concat=n=2:v=1,loop=loop=-1:size=32767[v]"
        run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-filter_complex", fc, "-map", "[v]",
             *common, str(out)])
    return out


def trailing_silence(wav_path: str, thresh: float = 0.02) -> float:
    """Seconds of near-silence at the end of a voice file."""
    with wave.open(wav_path) as wf:
        rate, ch = wf.getframerate(), wf.getnchannels()
        x = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16).astype(np.float32) / 32768
    if ch > 1:
        x = x.reshape(-1, ch)[:, 0]
    loud = np.nonzero(np.abs(x) > thresh)[0]
    return (len(x) - (loud[-1] + 1)) / rate if loud.size else 0.0


def build_voice(audio: list[dict], seg_durations: list[float], out: Path, sr: int = 44100) -> Path:
    """Concatenate scene wavs, each padded/trimmed to its video segment length (keeps sync exact)."""
    parts = []
    for a, d in zip(audio, seg_durations):
        with wave.open(a["wav"]) as wf:
            assert wf.getframerate() == sr and wf.getsampwidth() == 2
            x = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
            if wf.getnchannels() > 1:
                x = x.reshape(-1, wf.getnchannels())[:, 0]
        n = int(round(d * sr))
        parts.append(np.pad(x, (0, max(0, n - x.size)))[:n])
    y = np.concatenate(parts)
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(y.tobytes())
    return out


def pick_music(fmt: str) -> Path | None:
    tracks = sorted(MUSIC_DIR.glob("*.m4a")) + sorted(MUSIC_DIR.glob("*.mp3"))
    if not tracks:
        return None
    want = MUSIC_BY_FORMAT.get(fmt)
    named = [t for t in tracks if t.stem == want]
    return named[0] if named else random.choice(tracks)


def compose(story, audio: list[dict], clips: list[Path], job_dir: Path, preset: str = "veryfast",
            hook: bool = False, outro: dict | None = None, loop: bool = False, settings=None) -> Path:  # hook: title overlay off (John)
    lines = [sc.line_ar for sc in story.scenes]
    if outro:  # "follow for the next part": one more segment on the last scene's picture
        audio, clips, lines = [*audio, outro], [*clips, clips[-1]], [*lines, outro["text"]]
    work = job_dir / "compose"
    work.mkdir(parents=True, exist_ok=True)
    seg_durs = [n_frames(a["duration"]) / FPS for a in audio]
    if loop:  # end right on the last word, so the replay starts with no gap (rewatch loop)
        seg_durs[-1] = max(0.5, n_frames(max(0.3, seg_durs[-1] - trailing_silence(audio[-1]["wav"]) + 0.06)) / FPS)
    segs = [fit_clip(c, d, work / f"seg_{i:02d}.mp4", preset) for i, (c, d) in enumerate(zip(clips, seg_durs))]
    listing = work / "concat.txt"
    listing.write_text("".join(f"file '{s.resolve().as_posix()}'\n" for s in segs), encoding="utf-8")
    video = work / "video.mp4"
    run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(video)])
    voice = build_voice(audio, seg_durs, work / "voice.wav")
    starts = [sum(seg_durs[:i]) for i in range(len(seg_durs))]
    sfx.mix_into(voice, starts[1:], snores=[(starts[i], seg_durs[i]) for i, ln in enumerate(lines)
                                            if sfx.wants_snore(ln)])

    sub_scenes, offset = [], 0.0
    for line, a, d in zip(lines, audio, seg_durs):
        words = align.aligned(a["wav"], line, d, a["words"], settings) if settings else a["words"]
        sub_scenes.append({"offset": offset, "duration": d, "line": line, "words": words})
        offset += d
    total = offset
    ass = write_ass(sub_scenes, work / "subs.ass", hook=story.title if hook else None)

    final = job_dir / "final.mp4"
    music = pick_music(story.format)
    vf = f"[0:v]ass=filename='{ff_path(ass)}':fontsdir='{ff_path(FONTS_DIR)}'[v]"
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(video), "-i", str(voice)]
    if music:
        cmd += ["-stream_loop", "-1", "-i", str(music)]
        af = ("[1:a]aresample=44100,asplit=2[vo1][vo2];"
              "[2:a]aresample=44100,aformat=channel_layouts=mono,volume=0.32[m];"
              "[m][vo1]sidechaincompress=threshold=0.02:ratio=10:attack=15:release=350[md];"
              "[vo2][md]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[a]")
    else:
        af = "[1:a]aresample=44100,alimiter=limit=0.95[a]"
    cmd += ["-filter_complex", f"{vf};{af}", "-map", "[v]", "-map", "[a]", "-t", f"{total:.3f}",
            "-c:v", "libx264", "-preset", preset, "-crf", "21", "-pix_fmt", "yuv420p",
            "-r", str(FPS), "-c:a", "aac", "-b:a", "160k", "-ac", "2", "-movflags", "+faststart", str(final)]
    run(cmd, timeout=1800)
    (job_dir / "compose" / "timeline.json").write_text(
        json.dumps({"segments": seg_durs, "total": total, "music": str(music) if music else None},
                   ensure_ascii=False), encoding="utf-8")
    log.info("composed %s (%.1fs)", final, ffprobe_duration(final))
    return final
