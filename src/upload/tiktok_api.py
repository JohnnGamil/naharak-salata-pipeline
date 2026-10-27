"""TikTok Content Posting API (Direct Post, chunked FILE_UPLOAD) + OAuth token refresh.

Docs: developers.tiktok.com/doc/content-posting-api-reference-direct-post
      developers.tiktok.com/doc/content-posting-api-media-transfer-guide
Unaudited apps: posts are SELF_ONLY (private) and the TikTok account must be private.
"""
from __future__ import annotations

import hashlib
import logging
import secrets
import time
import urllib.parse
from pathlib import Path

import httpx

from .base import UploadError, Uploader, UploadResult

log = logging.getLogger(__name__)

API = "https://open.tiktokapis.com"
AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
SCOPES = "user.info.basic,video.publish,video.upload"
TOKEN_KEY = "tiktok_tokens"
MB = 1024 * 1024
CHUNK = 10 * MB  # 5-64 MB allowed; files under 5 MB go up as one chunk


def chunk_plan(size: int, chunk: int = CHUNK) -> tuple[int, int]:
    """(chunk_size, total_chunk_count) per the media transfer guide (last chunk absorbs the rest)."""
    if size < 5 * MB or size <= chunk:
        return size, 1
    return chunk, size // chunk


class TokenStore:
    """Access + refresh tokens in the DB kv table, refreshed before expiry."""

    def __init__(self, db, client_key: str, client_secret: str):
        self.db, self.key, self.secret = db, client_key, client_secret

    def get(self) -> dict | None:
        return self.db.get(TOKEN_KEY)

    def save(self, data: dict) -> dict:
        now = time.time()
        tok = {
            "access_token": data["access_token"],
            "refresh_token": data.get("refresh_token"),
            "open_id": data.get("open_id"),
            "scope": data.get("scope"),
            "expires_at": now + int(data.get("expires_in", 86400)),
            "refresh_expires_at": now + int(data.get("refresh_expires_in", 365 * 86400)),
        }
        self.db.put(TOKEN_KEY, tok)
        return tok

    def _token_call(self, form: dict) -> dict:
        r = httpx.post(f"{API}/v2/oauth/token/", data={"client_key": self.key, "client_secret": self.secret, **form},
                       headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=30)
        data = r.json() if r.content else {}
        if r.status_code != 200 or "access_token" not in data:
            raise UploadError(f"token endpoint {r.status_code}: {data}", retryable=r.status_code >= 500)
        return data

    def exchange_code(self, code: str, redirect_uri: str, code_verifier: str | None = None) -> dict:
        form = {"code": code, "grant_type": "authorization_code", "redirect_uri": redirect_uri}
        if code_verifier:
            form["code_verifier"] = code_verifier
        return self.save(self._token_call(form))

    def refresh(self) -> dict:
        tok = self.get()
        if not tok or not tok.get("refresh_token"):
            raise UploadError("no TikTok refresh token - run scripts/tiktok_oauth.py", retryable=False)
        if tok.get("refresh_expires_at", 0) < time.time():
            raise UploadError("TikTok refresh token expired - run scripts/tiktok_oauth.py again", retryable=False)
        data = self._token_call({"grant_type": "refresh_token", "refresh_token": tok["refresh_token"]})
        log.info("TikTok access token refreshed")
        return self.save(data)

    def access_token(self, margin_s: int = 1800) -> str:
        tok = self.get()
        if not tok:
            raise UploadError("TikTok not connected - run scripts/tiktok_oauth.py", retryable=False)
        if tok["expires_at"] - margin_s < time.time():
            tok = self.refresh()
        return tok["access_token"]


def authorize_url(client_key: str, redirect_uri: str, pkce: bool = False) -> tuple[str, str, str | None]:
    """Returns (url, state, code_verifier)."""
    state = secrets.token_urlsafe(16)
    q = {"client_key": client_key, "scope": SCOPES, "response_type": "code",
         "redirect_uri": redirect_uri, "state": state}
    verifier = None
    if pkce:
        verifier = secrets.token_urlsafe(64)[:96]
        # TikTok desktop flow uses hex(SHA256(verifier)) as the challenge
        q["code_challenge"] = hashlib.sha256(verifier.encode()).hexdigest()
        q["code_challenge_method"] = "S256"
    return f"{AUTH_URL}?{urllib.parse.urlencode(q)}", state, verifier


