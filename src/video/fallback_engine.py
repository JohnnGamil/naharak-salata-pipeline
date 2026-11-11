"""CPU-only "mouth-flap" animation: always works, no GPU, no network.

The character image gets an animated cartoon mouth driven by the voice RMS, plus a slow
zoom/pan, a punch-in at each cut and a small bounce on loud syllables. Frames are rendered
with PIL and piped straight into ffmpeg.
"""
from __future__ import annotations

import logging
import math
import random
import subprocess
import wave
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from . import face, visemes
from .base import FPS, H, W, SceneSpec, VideoEngine

log = logging.getLogger(__name__)

OVERSCAN = 1.14  # base canvas is bigger than the output so we can zoom/pan/bounce
BW, BH = int(W * OVERSCAN) // 2 * 2, int(H * OVERSCAN) // 2 * 2
MOUTH_LEVELS = (0.0, 0.35, 0.7, 1.0)
LEVEL_SHAPE = ("X", "B", "A", "A2")  # loudness level -> mouth shape when the words are unknown
ROUGH_LEVEL = {"X": 0.0, "M": 0.0, "B": 0.35, "E": 0.35, "O": 0.35, "F": 0.35, "A": 0.7, "A2": 1.0}


def cover(im: Image.Image, w: int, h: int) -> Image.Image:
    """Resize + centre-crop to exactly w x h (no blur bands, no letterbox)."""
    s = max(w / im.width, h / im.height)
    im = im.resize((math.ceil(im.width * s), math.ceil(im.height * s)), Image.LANCZOS)
    x, y = (im.width - w) // 2, (im.height - h) // 2
    return im.crop((x, y, x + w, y + h))


def cover_box(src_w: int, src_h: int, box: list[float], w: int, h: int) -> tuple[int, int, int, int]:
    """Map a normalized box in the source image into pixel coords of the cover()ed image."""
    s = max(w / src_w, h / src_h)
    nw, nh = src_w * s, src_h * s
    ox, oy = (nw - w) / 2, (nh - h) / 2
    x0, y0, x1, y1 = box
    return (int(x0 * nw - ox), int(y0 * nh - oy), int(x1 * nw - ox), int(y1 * nh - oy))


def read_rms(wav_path: Path, n_frames: int) -> np.ndarray:
    with wave.open(str(wav_path)) as wf:
        sr, ch, sw = wf.getframerate(), wf.getnchannels(), wf.getsampwidth()
        raw = wf.readframes(wf.getnframes())
    dtype = {1: np.int8, 2: np.int16, 4: np.int32}[sw]
    a = np.frombuffer(raw, dtype=dtype).astype(np.float32)
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1)
    hop = sr / FPS
    out = np.zeros(n_frames, dtype=np.float32)
    for i in range(n_frames):
        seg = a[int(i * hop): int((i + 1) * hop)]
        out[i] = float(np.sqrt(np.mean(seg ** 2))) if seg.size else 0.0
    ref = np.percentile(out[out > 0], 90) if np.any(out > 0) else 1.0
    return np.clip(out / (ref or 1.0), 0, 1.2)


def mouth_states(rms: np.ndarray) -> np.ndarray:
    """RMS -> mouth level index with a little hysteresis so it doesn't flicker."""
    idx = np.digitize(rms, [0.12, 0.35, 0.65])
    out = idx.copy()
    for i in range(1, len(idx)):
        # change at most 2 levels per frame, and hold an open mouth for 2 frames
        if idx[i] < out[i - 1] and i >= 2 and out[i - 1] != out[i - 2]:
            out[i] = out[i - 1]
        out[i] = int(np.clip(out[i], out[i - 1] - 2, out[i - 1] + 2))
    return out


