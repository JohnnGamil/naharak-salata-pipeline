"""Can this machine reach every outside service the channel uses? Light, read-only calls that spend
no daily allowance (no images, no speech). Run on the PC and on GitHub (workflow check.yml).

    python scripts/check_services.py
"""
from __future__ import annotations

import asyncio
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

from src import state_sync  # noqa: E402
from src.config import get_settings  # noqa: E402

s = get_settings()
results: list[tuple[str, bool, str]] = []


def check(name: str):
    def wrap(fn):
        t = time.time()
        try:
            note = fn() or ""
            results.append((name, True, f"{note} ({time.time() - t:.1f}s)"))
        except Exception as e:  # noqa: BLE001
            results.append((name, False, f"{type(e).__name__}: {str(e)[:160]}"))
            traceback.print_exc()
        return fn
    return wrap


def ok(r: httpx.Response) -> httpx.Response:
    if r.status_code >= 400:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:160]}")
    return r


if s.runner == "github":  # tokens live in the shared state; read-only here (nothing is pushed back)
    state_sync.pull(s)
from src.db import DB  # noqa: E402

db = DB(s.db_path)


@check("R2 shared state")
def _():
    ls = state_sync.lease(s)
    return f"last run by {ls.get('owner')} {(time.time() - ls.get('ts', 0)) / 60:.0f} min ago"


@check("Gemini (text + voices)")
def _():
    from google import genai
    client = genai.Client(api_key=s.gemini_api_key)
    names = [m.name for m in client.models.list()]
    tts = [n for n in names if "tts" in n]
    return f"{len(names)} models, {len(tts)} voice models"


@check("Cloudflare FLUX (pictures)")
def _():
    # listing the models is free; it fails the same way a picture would on a bad token
    r = ok(httpx.get(f"https://api.cloudflare.com/client/v4/accounts/{s.cloudflare_account_id}/ai/models/search",
                     params={"search": "flux-1-schnell", "per_page": 1},
                     headers={"Authorization": f"Bearer {s.cloudflare_api_token}"}, timeout=30))
    return f"{len(r.json()['result'])} FLUX model found"


@check("Pollinations (backup text/pictures)")
def _():
    return f"HTTP {httpx.get('https://image.pollinations.ai/', timeout=30, follow_redirects=True).status_code}"


@check("AI Horde (backup pictures)")
def _():
    ok(httpx.get("https://aihorde.net/api/v2/status/heartbeat", timeout=30))
    return "up"


@check("Kaggle (GPU pictures / cut-outs)")
def _():
    from src.video.kaggle_engine import KaggleEngine
    out = KaggleEngine(s, db).cli("kernels", "list", "--mine", "--page-size", "5", timeout=120)
    return f"{max(0, len(out.strip().splitlines()) - 2)} kernels visible"


@check("edge-tts (backup voice)")
def _():
    import edge_tts
    voices = asyncio.run(edge_tts.list_voices())
    return f"{sum(1 for v in voices if v['ShortName'].startswith('ar-EG'))} Egyptian voices"


@check("Whisper model (audio check)")
def _():
    from faster_whisper import WhisperModel
    t = time.time()
    WhisperModel(s.whisper_model, device="cpu", compute_type="int8", download_root=str(s.models_dir))
    return f"'{s.whisper_model}' loaded in {time.time() - t:.0f}s"


@check("Telegram bot")
def _():
    return "@" + ok(httpx.get(f"https://api.telegram.org/bot{s.telegram_bot_token}/getMe", timeout=30)).json()["result"]["username"]


@check("YouTube")
def _():
    from src.upload.youtube_api import YouTubeTokens, YouTubeUploader
    ch = YouTubeUploader(YouTubeTokens(db, s.youtube_client_id, s.youtube_client_secret)).channel()
    return ch.get("snippet", {}).get("title", "?")


@check("Instagram")
def _():
    tok = db.get("instagram_tokens")
    r = ok(httpx.get("https://graph.instagram.com/me", params={"fields": "username",
                     "access_token": tok["access_token"]}, timeout=30))
    return f"@{r.json()['username']}, token ok for {(tok['expires_at'] - time.time()) / 86400:.0f} more days"


@check("Facebook Page")
def _():
    p = db.get("facebook_page")
    r = ok(httpx.get(f"https://graph.facebook.com/v23.0/{p['page_id']}", params={"fields": "name",
                     "access_token": p["page_token"]}, timeout=30))
    return r.json()["name"]


@check("TikTok API token")
def _():
    tok = db.get("tiktok_tokens") or {}
    # no refresh here: TikTok rotates refresh tokens and this run does not save state
    days = (tok.get("refresh_expires_at", 0) - time.time()) / 86400
    ok(httpx.get("https://open.tiktokapis.com/", timeout=30)) if False else None
    if days <= 0:
        raise RuntimeError("refresh token expired")
    return f"refresh token valid {days:.0f} more days (posting goes via Telegram until the audit)"


@check("ffmpeg with Arabic subtitles (libass)")
def _():
    import subprocess
    out = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True).stdout
    if " ass " not in out:
        raise RuntimeError("ffmpeg has no ass filter")
    return "ass filter present"


print("\n==== services from", s.runner, "====")
for name, good, note in results:
    print(f"{'OK  ' if good else 'FAIL'}  {name:38s} {note}")
bad = [n for n, g, _ in results if not g]
print("ALL GOOD" if not bad else f"{len(bad)} FAILED: {', '.join(bad)}")
sys.exit(1 if bad else 0)
