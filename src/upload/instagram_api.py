"""Instagram Reels via the Instagram API with Instagram Login (no Facebook Page needed).

Docs: developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login
      .../content-publishing
Needs a Professional (Creator or Business) Instagram account added as a tester/admin of the Meta
app; publishing to your own account works in Development mode, no app review.
Flow: video -> public URL (R2) -> POST /{ig-user}/media (REELS container) -> poll status_code until
FINISHED -> POST /{ig-user}/media_publish. Limit: 50 API posts per 24 h.
Tokens: long-lived (60 days), refreshed when older than a day and under ~10 days left.
"""
from __future__ import annotations

import logging
import time
import urllib.parse
import uuid
from pathlib import Path

import httpx

from . import r2
from .base import UploadError, Uploader, UploadResult

log = logging.getLogger(__name__)

GRAPH = "https://graph.instagram.com"
AUTH_URL = "https://www.instagram.com/oauth/authorize"
SCOPES = "instagram_business_basic,instagram_business_content_publish"
TOKEN_KEY = "instagram_tokens"


def authorize_url(app_id: str, redirect_uri: str, state: str) -> str:
    q = {"client_id": app_id, "redirect_uri": redirect_uri, "response_type": "code", "scope": SCOPES,
         "state": state}
    return f"{AUTH_URL}?{urllib.parse.urlencode(q)}"


class InstagramTokens:
    def __init__(self, db, app_id: str, app_secret: str, version: str = "v23.0"):
        self.db, self.app_id, self.secret, self.v = db, app_id, app_secret, version

    def get(self) -> dict | None:
        return self.db.get(TOKEN_KEY)

    def exchange_code(self, code: str, redirect_uri: str) -> dict:
        r = httpx.post("https://api.instagram.com/oauth/access_token", timeout=30, data={
            "client_id": self.app_id, "client_secret": self.secret, "grant_type": "authorization_code",
            "redirect_uri": redirect_uri, "code": code.split("#")[0]})
        short = r.json() if r.content else {}
        if r.status_code != 200 or "access_token" not in short:
            raise UploadError(f"Instagram code exchange {r.status_code}: {short}", retryable=False)
        r = httpx.get(f"{GRAPH}/access_token", timeout=30, params={
            "grant_type": "ig_exchange_token", "client_secret": self.secret, "access_token": short["access_token"]})
        long = r.json() if r.content else {}
        if r.status_code != 200 or "access_token" not in long:
            raise UploadError(f"Instagram long-lived token {r.status_code}: {long}", retryable=False)
        tok = {"access_token": long["access_token"], "user_id": str(short.get("user_id", "")),
               "issued_at": time.time(), "expires_at": time.time() + int(long.get("expires_in", 5184000))}
        me = httpx.get(f"{GRAPH}/{self.v}/me", params={"fields": "user_id,username",
                                                        "access_token": tok["access_token"]}, timeout=30).json()
        tok["user_id"] = str(me.get("user_id") or tok["user_id"])
        tok["username"] = me.get("username")
        self.db.put(TOKEN_KEY, tok)
        return tok

    def access(self) -> tuple[str, str]:
        tok = self.get()
        if not tok:
            raise UploadError("Instagram not connected - run scripts/instagram_oauth.py", retryable=False)
        now = time.time()
        if tok["expires_at"] < now:
            raise UploadError("Instagram token expired - run scripts/instagram_oauth.py again", retryable=False)
        if tok["expires_at"] - now < 10 * 86400 and now - tok.get("issued_at", 0) > 86400:
            r = httpx.get(f"{GRAPH}/refresh_access_token", timeout=30,
                          params={"grant_type": "ig_refresh_token", "access_token": tok["access_token"]})
            data = r.json() if r.content else {}
            if r.status_code == 200 and "access_token" in data:
                tok.update(access_token=data["access_token"], issued_at=now,
                           expires_at=now + int(data.get("expires_in", 5184000)))
                self.db.put(TOKEN_KEY, tok)
                log.info("Instagram token refreshed")
            else:
                log.warning("Instagram token refresh failed: %s", data)
        return tok["access_token"], tok["user_id"]


class InstagramUploader(Uploader):
    name = "instagram"

    def __init__(self, tokens: InstagramTokens, settings, poll_s: int = 10, timeout_s: int = 900):
        self.tokens, self.s, self.poll_s, self.timeout_s = tokens, settings, poll_s, timeout_s

    def _api(self, method: str, path: str, **params) -> dict:
        token, _ = self.tokens.access()
        r = httpx.request(method, f"{GRAPH}/{self.tokens.v}/{path}", params={**params, "access_token": token},
                          timeout=120)
        data = r.json() if r.content else {}
        if r.status_code != 200:
            err = data.get("error", {})
            # 190 = bad token, 10/200 = permission: a person has to fix those
            retry = r.status_code >= 500 or err.get("code") in (4, 9, 17, 32, 613, 2207051)
            raise UploadError(f"Instagram {path} {r.status_code}: {err or r.text[:300]}", retryable=retry)
        return data

    def upload(self, video: Path, caption: str, cover: Path | None = None) -> UploadResult:
        if not r2.enabled(self.s):
            raise UploadError("Instagram needs a public video URL: set the R2_* values in .env", retryable=False)
        _, user = self.tokens.access()
        key = f"reels/{uuid.uuid4().hex}.mp4"
        url = r2.put_public(self.s, video, key)
        cover_key = None
        try:
            extra = {}
            if cover is not None and cover.exists():
                cover_key = key.replace(".mp4", ".jpg")
                extra["cover_url"] = r2.put_public(self.s, cover, cover_key, "image/jpeg")
            box = self._api("POST", f"{user}/media", media_type="REELS", video_url=url, caption=caption[:2200],
                            share_to_feed="true", **extra)
            cid = box["id"]
            started = time.time()
            while True:
                st = self._api("GET", cid, fields="status_code,status")
                code = st.get("status_code")
                if code == "FINISHED":
                    break
                if code in ("ERROR", "EXPIRED"):
                    raise UploadError(f"Instagram processing {code}: {st.get('status')}", retryable=True)
                if time.time() - started > self.timeout_s:
                    raise UploadError("Instagram processing timed out", retryable=True)
                time.sleep(self.poll_s)
            media = self._api("POST", f"{user}/media_publish", creation_id=cid)["id"]
            link = None
            try:
                link = self._api("GET", media, fields="permalink").get("permalink")
            except UploadError:
                pass
            log.info("Instagram reel published: %s", media)
            return UploadResult(publish_id=media, status="PUBLISHED", url=link)
        finally:
            r2.delete(self.s, key)
            if cover_key:
                r2.delete(self.s, cover_key)