def _skin_color(im: Image.Image, box) -> tuple[int, int, int]:
    x0, y0, x1, y1 = box
    pad = max(8, (x1 - x0) // 3)
    arr = np.asarray(im).astype(np.int32)
    ring = []
    for (a, b, c, d) in ((x0 - pad, y0, x0, y1), (x1, y0, x1 + pad, y1), (x0, y1, x1, y1 + pad)):
        a, b = max(0, a), max(0, b)
        c, d = min(im.width, c), min(im.height, d)
        if c > a and d > b:
            ring.append(arr[b:d, a:c].reshape(-1, 3))
    if not ring:
        return (200, 60, 50)
    return tuple(int(v) for v in np.median(np.concatenate(ring), axis=0))


def draw_mouth(base: Image.Image, box, level: float, skin, erase: bool) -> Image.Image:
    im = base.copy()
    x0, y0, x1, y1 = box
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    mw = max(x1 - x0, im.width * 0.11)
    if erase:  # paint over the drawn mouth with surrounding skin colour
        # cover the whole detected box (+15%): AI pictures often have a big open grin, and a
        # width-based ellipse left its lower half showing behind the animated mouth
        px, py = (x1 - x0) * 0.15, (y1 - y0) * 0.15
        ew, eh = max(mw * 0.62, (x1 - x0) / 2 + px), max(mw * 0.30, (y1 - y0) / 2 + py)
        mask = Image.new("L", im.size, 0)
        ImageDraw.Draw(mask).ellipse([cx - ew, cy - eh, cx + ew, cy + eh], fill=255)
        im.paste(skin, (0, 0), mask.filter(ImageFilter.GaussianBlur(mw * 0.08)))
    d = ImageDraw.Draw(im)
    dark = tuple(int(c * 0.35) for c in skin)
    lw = max(4, int(mw * 0.06))
    if level <= 0.01:
        d.arc([cx - mw * 0.4, cy - mw * 0.22, cx + mw * 0.4, cy + mw * 0.12], 20, 160, fill=dark, width=lw)
        return im
    oh = mw * (0.10 + 0.42 * level)
    ow = mw * (0.55 + 0.25 * level)
    box_m = [cx - ow / 2, cy - oh * 0.35, cx + ow / 2, cy + oh * 0.65]
    d.ellipse(box_m, fill=(70, 15, 20), outline=dark, width=lw)
    if level >= 0.35:  # teeth + tongue
        d.chord([box_m[0] + ow * 0.15, box_m[1] + lw * 0.6, box_m[2] - ow * 0.15, box_m[1] + oh * 0.45],
                0, 180, fill=(250, 250, 245))
        d.ellipse([cx - ow * 0.28, box_m[3] - oh * 0.45, cx + ow * 0.28, box_m[3] - lw * 0.5],
                  fill=(225, 90, 100))
    return im


class FallbackEngine(VideoEngine):
    name = "fallback"

    def __init__(self, preset: str = "veryfast"):
        self.preset = preset

    def _bases(self, spec: SceneSpec, shapes) -> dict[tuple[str, int], Image.Image]:
        """(mouth shape, blink level) -> overscanned frame base, for the shapes this scene uses."""
        src = Image.open(spec.image).convert("RGB")
        box_n = spec.meta.get("mouth", [0.42, 0.50, 0.58, 0.56])
        box = cover_box(src.width, src.height, box_n, BW, BH)
        base = cover(src, BW, BH)
        eyes = [cover_box(src.width, src.height, e, BW, BH) for e in spec.meta.get("eyes") or []]
        mouths = {}
        for sh in sorted(set(shapes)):
            if spec.meta.get("guessed"):
                # mouth position unknown (no vision model): a mouth in the wrong place looks worse
                # than none, so the character "talks" with squash-and-stretch only
                mouths[sh] = base
            elif spec.meta.get("procedural"):  # the PIL cartoon has no mouth of its own to erase
                skin = tuple(spec.meta.get("skin") or _skin_color(base, box))
                mouths[sh] = draw_mouth(base, box, ROUGH_LEVEL[sh], skin, erase=False)
            else:  # erase the picture's mouth, draw the one for this sound (shaped by the emotion)
                rgba = base.convert("RGBA")
                mouths[sh] = face.draw_shape(rgba, box, sh, spec.emotion, face._ring_color(rgba, box)).convert("RGB")
        out = {}
        for sh, m in mouths.items():
            for bi, amount in enumerate(face.BLINK_LEVELS):
                out[(sh, bi)] = (m if bi == 0 or not eyes
                                 else face.draw_blink(m.convert("RGBA"), eyes, amount).convert("RGB"))
        return out

    @staticmethod
    def _emotion_cam(emotion: str, t: float, f: float, rnd: random.Random) -> tuple[float, float, float]:
        """Extra (zoom, dx px, dy px) that sells the line's emotion with the camera."""
        if emotion == "angry":  # hard shake
            return 0.02, rnd.uniform(-7, 7), rnd.uniform(-5, 5)
        if emotion == "scared":  # fast small tremble, creeping in
            return 0.03 * f, rnd.uniform(-3, 3), rnd.uniform(-3, 3)
        if emotion in ("happy", "excited"):  # bouncing camera
            period = 0.55 if emotion == "excited" else 0.85
            return 0.0, 0.0, -abs(math.sin(math.pi * t / period)) * (26 if emotion == "excited" else 16)
        if emotion == "surprised":  # snap zoom in, then hold
            return 0.14 * (1 - math.exp(-t / 0.12)), 0.0, 0.0
        if emotion == "sad":  # slow pull back and sink
            return 0.07 * (1 - f) - 0.03, 0.0, 14 * f
        if emotion == "sarcastic":  # lazy side drift
            return 0.0, 30 * f, 0.0
        return 0.0, 0.0, 0.0

    def render_scene(self, spec: SceneSpec, out: Path, seed: int = 0) -> Path:
        n = max(1, round(spec.duration * FPS))
        rms = read_rms(spec.audio, n)
        words = spec.extra.get("words")
        if words:  # the mouth makes the shape of each letter being said
            shapes = visemes.frame_shapes(visemes.timeline(words), rms, FPS)
        else:
            shapes = [LEVEL_SHAPE[int(s)] for s in mouth_states(rms)]
        env = np.convolve(rms, np.ones(4) / 4, mode="same")
        bases = self._bases(spec, shapes)
        rnd = random.Random(seed * 1000 + spec.index)
        blinks, tb = [], rnd.uniform(0.6, 1.6)
        while tb < spec.duration:
            blinks.append(tb)
            tb += rnd.uniform(2.2, 4.2)
        zoom_end = 1.0 + rnd.uniform(0.04, 0.08)
        pan = (rnd.uniform(-1, 1) * 0.02, rnd.uniform(-1, 1) * 0.012)
        squash = 0.035 if spec.meta.get("guessed") else 0.012
        out.parent.mkdir(parents=True, exist_ok=True)
        cmd = ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
               "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
               "-c:v", "libx264", "-preset", self.preset, "-crf", "20", "-pix_fmt", "yuv420p",
               "-frames:v", str(n), str(out)]
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            for i in range(n):
                t = i / FPS
                f = i / max(1, n - 1)
                ez, ex, ey = self._emotion_cam(spec.emotion, t, f, rnd)
                z = max(1.0, 1.0 + (zoom_end - 1.0) * f + 0.07 * math.exp(-t / 0.12) + ez)  # punch-in at the cut
                cw = (BW / OVERSCAN) / z  # z=1 shows the centre W x H of the overscanned base
                ch = cw * H / W * (1 - squash * float(env[i]))  # stretch up on loud syllables
                cx = BW / 2 + pan[0] * BW * f
                cy = BH / 2 + pan[1] * BH * f - float(env[i]) * 16  # bounce up on loud syllables
                cx, cy = cx + ex, cy + ey
                x0 = min(max(0, cx - cw / 2), BW - cw)
                y0 = min(max(0, cy - ch / 2), BH - ch)
                bi = 0
                for b in blinks:
                    k = int((t - b) * FPS)
                    if 0 <= k < 4:
                        bi = (1, 2, 2, 1)[k]
                frame = bases[(shapes[i], bi)].resize(
                    (W, H), Image.BILINEAR, box=(x0, y0, x0 + cw, y0 + ch)
                )
                p.stdin.write(frame.tobytes())
            p.stdin.close()
        except BrokenPipeError:
            pass
        err = p.stderr.read().decode(errors="replace")
        if p.wait() != 0:
            raise RuntimeError(f"ffmpeg (fallback render) failed: {err[-800:]}")
        return out

    def generate(self, scenes: list[SceneSpec], out_dir: Path, seed: int = 0) -> list[Path]:
        return [self.render_scene(s, out_dir / f"scene_{s.index:02d}.mp4", seed) for s in scenes]
