"""Telegram notifier (info only). Never raises: a notification failure must not kill a run."""
from __future__ import annotations

import logging
import time

import httpx

log = logging.getLogger(__name__)


class Telegram:
    def __init__(self, token: str, chat_id: str, db=None):
        self.token, self.chat_id, self.db = token, chat_id, db

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    def send_video(self, video, caption: str) -> bool:
        """A finished video with its text, so John sees it as soon as it is made (2026-10-28)."""
        if not self.enabled:
            return False
        try:
            with open(video, "rb") as f:
                r = httpx.post(f"https://api.telegram.org/bot{self.token}/sendVideo", timeout=300,
                               data={"chat_id": self.chat_id, "caption": caption[:1024], "supports_streaming": "true"},
                               files={"video": ("video.mp4", f, "video/mp4")})
            if r.status_code == 200:
                return True
            log.warning("telegram video %s: %s", r.status_code, r.text[:300])
        except (httpx.HTTPError, OSError) as e:
            log.warning("telegram video error: %s", e)
        return False

    def send_photo(self, photo, caption: str) -> bool:
        if not self.enabled:
            return False
        try:
            with open(photo, "rb") as f:
                r = httpx.post(f"https://api.telegram.org/bot{self.token}/sendPhoto", timeout=120,
                               data={"chat_id": self.chat_id, "caption": caption[:1024]},
                               files={"photo": ("story.jpg", f, "image/jpeg")})
            if r.status_code == 200:
                return True
            log.warning("telegram photo %s: %s", r.status_code, r.text[:300])
        except (httpx.HTTPError, OSError) as e:
            log.warning("telegram photo error: %s", e)
        return False

    def send(self, text: str, throttle_key: str | None = None, throttle_s: float = 0) -> bool:
        """Send a message. With throttle_key, at most one message per throttle_s seconds."""
        if throttle_key and self.db is not None:
            last = self.db.get(f"tg_throttle:{throttle_key}", 0)
            if time.time() - last < throttle_s:
                return False
            self.db.put(f"tg_throttle:{throttle_key}", time.time())
        log.info("notify: %s", text.replace("\n", " | "))
        if not self.enabled:
            return False
        for attempt in range(4):
            try:
                r = httpx.post(
                    f"https://api.telegram.org/bot{self.token}/sendMessage",
                    json={"chat_id": self.chat_id, "text": text[:4000], "disable_web_page_preview": True},
                    timeout=20,
                )
                if r.status_code == 200:
                    return True
                if r.status_code == 429:
                    time.sleep(float(r.json().get("parameters", {}).get("retry_after", 5)))
                    continue
                log.warning("telegram %s: %s", r.status_code, r.text[:300])
                return False
            except httpx.HTTPError as e:
                log.warning("telegram error: %s", e)
                time.sleep(2 ** attempt)
        return False
