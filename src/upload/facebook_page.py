"""Facebook Page Reels: the same video goes to the Page directly (Instagram API Reels are not
cross-posted, and the IG + Facebook profiles could not be put in one Accounts Center - 2026-10-27).

Uses the Page's own access token (never expires when made from a long-lived user token) and the
Reels publishing flow: start -> upload by file_url (our R2 public link) -> finish/publish.
Docs: developers.facebook.com/docs/video-api/guides/reels-publishing
"""
from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path

import httpx

from . import r2
from .base import UploadError, Uploader, UploadResult

log = logging.getLogger(__name__)

GRAPH = "https://graph.facebook.com/v23.0"
TOKEN_KEY = "facebook_page"  # {"page_id", "page_token", "name"}


class FacebookPageUploader(Uploader):
    name = "facebook"

    def __init__(self, db, settings, poll_s: int = 10, timeout_s: int = 900):
        self.db, self.s, self.poll_s, self.timeout_s = db, settings, poll_s, timeout_s

    def _page(self) -> tuple[str, str]:
        p = self.db.get(TOKEN_KEY)
        if not p:
            raise UploadError("Facebook Page not connected - run scripts/facebook_page_token.py", retryable=False)
        return p["page_id"], p["page_token"]

    @staticmethod
    def _check(r: httpx.Response, what: str) -> dict:
        data = r.json() if r.content else {}
        if r.status_code != 200 or "error" in data:
            err = data.get("error", {})
            raise UploadError(f"Facebook {what} {r.status_code}: {err or r.text[:300]}",
                              retryable=r.status_code >= 500 or err.get("code") in (1, 2, 4, 17, 32, 368))
        return data

    def upload(self, video: Path, caption: str, cover: Path | None = None) -> UploadResult:
        if not r2.enabled(self.s):
            raise UploadError("Facebook Reels need a public video URL: set the R2_* values", retryable=False)
        page_id, token = self._page()
        key = f"fb/{uuid.uuid4().hex}.mp4"
        url = r2.put_public(self.s, video, key)
        try:
            start = self._check(httpx.post(f"{GRAPH}/{page_id}/video_reels", timeout=60,
                                           data={"upload_phase": "start", "access_token": token}), "reels start")
            vid = start["video_id"]
            self._check(httpx.post(f"https://rupload.facebook.com/video-upload/v23.0/{vid}", timeout=300,
                                   headers={"Authorization": f"OAuth {token}", "file_url": url}), "reels upload")
            self._check(httpx.post(f"{GRAPH}/{page_id}/video_reels", timeout=60, data={
                "upload_phase": "finish", "video_id": vid, "video_state": "PUBLISHED",
                "description": caption[:2200], "access_token": token}), "reels publish")
            started = time.time()
            while time.time() - started < self.timeout_s:  # wait so the remote file can be deleted
                try:
                    st = self._check(httpx.get(f"{GRAPH}/{vid}", timeout=30,
                                               params={"fields": "status", "access_token": token}), "status")
                except UploadError as e:
                    # the publish call already succeeded; a status read that Facebook refuses (code 100,
                    # 2026-11-03) must not mark a live reel as failed and ask John for help
                    log.warning("facebook status check failed after publish, assuming live: %s", e)
                    break
                status = st.get("status", {})
                if status.get("video_status") == "ready" or (status.get("publishing_phase") or {}).get("status") == "complete":
                    break
                if status.get("video_status") == "error":
                    raise UploadError(f"Facebook processing error: {status}", retryable=True)
                time.sleep(self.poll_s)
            if cover is not None and cover.exists():
                try:
                    with open(cover, "rb") as f:
                        self._check(httpx.post(f"{GRAPH}/{vid}/thumbnails", timeout=120,
                                               data={"is_preferred": "true", "access_token": token},
                                               files={"source": ("cover.jpg", f, "image/jpeg")}), "thumbnail")
                except UploadError as e:  # the reel is already live; a missing custom cover is not fatal
                    log.warning("facebook cover not set: %s", e)
            log.info("Facebook reel published: %s", vid)
            return UploadResult(publish_id=vid, status="PUBLISHED", url=f"https://www.facebook.com/reel/{vid}")
        finally:
            r2.delete(self.s, key)
