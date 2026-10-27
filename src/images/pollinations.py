"""Character reference images, first source that works:
Pollinations (keyed) -> Cloudflare Workers AI -> Kaggle GPU (Playground v2.5) -> AI Horde -> procedural PIL.

Images are cached per character id so recurring characters look the same across episodes.
"""
from __future__ import annotations

import io
import json
import logging
import random
import shutil
import threading
import time
import urllib.parse
from pathlib import Path

import httpx
from PIL import Image, ImageStat

from ..util import RetryableError, net_retry
from . import procedural
from ..video.base import QuotaWait
from .providers import cloudflare_image, horde_image

log = logging.getLogger(__name__)

STYLE = (
    "3D Pixar style render of a talking {kind} character: {look}. The character IS a real {kind} "
    "(the whole body is the {kind} itself with a cartoon face, small arms and legs), not a human, "
    "not a child, no clothes. Big expressive face in the center of the frame, front view facing "
    "the camera, mouth closed, full body standing on a kitchen counter, warm soft lighting, "
    "vibrant colors, shallow depth of field, vertical composition, highly detailed, no text, "
    "no watermark"
)

# one picture per line of dialogue, so every 2-4 s the viewer sees a new pose / place / camera
SCENE_STYLE = (
    "3D Pixar style animated movie still. A talking {kind} character: {look}. The character IS a real "
    "{kind} (the whole body is the {kind} itself with a cartoon face, small arms and legs), not a human. "
    "Scene: {scene}. Expression: {emotion}. The character is in the CENTER of the frame with its big "
    "expressive face clearly visible and facing the camera, vibrant colors, cinematic lighting, "
    "shallow depth of field, highly detailed. Plain unlabeled objects: no text, no letters, no labels, "
    "no logos, no watermark anywhere in the image"
)

SAFE_SCENE = ("Cute 3D Pixar style cartoon {kind} character with a funny face: {look}. "
              "Mood: {emotion}. In a colorful kitchen, centered, facing the camera, no text")

FACE_PROMPT = (
    "Detect the mouth and each eye of the cartoon character in this image. Return JSON only: a list "
    'of {"box_2d": [ymin, xmin, ymax, xmax], "label": "mouth" | "eye"} with coordinates normalized to '
    "0-1000. Tight boxes: the mouth around the lips, each eye around the white of that ONE eye."
)

_ANON_LOCK = threading.Lock()
_ANON_LAST = [0.0]
ANON_GAP_S = 16  # anonymous tier: ~1 request / 15 s


PICK_PROMPT = (
    "These {n} images are candidate pictures of ONE cartoon character for a kids-friendly TikTok: "
    "a talking {kind} ({look}). Pick the best one: it must clearly BE a {kind} with a cute cartoon "
    "face (eyes + mouth), no human body, no text, not deformed, appealing. Return JSON only: "
    '{{"best": index_from_0, "usable": true_or_false}}'
)


