"""Cut a character drawn on a plain white background into an RGBA sprite (classic flood fill, no AI).

The puppet engine animates the sprite over a separate background picture, so the face never melts
the way the GPU video models did. A picture whose border is not plain white returns None and the
scene falls back to animating the whole picture.
"""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

SENTINEL = (255, 0, 254)


def border_is_plain(im: Image.Image, min_white: float = 0.75) -> bool:
    a = np.asarray(im.convert("RGB")).astype(np.int16)
    edge = np.concatenate([a[0], a[-1], a[:, 0], a[:, -1]])
    return float(((edge > 225).all(axis=1)).mean()) >= min_white


def cut_white(im: Image.Image, thresh: int = 42, feather: float = 1.2) -> Image.Image | None:
    """RGBA sprite cropped to the character, or None when the background isn't plain white."""
    rgb = im.convert("RGB")
    if not border_is_plain(rgb):
        return None
    work = rgb.copy()
    w, h = work.size
    seeds = [(x, y) for x in range(0, w, max(1, w // 24)) for y in (0, h - 1)]
    seeds += [(x, y) for y in range(0, h, max(1, h // 24)) for x in (0, w - 1)]
    px = work.load()
    for xy in seeds:
        r, g, b = px[xy]
        if min(r, g, b) > 215 and px[xy] != SENTINEL:
            ImageDraw.floodfill(work, xy, SENTINEL, thresh=thresh)
    a = np.asarray(work).astype(np.int16)
    bg = (a[..., 0] == SENTINEL[0]) & (a[..., 1] == SENTINEL[1]) & (a[..., 2] == SENTINEL[2])
    # the generator paints a pale grey floor shadow under the feet (and white gaps between the legs)
    # that the fill can't reach: in the lower part of the character, light low-colour pixels are floor
    fg_rows = np.nonzero((~bg).any(axis=1))[0]
    if len(fg_rows):
        top, bottom = fg_rows[0], fg_rows[-1]
        cut_from = int(top + (bottom - top) * 0.68)
        o = np.asarray(rgb).astype(np.int16)
        pale = (o.min(axis=2) > 170) & ((o.max(axis=2) - o.min(axis=2)) < 28)
        bg[cut_from:] |= pale[cut_from:]
    fg_ratio = 1 - bg.mean()
    if not 0.04 < fg_ratio < 0.9:  # nothing found, or the fill leaked into the character
        return None
    alpha = Image.fromarray(np.where(bg, 0, 255).astype(np.uint8))
    # shave the 1-2 px white fringe the fill leaves at the outline, then soften the edge
    alpha = alpha.filter(ImageFilter.MinFilter(5)).filter(ImageFilter.GaussianBlur(feather))
    out = rgb.copy()
    out.putalpha(alpha)
    box = alpha.point(lambda v: 255 if v > 20 else 0).getbbox()
    return out.crop(box) if box else None


def cut_plain(im: Image.Image, thresh: int = 38, feather: float = 1.2) -> Image.Image | None:
    """Like cut_white for a picture on any plain studio background (flat grey, dark gradient):
    flood fill from the border with the border's own colours."""
    white = cut_white(im, feather=feather)
    if white is not None:
        return white
    rgb = im.convert("RGB")
    work = rgb.copy()
    w, h = work.size
    px = work.load()
    seeds = [(x, y) for x in range(0, w, max(1, w // 32)) for y in (0, h - 1)]
    seeds += [(x, y) for y in range(0, h, max(1, h // 32)) for x in (0, w - 1)]
    for xy in seeds:
        if px[xy] != SENTINEL:
            ImageDraw.floodfill(work, xy, SENTINEL, thresh=thresh)
    a = np.asarray(work).astype(np.int16)
    bg = (a[..., 0] == SENTINEL[0]) & (a[..., 1] == SENTINEL[1]) & (a[..., 2] == SENTINEL[2])
    if not 0.04 < 1 - bg.mean() < 0.9:
        return None
    alpha = Image.fromarray(np.where(bg, 0, 255).astype(np.uint8))
    alpha = alpha.filter(ImageFilter.MinFilter(5)).filter(ImageFilter.GaussianBlur(feather))
    out = rgb.copy()
    out.putalpha(alpha)
    box = alpha.point(lambda v: 255 if v > 20 else 0).getbbox()
    return out.crop(box) if box else None
