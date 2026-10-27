"""Designed cover (thumbnail) for every video, 1080x1920 JPEG (John, 2026-10-27: "a very good cover").

Layout: the most expressive scene picture as the background (gently zoomed, vignetted), a dark
band at the top, the title in big yellow Noto Kufi Black with a thick black outline (two lines max),
a series badge ("الجزء 1 من 3") and the channel name at the bottom. Arabic text is drawn by libass
(through ffmpeg), the only renderer here that shapes Arabic correctly.
The title sits in the top third so it stays readable in TikTok/Instagram grid crops (3:4 / 1:1).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

from ..config import CAPTION_FONT, FONTS_DIR
from ..util import ff_path, run

W, H = 1080, 1920
# emotions that make the strongest still: big faces, open mouths
EMOTION_RANK = {"surprised": 0, "scared": 1, "angry": 2, "excited": 3, "sad": 4, "happy": 5,
                "sarcastic": 6, "neutral": 7}


def _norm(t: str) -> str:
    return re.sub(r"[ةه]", "ه", re.sub(r"[أإآ]", "ا", t))


def pick_scene(story, scene_pictures: list[dict]) -> int:
    """Index of the scene whose picture makes the best cover: the character the title names first,
    then the strongest emotion, then the earliest scene."""
    title = _norm(story.title)
    named = {c.id for c in story.characters if _norm(c.name) in title or _norm(c.name).replace("ال", "") in title}
    best, best_key = 0, None
    for i, sc in enumerate(story.scenes):
        if i >= len(scene_pictures):
            break
        key = (0 if sc.character_id in named else 1, EMOTION_RANK.get(sc.emotion, 9), i)
        if best_key is None or key < best_key:
            best, best_key = i, key
    return best


def _cover_crop(im: Image.Image) -> Image.Image:
    s = max(W / im.width, H / im.height) * 1.15  # zoom: the face fills more of the frame
    im = im.resize((round(im.width * s), round(im.height * s)), Image.LANCZOS)
    x = (im.width - W) // 2
    y = max(0, min(im.height - H, int((im.height - H) * 0.55)))  # keep a bit more of the bottom
    return im.crop((x, y, x + W, y + H))


def _wrap_title(title: str, max_chars: int = 11) -> list[str]:
    words = re.sub(r"[؟?!.،,:]+", " ", title).split()
    lines, cur = [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > max_chars:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    if len(lines) > 2:  # keep it to two punchy lines
        lines = [lines[0], " ".join(lines[1:])]
    return lines


def make_cover(story, picture: Path, out: Path, channel: str, badge: str = "") -> Path:
    base = _cover_crop(Image.open(picture).convert("RGB"))
    base = ImageEnhance.Contrast(ImageEnhance.Color(base).enhance(1.18)).enhance(1.06)
    # dark band behind the title (top) + soft bottom fade for the channel name
    shade = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(shade)
    for y in range(int(H * 0.5)):
        d.line([(0, y), (W, y)], fill=int(225 * (1 - y / (H * 0.5)) ** 1.1))
    for y in range(int(H * 0.82), H):
        d.line([(0, y), (W, y)], fill=int(150 * ((y - H * 0.82) / (H * 0.18))))
    base.paste((8, 6, 12), (0, 0), shade.filter(ImageFilter.GaussianBlur(6)))
    vig = Image.new("L", (W, H), 0)
    ImageDraw.Draw(vig).ellipse([-W * 0.25, -H * 0.1, W * 1.25, H * 1.1], fill=255)
    base = Image.composite(base, Image.new("RGB", (W, H), (0, 0, 0)), vig.filter(ImageFilter.GaussianBlur(160)))
    tmp = out.with_suffix(".base.png")
    base.save(tmp)

    lines = _wrap_title(story.title)
    size = 190 if max(len(x) for x in lines) <= 9 else 165
    events = []
    y = 170
    for ln in lines:  # one dialogue per line: tags only at the start of each (RTL-safe)
        events.append(f"Dialogue: 0,0:00:00.00,0:00:05.00,Title,,0,0,{y},,{ln}")
        y += int(size * 1.25)
    if badge:
        events.append(f"Dialogue: 0,0:00:00.00,0:00:05.00,Badge,,0,0,190,,{badge}")  # bottom: keeps the face clear
    ass = out.with_suffix(".ass")
    ass.write_text(f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Title,{CAPTION_FONT},{size},&H0000E1FF,&H0000E1FF,&H00000000,&HA0000000,0,0,0,0,100,100,0,-4,1,14,8,8,40,40,0,178
Style: Badge,{CAPTION_FONT},70,&H00FFFFFF,&H00FFFFFF,&H002A2AE0,&H00000000,0,0,0,0,100,100,0,0,3,20,0,2,50,50,0,178
Style: Channel,{CAPTION_FONT},58,&H00FFFFFF,&H00FFFFFF,&H00000000,&H90000000,0,0,0,0,100,100,0,0,1,6,3,2,50,50,0,178

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
""" + "\n".join(events) + "\n", encoding="utf-8-sig")
    run(["ffmpeg", "-y", "-v", "error", "-i", str(tmp), "-vf",
         f"ass=filename='{ff_path(ass)}':fontsdir='{ff_path(FONTS_DIR)}'", "-frames:v", "1", "-q:v", "2", str(out)])
    tmp.unlink(missing_ok=True)
    ass.unlink(missing_ok=True)
    return out


