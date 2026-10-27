"""2.5D "puppet" animation: a cut-out character sprite acting over a separate background picture.

Every frame is the same clean picture moved, squashed and tilted, so faces never melt the way the
free-GPU video models did. Layers, back to front: background (moves at half the camera speed =
parallax) -> soft contact shadow -> character (pop-in entrance, breathing, talk bob driven by the
voice, an emotion-specific move, blinks, animated mouth shaped by the emotion).

A scene without a usable sprite + background falls back to FallbackEngine (whole-picture animation).
"""
from __future__ import annotations

import logging
import math
import random
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from .base import FPS, H, W, SceneSpec, VideoEngine
from .face import BLINK_LEVELS, MOUTH_LEVELS, _ring_color, draw_blink, draw_mouth
from .fallback_engine import FallbackEngine, cover, mouth_states, read_rms

log = logging.getLogger(__name__)

BG_OVERSCAN = 1.16  # background is bigger than the frame so the camera can drift and zoom
GROUND = 0.88  # character's feet, as a fraction of the frame height
CHAR_H = 0.60  # character height as a fraction of the frame height


def ease_out_back(x: float) -> float:
    c1, c3 = 1.70158, 2.70158
    x = min(max(x, 0.0), 1.0)
    return 1 + c3 * (x - 1) ** 3 + c1 * (x - 1) ** 2


