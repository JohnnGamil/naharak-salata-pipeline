"""Face drawing shared by the video engines: an emotion-shaped animated mouth and eyelid blinks.

Both work on RGBA images and never paint outside the alpha (a cut-out sprite) - an RGB picture
converted to RGBA is simply fully opaque.
"""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

MOUTH_LEVELS = (0.0, 0.35, 0.7, 1.0)
BLINK_LEVELS = (0.0, 0.55, 1.0)


def _ring_color(im: Image.Image, box, below: bool = True) -> tuple[int, int, int]:
    """Median colour just outside a box (the character's skin around the mouth / eyes)."""
    x0, y0, x1, y1 = box
    pad = max(6, (x1 - x0) // 3)
    a = np.asarray(im.convert("RGBA")).astype(np.int32)
    parts = []
    for (l, t, r, b) in ((x0 - pad, y0, x0, y1), (x1, y0, x1 + pad, y1),
                         (x0, y1, x1, y1 + pad) if below else (x0, y0 - pad, x1, y0)):
        l, t, r, b = max(0, l), max(0, t), min(im.width, r), min(im.height, b)
        if r > l and b > t:
            px = a[t:b, l:r].reshape(-1, 4)
            px = px[px[:, 3] > 200][:, :3]
            if len(px):
                parts.append(px)
    if not parts:
        return (220, 180, 60)
    return tuple(int(v) for v in np.median(np.concatenate(parts), axis=0))


def _erase(im: Image.Image, box, skin):
    """Paint the picture's own mouth over with the skin around it; (image, centre x, centre y, width)."""
    im = im.copy()
    x0, y0, x1, y1 = box
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    mw = max(x1 - x0, im.width * 0.12)
    px, py = (x1 - x0) * 0.18, (y1 - y0) * 0.18
    ew, eh = max(mw * 0.62, (x1 - x0) / 2 + px), max(mw * 0.32, (y1 - y0) / 2 + py)
    mask = Image.new("L", im.size, 0)
    ImageDraw.Draw(mask).ellipse([cx - ew, cy - eh, cx + ew, cy + eh], fill=255)
    alpha = im.getchannel("A")
    patch = Image.new("RGBA", im.size, (*skin, 255))
    im.paste(patch, (0, 0), mask.filter(ImageFilter.GaussianBlur(mw * 0.08)))
    im.putalpha(alpha)  # never paint outside the character's silhouette
    return im, cx, cy, mw


SHAPE_LEVEL = {"X": 0.0, "B": 0.35, "A": 0.7, "A2": 1.0}
INSIDE, TEETH, TONGUE = (70, 15, 20), (250, 250, 245), (225, 90, 100)


def draw_shape(im: Image.Image, box, shape: str, emotion: str, skin) -> Image.Image:
    """Mouth for one sound (see visemes.py): X/B/A/A2 are the open levels, M/E/O/F their own shapes."""
    if shape in SHAPE_LEVEL:
        return draw_mouth(im, box, SHAPE_LEVEL[shape], emotion, skin)
    im, cx, cy, mw = _erase(im, box, skin)
    d = ImageDraw.Draw(im)
    dark = tuple(int(c * 0.3) for c in skin)
    lip = tuple(min(255, int(c * 0.75)) for c in skin)
    lw = max(4, int(mw * 0.06))
    if shape == "M":  # lips pressed together: one thick flat line, a touch fuller in the middle
        w = mw * 0.30
        d.rounded_rectangle([cx - w, cy - lw * 0.75, cx + w, cy + lw * 0.75], radius=lw, fill=dark)
        d.ellipse([cx - w * 0.45, cy - lw * 1.0, cx + w * 0.45, cy + lw * 1.0], fill=dark)
    elif shape == "E":  # "ee": wide and flat, teeth showing
        w, h = mw * 0.40, mw * 0.13
        d.rounded_rectangle([cx - w, cy - h, cx + w, cy + h], radius=h, fill=INSIDE, outline=dark, width=lw)
        r = max(2, int(h * 0.35))
        d.rounded_rectangle([cx - w * 0.72, cy - h + lw * 0.8, cx + w * 0.72, cy - lw * 0.2], radius=r, fill=TEETH)
        d.rounded_rectangle([cx - w * 0.55, cy + lw * 0.4, cx + w * 0.55, cy + h - lw * 0.8], radius=r, fill=TEETH)
    elif shape == "O":  # "oo": small and round, lips pushed out
        r = mw * 0.17
        d.ellipse([cx - r * 1.35, cy - r * 1.35, cx + r * 1.35, cy + r * 1.35], fill=lip, outline=dark, width=lw)
        d.ellipse([cx - r * 0.8, cy - r * 0.85, cx + r * 0.8, cy + r * 0.85], fill=INSIDE)
    elif shape == "F":  # "f": upper teeth biting the lower lip
        w, h = mw * 0.34, mw * 0.12
        d.rounded_rectangle([cx - w, cy - h, cx + w, cy + h * 0.6], radius=h * 0.6, fill=INSIDE, outline=dark, width=lw)
        d.rounded_rectangle([cx - w * 0.7, cy - h + lw * 0.8, cx + w * 0.7, cy + h * 0.35],
                            radius=max(2, int(h * 0.3)), fill=TEETH)
        d.rounded_rectangle([cx - w * 0.85, cy + h * 0.15, cx + w * 0.85, cy + h * 0.9], radius=h * 0.4,
                            fill=lip, outline=dark, width=max(2, lw // 2))
    else:
        return draw_mouth(im, box, 0.35, emotion, skin)
    return im


def draw_mouth(im: Image.Image, box, level: float, emotion: str, skin) -> Image.Image:
    """Erase the drawn mouth and paint one whose shape follows the emotion."""
    im, cx, cy, mw = _erase(im, box, skin)
    d = ImageDraw.Draw(im)
    dark = tuple(int(c * 0.3) for c in skin)
    lw = max(4, int(mw * 0.06))
    frown = emotion in ("sad", "angry", "scared")
    if level <= 0.01:
        if emotion == "surprised":  # "o"
            r = mw * 0.22
            d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(70, 15, 20), outline=dark, width=lw)
        elif frown:
            d.arc([cx - mw * 0.36, cy - mw * 0.02, cx + mw * 0.36, cy + mw * 0.34], 200, 340, fill=dark, width=lw)
        elif emotion == "sarcastic":  # one-sided smirk
            d.arc([cx - mw * 0.30, cy - mw * 0.28, cx + mw * 0.42, cy + mw * 0.10], 10, 120, fill=dark, width=lw)
        else:
            d.arc([cx - mw * 0.4, cy - mw * 0.24, cx + mw * 0.4, cy + mw * 0.12], 20, 160, fill=dark, width=lw)
        return im
    if emotion == "surprised":
        oh, ow = mw * (0.40 + 0.45 * level), mw * (0.42 + 0.18 * level)
    elif frown:
        oh, ow = mw * (0.08 + 0.30 * level), mw * (0.50 + 0.20 * level)
    else:
        oh, ow = mw * (0.10 + 0.42 * level), mw * (0.55 + 0.25 * level)
    bm = [cx - ow / 2, cy - oh * 0.40, cx + ow / 2, cy + oh * 0.60]
    if frown:  # "∩": round top, flat bottom reads as upset (the other way round is a grin)
        d.chord([bm[0], bm[1], bm[2], bm[3] + oh], 180, 360, fill=(70, 15, 20), outline=dark, width=lw)
        if level >= 0.35:  # upper teeth peeking under the top lip
            d.chord([cx - ow * 0.3, bm[1] + lw * 0.5, cx + ow * 0.3, bm[1] + oh * 0.55], 180, 360,
                    fill=(250, 250, 245))
        return im
    d.ellipse(bm, fill=(70, 15, 20), outline=dark, width=lw)
    if level >= 0.35:
        d.chord([bm[0] + ow * 0.15, bm[1] + lw * 0.6, bm[2] - ow * 0.15, bm[1] + oh * 0.45], 0, 180,
                fill=(250, 250, 245))
        d.ellipse([cx - ow * 0.28, bm[3] - oh * 0.45, cx + ow * 0.28, bm[3] - lw * 0.5], fill=(225, 90, 100))
    return im


def draw_blink(im: Image.Image, eyes, amount: float) -> Image.Image:
    """Close an eyelid over each eye: skin-coloured, shaded, clipped to the eye's oval, lash line at
    its edge. amount 1 = fully closed."""
    if amount <= 0 or not eyes:
        return im
    im = im.copy()
    alpha = im.getchannel("A")
    # one lid colour for both eyes (sampling each eye separately gave two different oranges)
    rings = np.array([_ring_color(im, e, below=False) for e in eyes], dtype=np.float32)
    skin = tuple(int(v) for v in rings.mean(axis=0))
    for (x0, y0, x1, y1) in eyes:
        w, h = x1 - x0, y1 - y0
        k = -0.04 if amount >= 0.95 else 0.06  # closed lid reaches past the eye white's rim
        ex0, ey0, ex1, ey1 = x0 + w * k, y0 + h * k, x1 - w * k, y1 - h * k
        edge = ey0 + (ey1 - ey0) * amount  # where the lid's lower edge is now
        oval = Image.new("L", im.size, 0)
        ImageDraw.Draw(oval).ellipse([ex0, ey0, ex1, ey1], fill=255)
        lid = Image.new("L", im.size, 0)
        # lid shape: rectangle down to the edge, with a rounded (convex) bottom
        ImageDraw.Draw(lid).rectangle([ex0, ey0 - h, ex1, edge - h * 0.08], fill=255)
        ImageDraw.Draw(lid).ellipse([ex0, edge - h * 0.22, ex1, edge + h * 0.06], fill=255)
        mask = Image.fromarray(np.minimum(np.asarray(oval), np.asarray(lid)))
        mask = mask.filter(ImageFilter.GaussianBlur(max(1.0, w * 0.015)))
        # vertical shading: lighter at the top of the lid, a touch darker near its edge
        grad = np.ones(im.height, dtype=np.float32)
        a0, a1 = int(max(0, ey0)), int(min(im.height, ey1 + 1))
        grad[a0:a1] = np.linspace(1.12, 0.78, a1 - a0)  # round lid: light on top, shadowed at the edge
        grad = grad[:, None]
        shade = (np.array(skin, dtype=np.float32)[None, None, :] * grad[..., None]).clip(0, 255)
        shade = np.broadcast_to(shade, (im.height, im.width, 3)).astype(np.uint8)
        im.paste(Image.fromarray(shade, "RGB"), (0, 0), mask)
        d = ImageDraw.Draw(im)
        dark = tuple(int(c * 0.28) for c in skin)
        lw = max(3, int(w * 0.07))
        if amount >= 0.95:  # closed: a thin curved lash line a bit below the middle
            d.arc([ex0 + w * 0.1, ey0 + (ey1 - ey0) * 0.2, ex1 - w * 0.1, ey0 + (ey1 - ey0) * 0.68], 20, 160,
                  fill=(*dark, 255), width=max(2, int(lw * 0.7)))
        else:
            d.arc([ex0, edge - h * 0.22, ex1, edge + h * 0.06], 20, 160, fill=(*dark, 255), width=max(2, lw // 2))
    im.putalpha(alpha)
    return im
