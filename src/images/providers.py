"""Extra free text-to-image APIs: Cloudflare Workers AI and AI Horde. Each returns raw image bytes."""
from __future__ import annotations

import base64
import logging
import time

import httpx

from ..util import RetryableError, net_retry

log = logging.getLogger(__name__)

HORDE = "https://aihorde.net/api/v2"
HORDE_AGENT = "ai-tiktok-pipeline:1.0:local"  # Horde asks clients to identify themselves


@net_retry(attempts=3, max_wait=30)
def cloudflare_image(s, prompt: str, seed: int = 0) -> bytes:
    """FLUX schnell on Workers AI (1024x1024; the composer crops it to 9:16 around the centre)."""
    url = (f"https://api.cloudflare.com/client/v4/accounts/{s.cloudflare_account_id}"
           f"/ai/run/{s.cloudflare_image_model}")
    # schnell on Workers AI rejects a "seed" field; each call is already a different picture
    r = httpx.post(url, json={"prompt": prompt[:2000], "steps": 8}, timeout=120,
                   headers={"Authorization": f"Bearer {s.cloudflare_api_token}"})
    if r.status_code == 429 and "daily free allocation" in r.text:  # 10k neurons/day (~40 pictures)
        raise RuntimeError("cloudflare daily free allocation used up")
    if r.status_code == 429 or r.status_code >= 500:
        raise RetryableError(f"cloudflare image {r.status_code}")
    if r.status_code >= 400:  # 4xx incl. daily allowance used up: retrying won't help
        raise RuntimeError(f"cloudflare image {r.status_code}: {r.text[:300]}")
    if r.headers.get("content-type", "").startswith("image/"):
        return r.content
    data = r.json()
    img = (data.get("result") or {}).get("image")
    if not img:
        raise RuntimeError(f"cloudflare returned no image: {str(data)[:300]}")
    return base64.b64decode(img)


def horde_image(s, prompt: str, negative: str, seed: int, width: int = 576, height: int = 1024,
                timeout_s: int = 600) -> bytes:
    """Queue one picture on AI Horde, wait for it, download it."""
    headers = {"apikey": s.horde_api_key or "0000000000", "Client-Agent": HORDE_AGENT}
    body = {
        "prompt": f"{prompt} ### {negative}",
        "params": {"width": width, "height": height, "steps": 25, "cfg_scale": 6.0,
                   "sampler_name": "k_dpmpp_2m", "karras": True, "seed": str(seed), "n": 1},
        "models": [m.strip() for m in s.horde_models.split(",") if m.strip()],
        "nsfw": False, "censor_nsfw": True, "trusted_workers": True, "r2": True,
    }
    r = httpx.post(f"{HORDE}/generate/async", json=body, headers=headers, timeout=60)
    if r.status_code not in (200, 202):
        raise RuntimeError(f"horde submit {r.status_code}: {r.text[:300]}")
    job = r.json()["id"]
    started = time.time()
    try:
        while time.time() - started < timeout_s:
            time.sleep(8)
            chk = httpx.get(f"{HORDE}/generate/check/{job}", headers=headers, timeout=30).json()
            if chk.get("faulted") or not chk.get("is_possible", True):
                raise RuntimeError(f"horde job impossible/faulted: {chk}")
            if chk.get("done"):
                break
        else:
            raise RuntimeError("horde timed out")
        st = httpx.get(f"{HORDE}/generate/status/{job}", headers=headers, timeout=60).json()
        gens = [g for g in st.get("generations", []) if not g.get("censored")]
        if not gens:
            raise RuntimeError("horde returned no (uncensored) image")
        img = gens[0]["img"]
        if img.startswith("http"):
            return httpx.get(img, timeout=120, follow_redirects=True).content
        return base64.b64decode(img)
    except Exception:
        try:  # free the queue slot
            httpx.delete(f"{HORDE}/generate/status/{job}", headers=headers, timeout=20)
        except Exception:
            pass
        raise