class PuppetEngine(VideoEngine):
    name = "puppet"

    def __init__(self, preset: str = "veryfast"):
        self.preset = preset
        self.fallback = FallbackEngine(preset)

    # --- per scene sprites -----------------------------------------------------
    def _sprites(self, spec: SceneSpec, target_h: int) -> tuple[dict, Image.Image]:
        sprite = Image.open(spec.extra["sprite"]).convert("RGBA")
        s = target_h / sprite.height
        if sprite.width * s > W * 0.9:
            s = W * 0.9 / sprite.width
        sprite = sprite.resize((max(1, round(sprite.width * s)), max(1, round(sprite.height * s))), Image.LANCZOS)
        sw, sh = sprite.size

        def px(b):
            return (int(b[0] * sw), int(b[1] * sh), int(b[2] * sw), int(b[3] * sh))
        mouth = spec.extra.get("sprite_mouth")
        eyes = [px(e) for e in spec.extra.get("sprite_eyes") or []]
        out = {}
        base_m = {}
        for li, lv in enumerate(MOUTH_LEVELS):
            if mouth:
                mb = px(mouth)
                base_m[li] = draw_mouth(sprite, mb, lv, spec.emotion, _ring_color(sprite, mb))
            else:
                base_m[li] = sprite
        for li in base_m:
            for bi, bl in enumerate(BLINK_LEVELS):
                out[(li, bi)] = draw_blink(base_m[li], eyes, bl)
        shadow = Image.new("L", (int(sw * 0.8), max(8, int(sw * 0.16))), 0)
        ImageDraw.Draw(shadow).ellipse([0, 0, shadow.width - 1, shadow.height - 1], fill=150)
        return out, shadow.filter(ImageFilter.GaussianBlur(shadow.height * 0.25))

    # --- motion ------------------------------------------------------------------
    @staticmethod
    def _pose(emotion: str, t: float, level: float, rnd_phase: float) -> tuple[float, float, float, float, float]:
        """(dx px, dy px, scale x, scale y, rotation deg) for one frame."""
        breathe = math.sin(2 * math.pi * 0.9 * t + rnd_phase)
        sx, sy = 1 - 0.006 * breathe, 1 + 0.012 * breathe
        dx, dy, rot = 0.0, -level * H * 0.012, 0.0
        sy += level * 0.03
        sx -= level * 0.015
        if emotion in ("happy", "excited"):
            period = 0.62 if emotion == "excited" else 0.95
            ph = (t / period) % 1.0
            hop = math.sin(math.pi * ph) ** 1.3
            dy -= hop * H * (0.05 if emotion == "excited" else 0.03)
            land = max(0.0, 1 - ph / 0.12) if ph < 0.12 else 0.0  # squash right after touching down
            sy -= 0.07 * land
            sx += 0.05 * land
            rot = 3 * math.sin(2 * math.pi * t / (period * 2))
        elif emotion == "angry":
            dx += 7 * math.sin(2 * math.pi * 17 * t) * (0.4 + level)
            sx, sy = sx * 1.04, sy * 1.03
            rot = -4 + 1.5 * math.sin(2 * math.pi * 2 * t)
        elif emotion == "scared":
            dx += 5 * math.sin(2 * math.pi * 23 * t)
            dy += 3 * math.sin(2 * math.pi * 19 * t)
            sx, sy = sx * 0.95, sy * 0.93
        elif emotion == "sad":
            sy *= 0.97
            dy += H * 0.01
            rot = 2.5 * math.sin(2 * math.pi * 0.35 * t)
        elif emotion == "surprised":
            jump = math.exp(-t / 0.18) * math.sin(math.pi * min(t / 0.45, 1.0))
            dy -= jump * H * 0.07
            sy *= 1.04 + 0.06 * jump
            sx *= 0.98
        elif emotion == "sarcastic":
            rot = 6 + 2 * math.sin(2 * math.pi * 0.5 * t)
        else:
            rot = 1.8 * math.sin(2 * math.pi * 0.4 * t + rnd_phase)
        return dx, dy, sx, sy, rot

    def render_scene(self, spec: SceneSpec, out: Path, seed: int = 0) -> Path:
        if not (spec.extra.get("sprite") and spec.extra.get("background")):
            return self.fallback.render_scene(spec, out, seed)
        n = max(1, round(spec.duration * FPS))
        rms = read_rms(spec.audio, n)
        states = mouth_states(rms) if spec.extra.get("sprite_mouth") else np.zeros(n, dtype=int)
        env = np.convolve(rms, np.ones(4) / 4, mode="same")
        rnd = random.Random(seed * 1000 + spec.index)
        bgw, bgh = int(W * BG_OVERSCAN) // 2 * 2, int(H * BG_OVERSCAN) // 2 * 2
        bg = cover(Image.open(spec.extra["background"]).convert("RGB"), bgw, bgh)
        bg = bg.filter(ImageFilter.GaussianBlur(2.2))  # shallow depth of field: the character pops
        sprites, shadow = self._sprites(spec, int(H * CHAR_H))
        sw, sh = sprites[(0, 0)].size
        side = (-1) ** spec.index * W * rnd.uniform(0.0, 0.05)  # alternate left/right a little per cut
        base_x, base_y = W / 2 + side, H * GROUND
        zoom_end = 1.0 + rnd.uniform(0.04, 0.08)
        pan = (rnd.uniform(-1, 1) * 0.03, rnd.uniform(-1, 1) * 0.015)
        phase = rnd.uniform(0, 2 * math.pi)
        blinks = []
        t = rnd.uniform(0.6, 1.6)
        while t < spec.duration:
            blinks.append(t)
            t += rnd.uniform(2.2, 4.2)
        out.parent.mkdir(parents=True, exist_ok=True)
        cmd = ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
               "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", self.preset, "-crf", "20",
               "-pix_fmt", "yuv420p", "-frames:v", str(n), str(out)]
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            for i in range(n):
                t = i / FPS
                f = i / max(1, n - 1)
                lv = float(env[i])
                # camera: slow push-in + drift, punch at the cut; background moves at half speed
                z = 1.0 + (zoom_end - 1.0) * f + 0.05 * math.exp(-t / 0.12)
                cam_x, cam_y = pan[0] * W * f, pan[1] * H * f
                cw, ch = W / z, H / z
                bx = (bgw - cw) / 2 + cam_x * 0.5
                by = (bgh - ch) / 2 + cam_y * 0.5
                frame = bg.resize((W, H), Image.BILINEAR, box=(bx, by, bx + cw, by + ch))
                # character
                dx, dy, sx, sy, rot = self._pose(spec.emotion, t, lv, phase)
                pop = ease_out_back(t / 0.32) if t < 0.32 else 1.0
                sx, sy = sx * (0.55 + 0.45 * pop), sy * (0.55 + 0.45 * pop)
                bi = 0
                for b in blinks:
                    k = int((t - b) * FPS)
                    if 0 <= k < 4:
                        bi = (1, 2, 2, 1)[k]
                spr = sprites[(int(states[i]), bi)]
                cw_px, ch_px = max(2, int(sw * sx * z)), max(2, int(sh * sy * z))
                spr = spr.resize((cw_px, ch_px), Image.BILINEAR)
                if abs(rot) > 0.05:
                    spr = spr.rotate(rot, resample=Image.BICUBIC, expand=True)
                # feet stay on the ground line; everything scales around the frame centre with z
                gx = W / 2 + (base_x - W / 2 + cam_x * -0.0 + dx) * z
                gy = H / 2 + (base_y - H / 2 + dy) * z
                hop = max(0.0, (base_y - (base_y + dy)) / (H * 0.07))
                sh_w = int(shadow.width * z * (1 - 0.35 * min(hop, 1)))
                if sh_w > 4:
                    shd = shadow.resize((sh_w, max(2, int(shadow.height * z))), Image.BILINEAR)
                    shd = shd.point(lambda v, a=1 - 0.5 * min(hop, 1): int(v * a))
                    frame.paste((20, 20, 30), (int(W / 2 + (base_x - W / 2) * z - sh_w / 2),
                                               int(H / 2 + (base_y - H / 2) * z - shd.height / 2)), shd)
                frame.paste(spr, (int(gx - spr.width / 2), int(gy - spr.height)), spr)
                p.stdin.write(frame.tobytes())
            p.stdin.close()
        except BrokenPipeError:
            pass
        err = p.stderr.read().decode(errors="replace")
        if p.wait() != 0:
            raise RuntimeError(f"ffmpeg (puppet render) failed: {err[-800:]}")
        return out

    def generate(self, scenes: list[SceneSpec], out_dir: Path, seed: int = 0) -> list[Path]:
        return [self.render_scene(s, out_dir / f"scene_{s.index:02d}.mp4", seed) for s in scenes]
