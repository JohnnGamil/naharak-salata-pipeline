"""YouTube Shorts via the YouTube Data API v3 (resumable upload) + Google OAuth token refresh.

Docs: developers.google.com/youtube/v3/docs/videos/insert, .../guides/uploading_a_video
Free quota: 10,000 units/day, one upload = 1,600 units (~6 uploads/day).
Unverified API projects: uploads are locked to PRIVATE until the project passes Google's audit
(support.google.com/youtube/contact/yt_api_form). The OAuth consent screen must be "In production"
or the refresh token dies after 7 days.
A vertical video up to 3 minutes is published as a Short automatically.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.parse
from pathlib import Path

import httpx

from .base import UploadError, Uploader, UploadResult

log = logging.getLogger(__name__)

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
SCOPES = "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly"
TOKEN_KEY = "youtube_tokens"
CHUNK = 8 * 1024 * 1024  # resumable chunks must be multiples of 256 KiB


def authorize_url(client_id: str, redirect_uri: str, state: str) -> str:
    q = {"client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code", "scope": SCOPES,
         "access_type": "offline", "prompt": "consent", "state": state}
    return f"{AUTH_URL}?{urllib.parse.urlencode(q)}"


class YouTubeTokens:
    def __init__(self, db, client_id: str, client_secret: str):
        self.db, self.cid, self.secret = db, client_id, client_secret

    def get(self) -> dict | None:
        return self.db.get(TOKEN_KEY)

    def _call(self, form: dict) -> dict:
        r = httpx.post(TOKEN_URL, data={"client_id": self.cid, "client_secret": self.secret, **form}, timeout=30)
        data = r.json() if r.content else {}
        if r.status_code != 200 or "access_token" not in data:
            # invalid_grant = refresh token revoked/expired (e.g. consent screen still in "Testing")
            raise UploadError(f"Google token endpoint {r.status_code}: {data}", retryable=r.status_code >= 500)
        return data

    def exchange_code(self, code: str, redirect_uri: str) -> dict:
        data = self._call({"code": code, "grant_type": "authorization_code", "redirect_uri": redirect_uri})
        tok = {"access_token": data["access_token"], "refresh_token": data.get("refresh_token"),
               "expires_at": time.time() + int(data.get("expires_in", 3600))}
        self.db.put(TOKEN_KEY, tok)
        return tok

    def access_token(self) -> str:
        tok = self.get()
        if not tok:
            raise UploadError("YouTube not connected - run scripts/youtube_oauth.py", retryable=False)
        if tok["expires_at"] - 300 > time.time():
            return tok["access_token"]
        if not tok.get("refresh_token"):
            raise UploadError("no YouTube refresh token - run scripts/youtube_oauth.py again", retryable=False)
        data = self._call({"grant_type": "refresh_token", "refresh_token": tok["refresh_token"]})
        tok.update(access_token=data["access_token"], expires_at=time.time() + int(data.get("expires_in", 3600)))
        self.db.put(TOKEN_KEY, tok)
        return tok["access_token"]


class YouTubeUploader(Uploader):
    name = "youtube"

    def __init__(self, tokens: YouTubeTokens, privacy: str = "private", made_for_kids: bool = False,
                 category: str = "24"):
        self.tokens, self.privacy, self.made_for_kids, self.category = tokens, privacy, made_for_kids, category

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.tokens.access_token()}"}

    def channel(self) -> dict:
        r = httpx.get("https://www.googleapis.com/youtube/v3/channels", params={"part": "snippet", "mine": "true"},
                      headers=self._headers(), timeout=30)
        if r.status_code != 200:
            raise UploadError(f"channels {r.status_code}: {r.text[:300]}", retryable=r.status_code >= 500)
        items = r.json().get("items") or []
        return items[0] if items else {}

    @staticmethod
    def metadata(title: str, description: str, tags: list[str], privacy: str, made_for_kids: bool,
                 category: str, short: bool = True) -> dict:
        title = " ".join(title.replace("<", "").replace(">", "").split())
        if short and "#shorts" not in title.lower():
            title = f"{title} #Shorts"
        return {
            "snippet": {"title": title[:100], "description": description[:4900],
                        "tags": [t[:30] for t in tags][:15], "categoryId": category, "defaultLanguage": "ar",
                        "defaultAudioLanguage": "ar"},
            "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": made_for_kids,
                       "containsSyntheticMedia": True},  # AI-generated pictures and voices
        }

    def set_thumbnail(self, video_id: str, cover: Path) -> None:
        """Custom thumbnail (needs a phone-verified channel; YouTube returns 403 otherwise)."""
        r = httpx.post("https://www.googleapis.com/upload/youtube/v3/thumbnails/set",
                       params={"videoId": video_id, "uploadType": "media"}, content=cover.read_bytes(),
                       headers={**self._headers(), "Content-Type": "image/jpeg"}, timeout=120)
        if r.status_code != 200:
            raise UploadError(f"YouTube thumbnail {r.status_code}: {r.text[:300]}", retryable=r.status_code >= 500)

    def upload(self, video: Path, caption: str, title: str = "", tags: list[str] | None = None,
               cover: Path | None = None, short: bool = True) -> UploadResult:
        meta = self.metadata(title or caption.splitlines()[0], caption, tags or [], self.privacy,
                             self.made_for_kids, self.category, short)
        size = video.stat().st_size
        r = httpx.post(f"{UPLOAD_URL}?uploadType=resumable&part=snippet,status",
                       headers={**self._headers(), "Content-Type": "application/json; charset=UTF-8",
                                "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(size)},
                       content=json.dumps(meta, ensure_ascii=False).encode("utf-8"), timeout=60)
        if r.status_code == 403 and "quotaExceeded" in r.text:
            raise UploadError("YouTube daily quota used up (resets at midnight Pacific)", retryable=True)
        if r.status_code != 200 or "location" not in r.headers:
            raise UploadError(f"YouTube init {r.status_code}: {r.text[:400]}", retryable=r.status_code >= 500)
        session = r.headers["location"]
        sent = 0
        with open(video, "rb") as f:
            while sent < size:
                chunk = f.read(CHUNK)
                end = sent + len(chunk) - 1
                for attempt in range(4):
                    try:
                        rr = httpx.put(session, content=chunk, timeout=300,
                                       headers={"Content-Length": str(len(chunk)),
                                                "Content-Range": f"bytes {sent}-{end}/{size}"})
                    except httpx.HTTPError as e:
                        if attempt == 3:
                            raise UploadError(f"YouTube chunk upload: {e}") from e
                        time.sleep(5 * (attempt + 1))
                        continue
                    if rr.status_code in (200, 201):
                        vid = rr.json()["id"]
                        log.info("YouTube upload done: %s", vid)
                        if cover is not None and cover.exists():
                            try:
                                self.set_thumbnail(vid, cover)
                            except UploadError as e:
                                log.warning("youtube thumbnail not set: %s", e)
                        return UploadResult(publish_id=vid, status=self.privacy.upper(),
                                            url=f"https://youtube.com/shorts/{vid}")
                    if rr.status_code == 308:  # chunk accepted, send the next one
                        break
                    if rr.status_code >= 500 and attempt < 3:
                        time.sleep(5 * (attempt + 1))
                        continue
                    raise UploadError(f"YouTube chunk {rr.status_code}: {rr.text[:300]}",
                                      retryable=rr.status_code >= 500)
                sent = end + 1
        raise UploadError("YouTube upload ended without a video id")
