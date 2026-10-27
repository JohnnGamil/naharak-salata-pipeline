"""Kaggle GPU engine: push one kernel per video, poll, download clips.

Commands used (Kaggle CLI 2.x): kernels push -p DIR / kernels status USER/SLUG /
kernels output USER/SLUG -p DIR -o
"""
from __future__ import annotations

import base64
import io
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from PIL import Image

from ..config import ROOT
from .base import EngineUnavailable, SceneSpec, VideoEngine

log = logging.getLogger(__name__)

TEMPLATES = ROOT / "kaggle_job"
GPU_SERVICE = "kaggle_gpu_h"
WEEK = 7 * 24 * 3600

NEGATIVE = ("worst quality, blurry, jittery, distorted, deformed face, extra limbs, melting, "
            "text, watermark, human, person")
PIP = ["diffusers>=0.35", "transformers", "accelerate", "sentencepiece", "imageio", "imageio-ffmpeg",
       "hf_transfer", "ftfy"]

# Playground v2.5 is SDXL-sized (fits a T4 in fp16) and much better at cute 3D characters than SDXL base
IMAGE_MODELS = ["playgroundai/playground-v2.5-1024px-aesthetic", "stabilityai/stable-diffusion-xl-base-1.0"]
IMAGE_PIP = ["diffusers>=0.33", "transformers", "accelerate", "hf_transfer"]
IMAGE_NEGATIVE = ("human, person, child, clothes, text, watermark, logo, deformed, extra limbs, "
                  "blurry, low quality, multiple characters")


def kaggle_bin() -> list[str]:
    exe = shutil.which("kaggle")
    if exe:
        return [exe]
    return [sys.executable, "-m", "kaggle"]