class TikTokUploader(Uploader):
    name = "tiktok"

    def __init__(self, tokens: TokenStore, privacy_level: str = "SELF_ONLY", is_aigc: bool = True,
                 poll_s: int = 15, max_poll_s: int = 900):
        self.tokens, self.privacy, self.is_aigc = tokens, privacy_level, is_aigc
        self.poll_s, self.max_poll_s = poll_s, max_poll_s

    def _post(self, path: str, body: dict) -> dict:
        for attempt in range(5):
            tok = self.tokens.access_token()
            r = httpx.post(f"{API}{path}", json=body, timeout=60,
                           headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json; charset=UTF-8"})
            data = r.json() if r.content else {}
            code = (data.get("error") or {}).get("code", "ok")
            if r.status_code == 401 or code in ("access_token_invalid", "token_expired"):
                self.tokens.refresh()
                continue
            if r.status_code == 429 or code == "rate_limit_exceeded":
                time.sleep(15 * (attempt + 1))
                continue
            if r.status_code >= 500:
                time.sleep(5 * 2 ** attempt)
                continue
            if code != "ok":
                retry = code in ("internal_error",)
                raise UploadError(f"{path}: {code} - {(data.get('error') or {}).get('message')}", retryable=retry)
            return data.get("data", {})
        raise UploadError(f"{path}: gave up after retries")

    def creator_info(self) -> dict:
        return self._post("/v2/post/publish/creator_info/query/", {})

    def _put_chunks(self, url: str, video: Path, chunk_size: int, count: int) -> None:
        size = video.stat().st_size
        with open(video, "rb") as f:
            for i in range(count):
                start = i * chunk_size
                end = size - 1 if i == count - 1 else start + chunk_size - 1
                f.seek(start)
                blob = f.read(end - start + 1)
                for attempt in range(5):
                    try:
                        r = httpx.put(url, content=blob, timeout=300, headers={
                            "Content-Type": "video/mp4", "Content-Length": str(len(blob)),
                            "Content-Range": f"bytes {start}-{end}/{size}"})
                        if r.status_code in (200, 201, 206):
                            break
                        log.warning("chunk %d/%d -> %s %s", i + 1, count, r.status_code, r.text[:200])
                    except httpx.HTTPError as e:
                        log.warning("chunk %d upload error: %s", i + 1, e)
                    time.sleep(3 * 2 ** attempt)
                else:
                    raise UploadError(f"chunk {i + 1}/{count} failed")

    def upload(self, video: Path, caption: str) -> UploadResult:
        info = self.creator_info()
        options = info.get("privacy_level_options") or []
        privacy = self.privacy if self.privacy in options else ("SELF_ONLY" if "SELF_ONLY" in options or not options
                                                                 else options[0])
        max_dur = info.get("max_video_post_duration_sec")
        size = video.stat().st_size
        chunk_size, count = chunk_plan(size)
        body = {
            "post_info": {
                "title": caption[:2200], "privacy_level": privacy,
                "disable_comment": bool(info.get("comment_disabled", False)),
                "disable_duet": bool(info.get("duet_disabled", False)),
                "disable_stitch": bool(info.get("stitch_disabled", False)),
                # the video starts with 3 frames of our designed cover (see compose.thumbnail.cover_intro)
                "video_cover_timestamp_ms": 0, "is_aigc": self.is_aigc,
            },
            "source_info": {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk_size,
                            "total_chunk_count": count},
        }
        log.info("TikTok init: %d bytes, %d chunk(s), privacy=%s, max_dur=%s", size, count, privacy, max_dur)
        data = self._post("/v2/post/publish/video/init/", body)
        publish_id, upload_url = data["publish_id"], data["upload_url"]
        self._put_chunks(upload_url, video, chunk_size, count)
        deadline = time.time() + self.max_poll_s
        status = "PROCESSING_UPLOAD"
        while time.time() < deadline:
            time.sleep(self.poll_s)
            st = self._post("/v2/post/publish/status/fetch/", {"publish_id": publish_id})
            status = st.get("status", status)
            if status == "FAILED":
                raise UploadError(f"TikTok processing failed: {st.get('fail_reason')}", retryable=True)
            if status in ("PUBLISH_COMPLETE", "SEND_TO_USER_INBOX"):
                ids = st.get("publicaly_available_post_id") or st.get("publicly_available_post_id") or []
                url = f"https://m.tiktok.com/v/{ids[0]}.html" if ids else None
                return UploadResult(publish_id=publish_id, status=status, url=url)
        return UploadResult(publish_id=publish_id, status=status)  # still processing; it usually completes