COVER_ART = (
    "Movie poster style 3D Pixar render, extreme close-up portrait of a cute cartoon {kind} character: "
    "{look}. Hugely exaggerated {emotion} facial expression, big shiny eyes looking into the camera, "
    "dramatic cinematic rim lighting, vivid saturated colors, glowing bokeh background of {place}, "
    "the face fills the lower two thirds of the frame, empty dark space at the top for a title, "
    "vertical composition, ultra detailed. No text, no letters, no watermark"
)
EMOTION_WORDS = {"surprised": "shocked open-mouth surprise", "scared": "terrified panic", "angry": "furious",
                 "sad": "heartbroken crying with big tears", "excited": "over-the-moon excitement",
                 "happy": "huge joyful grin", "sarcastic": "smug sarcastic smirk", "neutral": "curious"}


def cover_art(settings, story, i: int, out: Path) -> Path | None:
    """A picture made just for the cover (Cloudflare FLUX); None when it can't be made right now."""
    from ..images.providers import cloudflare_image
    if not (settings.cloudflare_account_id and settings.cloudflare_api_token):
        return None
    sc = story.scenes[i]
    ch = story.char(sc.character_id)
    prompt = COVER_ART.format(kind=ch.id.replace("_", " "), look=ch.look,
                              emotion=EMOTION_WORDS.get(sc.emotion, sc.emotion),
                              place="a colorful Egyptian kitchen")
    try:
        data = cloudflare_image(settings, prompt)
        im = Image.open(__import__("io").BytesIO(data)).convert("RGB")
        im.save(out, quality=94)
        return out
    except Exception:
        return None


def cover_for_job(job: dict, story, job_dir: Path, channel: str, settings=None) -> tuple[Path, float]:
    """Make job_dir/cover.jpg; returns (path, best video moment in seconds for TikTok's frame cover).
    Background: a picture drawn just for the cover, else the strongest scene picture."""
    imgs = json.loads((job_dir / "images.json").read_text(encoding="utf-8"))
    pics = imgs.get("__scenes__") or [imgs[sc.character_id] for sc in story.scenes]
    i = pick_scene(story, pics)
    badge = f"الجزء {job['part']} من {job['parts']}" if job.get("series_id") else ""
    art = job_dir / "cover_art.jpg"
    if not art.exists() and settings is not None:
        cover_art(settings, story, i, art)
    picture = art if art.exists() else Path(pics[i]["path"])
    out = make_cover(story, picture, job_dir / "cover.jpg", channel, badge)
    t = 0.0
    tl = job_dir / "compose" / "timeline.json"
    if tl.exists():
        segs = json.loads(tl.read_text(encoding="utf-8"))["segments"]
        t = sum(segs[:i]) + min(1.0, segs[i] / 2)
    return out, t


def cover_intro(video: Path, cover: Path, out: Path, frames: int = 3, fps: int = 30) -> Path:
    """Copy of the video that opens with `frames` frames of the cover, for platforms that take their
    thumbnail from the video itself (YouTube Shorts feed, TikTok API). ~0.1 s: not noticeable."""
    dur = frames / fps
    run(["ffmpeg", "-y", "-v", "error", "-loop", "1", "-t", f"{dur:.3f}", "-i", str(cover), "-i", str(video),
         "-f", "lavfi", "-t", f"{dur:.3f}", "-i", "anullsrc=r=44100:cl=stereo",
         "-filter_complex",
         f"[0:v]scale=1080:1920,setsar=1,fps={fps},format=yuv420p[c];[1:v]fps={fps},format=yuv420p[v];"
         "[c][2:a][v][1:a]concat=n=2:v=1:a=1[vo][ao]",
         "-map", "[vo]", "-map", "[ao]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
         "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out)], timeout=1800)
    return out