class KaggleEngine(VideoEngine):
    name = "kaggle"

    def __init__(self, settings, db, width: int = 512, height: int = 896, fps: int = 24,
                 clip_s: float = 5.0, steps: int = 30, guidance: float = 3.0, poll_s: int = 30):
        self.s, self.db = settings, db
        self.width, self.height, self.fps, self.clip_s = width, height, fps, clip_s
        self.steps, self.guidance, self.poll_s = steps, guidance, poll_s

    # --- CLI ----------------------------------------------------------------
    def _env(self) -> dict:
        env = dict(os.environ)
        env["KAGGLE_USERNAME"] = self.s.kaggle_username
        if self.s.kaggle_key:
            env["KAGGLE_KEY"] = self.s.kaggle_key
        if self.s.kaggle_api_token:
            env["KAGGLE_API_TOKEN"] = self.s.kaggle_api_token
        env["PYTHONIOENCODING"] = "utf-8"
        return env

    def cli(self, *args: str, timeout: int = 600) -> str:
        p = subprocess.run([*kaggle_bin(), *args], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout, env=self._env())
        out = (p.stdout or "") + (p.stderr or "")
        if p.returncode != 0:
            raise RuntimeError(f"kaggle {' '.join(args[:2])} failed: {out[-800:]}")
        return out

    @property
    def kernel_ref(self) -> str:
        return f"{self.s.kaggle_username}/{self.s.kaggle_kernel_slug}"

    def status(self, ref: str | None = None) -> str:
        out = self.cli("kernels", "status", ref or self.kernel_ref, timeout=120).lower()
        for word in ("complete", "error", "cancel", "running", "queued", "new"):
            if word in out:
                return word
        return out.strip()[-80:]

    # --- quota --------------------------------------------------------------
    def hours_used_week(self) -> float:
        return self.db.quota_since(GPU_SERVICE, time.time() - WEEK)

    def check_available(self, n_scenes: int) -> None:
        if not self.s.kaggle_enabled:
            raise EngineUnavailable("Kaggle credentials not configured")
        est_h = (8 + 4 * n_scenes) / 60  # startup + per-scene estimate on a T4
        used = self.hours_used_week()
        if used + est_h > self.s.kaggle_weekly_gpu_hours_budget:
            raise EngineUnavailable(f"Kaggle GPU budget: {used:.1f}h used of "
                                    f"{self.s.kaggle_weekly_gpu_hours_budget}h this week")

    # --- build --------------------------------------------------------------
    @staticmethod
    def _image_b64(path: Path, max_side: int = 896) -> str:
        im = Image.open(path).convert("RGB")
        im.thumbnail((max_side, max_side))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=88)
        return base64.b64encode(buf.getvalue()).decode()

    def build(self, scenes: list[SceneSpec], kdir: Path, seed: int, budget_s: int) -> Path:
        kdir.mkdir(parents=True, exist_ok=True)
        wan = "wan" in self.s.kaggle_video_model.lower()
        kind = ("vace" if "vace" in self.s.kaggle_video_model.lower() else "wan") if wan else "ltx"
        if wan:  # Wan 2.x: sides multiple of 32 (16 for 1.3B is fine too), 4k+1 frames, CFG ~5
            width, height, step, guidance = 480, 832, 4, 5.0
        else:  # LTX-Video wants 8k+1 frames
            width, height, step, guidance = self.width, self.height, 8, self.guidance
        frames = int(self.clip_s * self.fps) // step * step + 1
        job = {
            "model": self.s.kaggle_video_model, "pipeline": kind, "pip": PIP,
            "offload": os.environ.get("KAGGLE_OFFLOAD", "model"),  # "group" filled Kaggle's 30 GB RAM
            "width": width, "height": height, "fps": self.fps, "steps": self.steps, "guidance": guidance,
            "negative_prompt": NEGATIVE, "time_budget_s": budget_s,
            "scenes": [{
                "index": s.index, "frames": frames, "seed": seed * 100 + s.index,
                "prompt": (f"{s.prompt}. The cartoon character talks expressively to the camera, "
                           f"{s.emotion} expression, smooth natural motion, 3D Pixar animation style"),
                "image_b64": self._image_b64(s.image),
            } for s in scenes],
        }
        code = (TEMPLATES / "generate.py.tmpl").read_text(encoding="utf-8")
        code = code.replace("__JOB_B64__", base64.b64encode(json.dumps(job).encode()).decode())
        (kdir / "generate.py").write_text(code, encoding="utf-8")
        sources = [self.s.kaggle_weights_kernel] if self.s.kaggle_weights_kernel else []
        meta = (TEMPLATES / "kernel-metadata.json.tmpl").read_text(encoding="utf-8")
        meta = (meta.replace("__USERNAME__", self.s.kaggle_username)
                .replace("__SLUG__", self.s.kaggle_kernel_slug)
                .replace("__KERNEL_SOURCES__", json.dumps(sources)))
        (kdir / "kernel-metadata.json").write_text(meta, encoding="utf-8")
        return kdir

    # --- run ----------------------------------------------------------------
    def generate(self, scenes: list[SceneSpec], out_dir: Path, seed: int = 0) -> list[Path | None]:
        """Returns a clip per scene, or None for scenes the GPU job failed on."""
        self.check_available(len(scenes))
        timeout_s = self.s.kaggle_timeout_min * 60
        kdir = self.build(scenes, out_dir.parent / "kaggle", seed, budget_s=int(timeout_s * 0.8))
        state, res_dir, result = self.run_kernel(kdir, self.kernel_ref, timeout_s, out_dir.parent / "kaggle_out")
        out_dir.mkdir(parents=True, exist_ok=True)
        clips: list[Path | None] = []
        for s in scenes:
            info = result.get("scenes", {}).get(str(s.index), {})
            found = list(res_dir.rglob(info.get("file", f"scene_{s.index:02d}.mp4"))) if info.get("ok") else []
            if found:
                dst = out_dir / f"scene_{s.index:02d}.mp4"
                shutil.copyfile(found[0], dst)
                clips.append(dst)
            else:
                log.warning("kaggle scene %d missing: %s", s.index, info.get("error", "no output"))
                clips.append(None)
        if not any(clips):
            raise EngineUnavailable(f"Kaggle produced no clips (state={state}, fatal={result.get('fatal')})")
        return clips

    def run_kernel(self, kdir: Path, ref: str, timeout_s: int, res_dir: Path) -> tuple[str, Path, dict]:
        """Push kdir, poll until it ends, download its output. Returns (state, res_dir, result.json)."""
        if self.status_safe(ref) in ("running", "queued"):
            raise EngineUnavailable(f"previous Kaggle run of {ref} still active")
        log.info("pushing Kaggle kernel %s", ref)
        out = self.cli("kernels", "push", "-p", str(kdir), "-t", str(timeout_s), timeout=300)
        if "error" in out.lower() and "successfully" not in out.lower():
            raise RuntimeError(f"kaggle push: {out[-500:]}")
        started = time.time()
        time.sleep(20)
        state = "queued"
        try:
            while time.time() - started < timeout_s + 600:
                state = self.status_safe(ref)
                if state in ("complete", "error", "cancel"):
                    break
                time.sleep(self.poll_s)
            else:
                raise EngineUnavailable("Kaggle run timed out")
        finally:
            hours = (time.time() - started) / 3600
            self.db.add_quota(GPU_SERVICE, hours, f"{ref} state={state}")
            self._quota_note()
        shutil.rmtree(res_dir, ignore_errors=True)
        res_dir.mkdir(parents=True)
        self.cli("kernels", "output", ref, "-p", str(res_dir), "-o", "-q", timeout=900)
        result = {}
        for cand in res_dir.rglob("result.json"):
            result = json.loads(cand.read_text(encoding="utf-8"))
            break
        if result.get("fatal") or state != "complete":
            log.warning("kaggle %s state=%s fatal=%s log=%s", ref, state, result.get("fatal"),
                        "\n".join(result.get("log", [])[-10:]))
        return state, res_dir, result

    def status_safe(self, ref: str | None = None) -> str:
        try:
            return self.status(ref)
        except Exception as e:
            log.warning("kaggle status failed: %s", e)
            return "unknown"

    def _quota_note(self):
        used, budget = self.hours_used_week(), self.s.kaggle_weekly_gpu_hours_budget
        if used > 0.8 * budget:
            self.db.put("quota_warning", f"Kaggle GPU {used:.1f}h of {budget}h used in the last 7 days")

    # --- character pictures (when Pollinations is down) ----------------------------
    def generate_images(self, chars: list[dict], out_dir: Path, candidates: int = 2,
                        slug: str = "tiktok-imagegen", size: tuple[int, int] = (768, 1344)) -> dict[str, list[Path]]:
        """chars: [{"id", "prompt", "seed"}] -> {id: [candidate image paths]} (missing ids failed)."""
        if not self.s.kaggle_enabled:
            raise EngineUnavailable("Kaggle credentials not configured")
        self.check_available(0)
        kdir = out_dir.parent / "kaggle_img"
        kdir.mkdir(parents=True, exist_ok=True)
        job = {"models": IMAGE_MODELS, "pip": IMAGE_PIP, "width": size[0], "height": size[1], "steps": 30,
               "guidance": 3.0, "candidates": candidates, "negative_prompt": IMAGE_NEGATIVE,
               "chars": chars}
        code = (TEMPLATES / "images.py.tmpl").read_text(encoding="utf-8")
        (kdir / "images.py").write_text(
            code.replace("__JOB_B64__", base64.b64encode(json.dumps(job).encode()).decode()), encoding="utf-8")
        meta = json.loads((TEMPLATES / "kernel-metadata.json.tmpl").read_text(encoding="utf-8")
                          .replace("__USERNAME__", self.s.kaggle_username).replace("__SLUG__", slug)
                          .replace("__KERNEL_SOURCES__", "[]"))
        meta["code_file"] = "images.py"
        (kdir / "kernel-metadata.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
        ref = f"{self.s.kaggle_username}/{slug}"
        _, res_dir, result = self.run_kernel(kdir, ref, 40 * 60, out_dir.parent / "kaggle_img_out")
        out_dir.mkdir(parents=True, exist_ok=True)
        found: dict[str, list[Path]] = {}
        for cid, info in result.get("chars", {}).items():
            for name in info.get("files", []) if info.get("ok") else []:
                for src in res_dir.rglob(name):
                    dst = out_dir / name
                    shutil.copyfile(src, dst)
                    found.setdefault(cid, []).append(dst)
                    break
        log.info("kaggle images (%s): %s", result.get("model"), {k: len(v) for k, v in found.items()})
        return found

    # --- background removal (CPU kernel, no GPU quota) ------------------------------
    def cutout_images(self, images: dict[str, Path], out_dir: Path, slug: str = "tiktok-cutout",
                      model: str = "isnet-general-use") -> dict[str, Path]:
        """{name: picture} -> {name: RGBA png} with rembg, for pictures on a busy background."""
        if not self.s.kaggle_enabled:
            raise EngineUnavailable("Kaggle credentials not configured")
        kdir = out_dir.parent / f"{out_dir.name}_kernel"
        kdir.mkdir(parents=True, exist_ok=True)
        # the pictures travel inside the script, and Kaggle rejects big scripts (400 on push)
        job = {"model": model, "images": {k: self._image_b64(Path(v), max_side=900) for k, v in images.items()}}
        code = (TEMPLATES / "cutout.py.tmpl").read_text(encoding="utf-8")
        (kdir / "cutout.py").write_text(
            code.replace("__JOB_B64__", base64.b64encode(json.dumps(job).encode()).decode()), encoding="utf-8")
        meta = json.loads((TEMPLATES / "kernel-metadata.json.tmpl").read_text(encoding="utf-8")
                          .replace("__USERNAME__", self.s.kaggle_username).replace("__SLUG__", slug)
                          .replace("__KERNEL_SOURCES__", "[]"))
        meta.update(code_file="cutout.py", enable_gpu="false")
        meta.pop("machine_shape", None)
        (kdir / "kernel-metadata.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
        _, res_dir, result = self.run_kernel(kdir, f"{self.s.kaggle_username}/{slug}", 30 * 60,
                                             out_dir.parent / f"{out_dir.name}_out")
        out_dir.mkdir(parents=True, exist_ok=True)
        got = {}
        for name, info in result.get("items", {}).items():
            for src in res_dir.rglob(info.get("file", "-")) if info.get("ok") else []:
                dst = out_dir / src.name
                shutil.copyfile(src, dst)
                got[name] = dst
                break
        return got

    # --- one-time weights kernel ------------------------------------------------
    def push_weights_kernel(self, slug: str = "tiktok-clipgen-weights") -> str:
        kdir = self.s.data_dir / "kaggle_weights"
        kdir.mkdir(parents=True, exist_ok=True)
        code = (TEMPLATES / "weights.py.tmpl").read_text(encoding="utf-8").replace(
            "__MODEL__", self.s.kaggle_video_model)
        (kdir / "weights.py").write_text(code, encoding="utf-8")
        meta = json.loads((TEMPLATES / "kernel-metadata.json.tmpl").read_text(encoding="utf-8")
                          .replace("__USERNAME__", self.s.kaggle_username).replace("__SLUG__", slug)
                          .replace("__KERNEL_SOURCES__", "[]"))
        meta.update(code_file="weights.py", enable_gpu="false")
        meta.pop("machine_shape", None)
        (kdir / "kernel-metadata.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
        self.cli("kernels", "push", "-p", str(kdir), timeout=300)
        return f"{self.s.kaggle_username}/{slug}"