class ImageGen:
    def __init__(self, settings, db, llm=None, kaggle=None):
        self.s, self.db, self.llm, self.kaggle = settings, db, llm, kaggle
        self.cache = settings.cache_dir / "characters"
        self.cache.mkdir(parents=True, exist_ok=True)

    # --- network ------------------------------------------------------------
    @net_retry(attempts=4, max_wait=90)
    def fetch(self, prompt: str, width: int, height: int, seed: int) -> bytes:
        q = {"width": width, "height": height, "seed": seed, "nologo": "true",
             "model": self.s.pollinations_image_model}
        path = urllib.parse.quote(prompt[:900], safe="")
        if self.s.pollinations_key:
            url = f"https://gen.pollinations.ai/image/{path}"
            headers = {"Authorization": f"Bearer {self.s.pollinations_key}"}
        else:
            url = f"https://image.pollinations.ai/prompt/{path}"
            headers = {}
            with _ANON_LOCK:
                wait = ANON_GAP_S - (time.time() - _ANON_LAST[0])
                if wait > 0:
                    time.sleep(wait)
                _ANON_LAST[0] = time.time()
        r = httpx.get(url, params=q, headers=headers, timeout=180, follow_redirects=True)
        if r.status_code == 402:  # anonymous / pollen quota used up: no point retrying
            raise RuntimeError("pollinations quota exhausted (402)")
        if r.status_code in (429, 500, 502, 503, 504, 530):
            raise RetryableError(f"pollinations image {r.status_code}")
        r.raise_for_status()
        if not r.headers.get("content-type", "").startswith("image/"):
            raise RetryableError(f"pollinations returned {r.headers.get('content-type')}")
        self.db.add_quota("pollinations", 1)
        return r.content

    @staticmethod
    def looks_valid(data: bytes, min_side: int = 400) -> Image.Image | None:
        try:
            im = Image.open(io.BytesIO(data)).convert("RGB")
        except Exception:
            return None
        if min(im.size) < min_side:
            return None
        if max(ImageStat.Stat(im).stddev) < 12:  # blank / single-colour error image
            return None
        return im

    # --- mouth location -----------------------------------------------------
    def locate_mouth(self, path: Path) -> dict:
        """Mouth box (+ eye boxes for blinking) of the character in a picture, normalized 0-1.
        One vision call; "guessed" when no mouth was found (the engines then skip the mouth)."""
        face = self.locate_face(Image.open(path))
        meta = {"eyes": face["eyes"]}
        if face["mouth"] is None:
            return {**meta, "mouth": [0.42, 0.50, 0.58, 0.56], "guessed": True}
        x0, y0, x1, y1 = face["mouth"]
        if x1 - x0 > 0.6:
            return {**meta, "mouth": [0.42, 0.50, 0.58, 0.56], "guessed": True}
        if y1 - y0 < 0.02:  # very thin boxes (closed lips) -> give the animated mouth some room
            cy = (y0 + y1) / 2
            y0, y1 = cy - 0.015, cy + 0.015
        return {**meta, "mouth": [x0, y0, x1, y1], "face_found": True}

    def locate_face(self, im: Image.Image) -> dict:
        """{"mouth": [x0,y0,x1,y1] | None, "eyes": [[x0,y0,x1,y1], ...]} normalized."""
        if self.llm is None or not self.llm.gemini_enabled:
            return {"mouth": None, "eyes": []}
        try:
            rgb = Image.new("RGB", im.size, (255, 255, 255))
            rgb.paste(im, (0, 0), im if im.mode == "RGBA" else None)
            rgb.thumbnail((768, 768))
            buf = io.BytesIO()
            rgb.save(buf, "JPEG", quality=88)
            res = self.llm.vision_json([buf.getvalue()], FACE_PROMPT)
            if isinstance(res, dict):  # tolerate {"mouth": [...], "eyes": [[...], ...]}
                items = [{"label": "mouth", "box_2d": res.get("mouth")}]
                items += [{"label": "eye", "box_2d": e} for e in res.get("eyes") or []]
            else:  # Gemini's native list of {"box_2d", "label"}
                items = [x for x in res if isinstance(x, dict)]

            def box(b):
                if not b or len(b) != 4:
                    return None
                y0, x0, y1, x1 = (float(v) / 1000 for v in b)
                ok = 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1 and (x1 - x0) < 0.7
                return [x0, y0, x1, y1] if ok else None
            mouths = [box(x.get("box_2d")) for x in items if "mouth" in str(x.get("label", "")).lower()]
            eyes = [box(x.get("box_2d")) for x in items if "eye" in str(x.get("label", "")).lower()]
            eyes = [e for e in eyes if e]
            # "eyes" as ONE box around both: split it down the middle
            if len(eyes) == 1 and eyes[0][2] - eyes[0][0] > 0.3:
                x0, y0, x1, y1 = eyes[0]
                mid = (x0 + x1) / 2
                eyes = [[x0, y0, mid - 0.01, y1], [mid + 0.01, y0, x1, y1]]
            return {"mouth": next((m for m in mouths if m), None), "eyes": eyes[:2]}
        except Exception as e:
            log.warning("face detection failed: %s", e)
            return {"mouth": None, "eyes": []}

    # --- Kaggle GPU batch ----------------------------------------------------
    def _kaggle_batch(self, chars, job_dir: Path) -> dict[str, Path]:
        """One kernel run for all characters that need a picture; best candidate per character."""
        if self.kaggle is None or not self.s.kaggle_enabled or not chars:
            return {}
        req = [{"id": c.id, "seed": random.randint(1, 2**31 - 1), "prompt": self._prompt(c)} for c in chars]
        try:
            found = self.kaggle.generate_images(req, job_dir / "kaggle_images")
        except Exception as e:
            log.warning("kaggle images failed: %s", e)
            return {}
        best = {}
        for c in chars:
            cands = [p for p in found.get(c.id, []) if self.looks_valid(p.read_bytes())]
            if cands:
                best[c.id] = self._pick(c, cands)
        return best

    def _pick(self, char, cands: list[Path]) -> Path:
        if len(cands) == 1 or self.llm is None or not self.llm.gemini_enabled:
            return cands[0]
        try:
            imgs = []
            for p in cands:
                im = Image.open(p).convert("RGB")
                im.thumbnail((512, 512))
                buf = io.BytesIO()
                im.save(buf, "JPEG", quality=85)
                imgs.append(buf.getvalue())
            kind = char.id.replace("_", " ")
            res = self.llm.vision_json(imgs, PICK_PROMPT.format(n=len(imgs), kind=kind, look=char.look))
            if isinstance(res, list):
                res = res[0] if res else {}
            i = int(res.get("best", 0))
            return cands[i] if 0 <= i < len(cands) else cands[0]
        except Exception as e:
            log.warning("candidate pick failed: %s", e)
            return cands[0]

    # --- public -------------------------------------------------------------
    def _stored(self, char) -> tuple[Path, dict] | None:
        row = self.db.character(char.id)
        if row and row.get("image_path") and Path(row["image_path"]).exists():
            return Path(row["image_path"]), json.loads(row["mouth_json"] or "{}")
        return None

    def _cached(self, char) -> tuple[Path, dict] | None:
        """Stored picture worth reusing; last-resort cartoons and anonymous Pollinations get redrawn."""
        hit = self._stored(char)
        if hit is None:
            return None
        src = hit[1].get("source")
        if src in ("procedural", None) or (src == "pollinations" and not self.s.pollinations_key):
            return None
        return hit

    def _store(self, char, out: Path, meta: dict) -> tuple[Path, dict]:
        self.db.upsert_character(
            char.id, name_ar=char.name, voice=char.voice, look=char.look,
            image_path=str(out), mouth_json=json.dumps(meta),
        )
        return out, meta

    def _prompt(self, char) -> str:
        return STYLE.format(kind=char.id.replace("_", " "), look=char.look)

    def _api_sources(self):
        """(name, fn(char, seed) -> bytes) for the per-picture APIs that are configured."""
        src = []
        if self.s.pollinations_key:  # anonymous tier: ~1 picture/hour and often off-model, so skipped
            src.append(("pollinations", lambda c, seed: self.fetch(self._prompt(c), 768, 1344, seed)))
        if self.s.cloudflare_account_id and self.s.cloudflare_api_token:
            src.append(("cloudflare", lambda c, seed: cloudflare_image(self.s, self._prompt(c), seed)))
        return src

    def _from_apis(self, char, sources, work: Path, candidates: int = 2) -> tuple[Path, str] | None:
        for name, fn in sources:
            cands = []
            for k in range(candidates):
                try:
                    data = fn(char, random.randint(1, 2**31 - 1))
                except Exception as e:
                    log.warning("%s failed for %s: %s", name, char.id, e)
                    break
                im = self.looks_valid(data)
                if im is not None:
                    p = work / f"{char.id}_{name}_{k}.jpg"
                    im.save(p, quality=92)
                    cands.append(p)
            if cands:
                return self._pick(char, cands), name
        return None

    def _horde(self, char, work: Path) -> Path | None:
        from ..video.kaggle_engine import IMAGE_NEGATIVE
        try:
            data = horde_image(self.s, self._prompt(char), IMAGE_NEGATIVE, random.randint(1, 2**31 - 1))
        except Exception as e:
            log.warning("horde failed for %s: %s", char.id, e)
            return None
        im = self.looks_valid(data)
        if im is None:
            return None
        p = work / f"{char.id}_horde.jpg"
        im.save(p, quality=92)
        return p

    def _save_new(self, char, picture: Path | None, source: str) -> tuple[Path, dict]:
        out = self.cache / f"{char.id}.jpg"
        if picture is not None:
            Image.open(picture).convert("RGB").save(out, quality=92)
            meta = self.locate_mouth(out)
            meta["source"] = source
        else:
            log.warning("all image sources failed for %s: drawing procedural character", char.id)
            meta = procedural.draw_character(f"{char.id} {char.look} {char.name}", out,
                                             seed=hash(char.id) & 0xFFFF)
            meta["source"] = "procedural"
        return self._store(char, out, meta)

    def make_pictures(self, chars, job_dir: Path) -> None:
        """Draw new reference pictures for chars, trying every source in order."""
        work = job_dir / "image_candidates"
        work.mkdir(parents=True, exist_ok=True)
        sources = self._api_sources()
        left = []
        for c in chars:
            got = self._from_apis(c, sources, work)
            if got:
                self._save_new(c, got[0], got[1])
            else:
                left.append(c)
        gpu = self._kaggle_batch(left, job_dir)  # one kernel run for everything the APIs missed
        for c in left:
            if c.id in gpu:
                self._save_new(c, gpu[c.id], "kaggle")
            else:
                pic = self._horde(c, work)
                self._save_new(c, pic, "horde")

    def character_image(self, char, force_new: bool = False) -> tuple[Path, dict]:
        """Return (image_path, meta) for a story Character, cached by id."""
        if not force_new and (hit := self._cached(char)):
            return hit
        self.make_pictures([char], self.s.cache_dir / "tmp_pictures")
        return self._stored(char)

    def scene_pictures(self, story, job_dir: Path, refs: dict[str, dict]) -> list[dict]:
        """A fresh picture per scene (Cloudflare only: fast enough for ~10 a video).
        A scene whose picture fails keeps its character's reference picture."""
        out = []
        api = self.s.cloudflare_account_id and self.s.cloudflare_api_token
        for i, sc in enumerate(story.scenes):
            ch = story.char(sc.character_id)
            item = dict(refs[ch.id])
            if api:
                prompt = SCENE_STYLE.format(kind=ch.id.replace("_", " "), look=ch.look,
                                            scene=sc.visual_prompt_en, emotion=sc.emotion)
                try:
                    try:
                        data = cloudflare_image(self.s, prompt)
                    except RuntimeError as e:
                        if "daily free allocation" in str(e):
                            raise QuotaWait("Cloudflare daily picture allowance used up") from e
                        if "NSFW" not in str(e):
                            raise
                        # Workers AI's filter misfires on harmless words ("body", "legs", "crying"):
                        # retry with just the look, the place and the mood
                        data = cloudflare_image(self.s, SAFE_SCENE.format(
                            kind=ch.id.replace("_", " "), look=ch.look, emotion=sc.emotion))
                    im = self.looks_valid(data)
                    if im is None:
                        raise RuntimeError("invalid picture")
                    p = job_dir / "images" / f"scene_{i:02d}.jpg"
                    im.save(p, quality=92)
                    meta = self.locate_mouth(p)
                    meta["source"] = "cloudflare_scene"
                    item = {"path": str(p), "meta": meta}
                except QuotaWait:
                    raise
                except Exception as e:
                    log.warning("scene %d picture failed (%s); using %s's reference picture", i, e, ch.id)
            out.append(item)
        return out

    def copy_into(self, job_dir: Path, story, force_new: bool = False) -> dict[str, dict]:
        """Materialize every character's image into the job dir; returns id -> {path, meta}."""
        need = [c for c in story.characters if force_new or self._cached(c) is None]
        if need:
            self.make_pictures(need, job_dir)
        res = {}
        (job_dir / "images").mkdir(parents=True, exist_ok=True)
        for c in story.characters:
            src, meta = self._stored(c)
            dst = job_dir / "images" / f"{c.id}.jpg"
            shutil.copyfile(src, dst)
            res[c.id] = {"path": str(dst), "meta": meta}
        return res
