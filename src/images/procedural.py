"""Offline cartoon character drawn with PIL: the last-resort image source (no network).

Draws a fruit/vegetable body with eyes on a kitchen-ish background and leaves the mouth area
clean, so the fallback animator can paint the mouth. Returns the mouth box it used.
"""
from __future__ import annotations

import hashlib
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

W, H = 1080, 1920

# keyword -> (body colour, shape, leaf colour or None)
PALETTE = [
    (("tomato", "طماطم"), (222, 45, 38), "round", (46, 140, 60)),
    (("onion", "بصل"), (196, 134, 70), "round", (120, 170, 80)),
    (("potato", "بطاط"), (176, 128, 80), "oval", None),
    (("cucumber", "خيار"), (70, 150, 60), "long", (250, 210, 60)),
    (("banana", "موز"), (245, 210, 60), "long", (110, 90, 50)),
    (("watermelon", "بطيخ"), (40, 130, 60), "round", None),
    (("lemon", "ليمون"), (250, 220, 50), "oval", (60, 150, 60)),
    (("eggplant", "بتنجان", "باذنجان"), (100, 50, 130), "long", (60, 140, 60)),
    (("orange", "برتقال"), (250, 150, 30), "round", (50, 140, 60)),
    (("carrot", "جزر"), (240, 120, 30), "long", (60, 160, 60)),
    (("pepper", "فلفل"), (220, 40, 40), "oval", (50, 130, 50)),
    (("apple", "تفاح"), (210, 30, 50), "round", (70, 150, 50)),
    (("mango", "مانج"), (250, 170, 40), "oval", (60, 140, 50)),
    (("garlic", "توم", "ثوم"), (240, 235, 220), "round", None),
]


def _style(text: str):
    t = text.lower()
    for keys, color, shape, leaf in PALETTE:
        if any(k in t for k in keys):
            return color, shape, leaf
    rnd = random.Random(int(hashlib.md5(t.encode()).hexdigest()[:8], 16))
    return (rnd.randint(60, 240), rnd.randint(60, 200), rnd.randint(40, 160)), "round", (60, 150, 60)


def _shade(c, f):
    return tuple(max(0, min(255, int(v * f))) for v in c)


def draw_character(key_text: str, out: Path, seed: int = 0) -> dict:
    """Draw a character; returns {"mouth": [x0,y0,x1,y1]} in normalized coords."""
    color, shape, leaf = _style(key_text)
    rnd = random.Random(seed)
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    # background: warm wall gradient + counter
    top, bottom = (255, 236, 205), (250, 205, 160)
    for y in range(H):
        f = y / H
        d.line([(0, y), (W, y)], fill=tuple(int(top[i] * (1 - f) + bottom[i] * f) for i in range(3)))
    for x in range(0, W, 120):  # tiles
        d.line([(x, 0), (x, int(H * 0.68))], fill=(240, 220, 190), width=3)
    for y in range(0, int(H * 0.68), 120):
        d.line([(0, y), (W, y)], fill=(240, 220, 190), width=3)
    d.rectangle([0, int(H * 0.68), W, H], fill=(150, 100, 70))
    d.rectangle([0, int(H * 0.68), W, int(H * 0.70)], fill=(120, 80, 55))

    cx = W // 2
    if shape == "round":
        bw, bh = 780, 760
    elif shape == "oval":
        bw, bh = 700, 860
    else:
        bw, bh = 540, 950
    cy = int(H * 0.70) - bh // 2  # sits on the counter, leaves stay inside the zoomed crop
    box = [cx - bw // 2, cy - bh // 2, cx + bw // 2, cy + bh // 2]
    # shadow on the counter
    shadow = Image.new("L", (W, H), 0)
    ImageDraw.Draw(shadow).ellipse([cx - bw // 2, box[3] - 40, cx + bw // 2, box[3] + 50], fill=120)
    img.paste((60, 40, 30), (0, 0), shadow.filter(ImageFilter.GaussianBlur(25)))
    d.ellipse(box, fill=color, outline=_shade(color, 0.6), width=10)
    # highlight
    hl = Image.new("L", (W, H), 0)
    ImageDraw.Draw(hl).ellipse(
        [box[0] + bw * 0.15, box[1] + bh * 0.08, box[0] + bw * 0.45, box[1] + bh * 0.3], fill=90
    )
    img.paste((255, 255, 255), (0, 0), hl.filter(ImageFilter.GaussianBlur(30)))
    d = ImageDraw.Draw(img)
    if leaf:
        for i in range(5):
            ang = -60 + i * 30 + rnd.randint(-8, 8)
            lx = cx + ang * 2.2
            d.ellipse([lx - 45, box[1] - 120, lx + 45, box[1] + 40], fill=leaf, outline=_shade(leaf, 0.6), width=5)
    # face
    face_y = box[1] + int(bh * 0.42)
    eye_dx, eye_r = int(bw * 0.17), int(min(bw, bh) * 0.11)
    for sx in (-1, 1):
        ex = cx + sx * eye_dx
        d.ellipse([ex - eye_r, face_y - eye_r * 1.2, ex + eye_r, face_y + eye_r * 1.2], fill="white",
                  outline=(30, 30, 30), width=6)
        pr = int(eye_r * 0.55)
        d.ellipse([ex - pr, face_y - pr + 8, ex + pr, face_y + pr + 8], fill=(35, 25, 20))
        d.ellipse([ex - pr // 2, face_y - pr // 2, ex - pr // 8, face_y - pr // 8 + 4], fill="white")
        by = face_y - eye_r * 1.2 - 30
        d.line([(ex - eye_r, by + sx * 0), (ex + eye_r, by - 12)] if sx < 0 else
               [(ex - eye_r, by - 12), (ex + eye_r, by)], fill=_shade(color, 0.35), width=14)
        ck = Image.new("L", (W, H), 0)
        ImageDraw.Draw(ck).ellipse([ex - 70 + sx * 40, face_y + eye_r * 1.4, ex + 70 + sx * 40,
                                    face_y + eye_r * 1.4 + 70], fill=110)
        img.paste((255, 120, 140), (0, 0), ck.filter(ImageFilter.GaussianBlur(18)))
        d = ImageDraw.Draw(img)
    mw, mh = int(bw * 0.30), int(bh * 0.12)
    my = face_y + int(eye_r * 2.3)
    mouth = [cx - mw // 2, my - mh // 2, cx + mw // 2, my + mh // 2]
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, quality=92)
    return {"mouth": [mouth[0] / W, mouth[1] / H, mouth[2] / W, mouth[3] / H], "skin": list(color),
            "procedural": True}
