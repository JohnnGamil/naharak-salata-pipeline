"""'Manual TikTok' while the TikTok app waits for its audit: the finished video and its ready caption
go to John's Telegram bot chat, and he posts it from the TikTok app himself (agreed 2026-10-27).
The Bot API takes files up to 50 MB; our videos are ~5-15 MB.
"""
from __future__ import annotations

from pathlib import Path

import httpx

from .base import UploadError, Uploader, UploadResult


class TelegramHandoff(Uploader):
    name = "tiktok_manual"

    def __init__(self, token: str, chat_id: str):
        self.token, self.chat_id = token, chat_id

    def upload(self, video: Path, caption: str, cover: Path | None = None) -> UploadResult:
        api = f"https://api.telegram.org/bot{self.token}"
        if cover is not None and cover.exists():  # TikTok app: "Edit cover" -> upload this picture
            with open(cover, "rb") as f:
                httpx.post(f"{api}/sendPhoto", timeout=120, data={"chat_id": self.chat_id,
                           "caption": "🖼️ الكافر: في تيك توك اختار Edit cover وارفع الصورة دي"},
                           files={"photo": ("cover.jpg", f, "image/jpeg")})
        with open(video, "rb") as f:
            r = httpx.post(f"{api}/sendVideo", timeout=300,
                           data={"chat_id": self.chat_id, "supports_streaming": "true",
                                 "caption": "🎬 TikTok: نزّل الفيديو ده من التطبيق، والكابشن في الرسالة الجاية 👇"},
                           files={"video": (video.name, f, "video/mp4")})
        if r.status_code != 200 or not r.json().get("ok"):
            raise UploadError(f"telegram sendVideo {r.status_code}: {r.text[:300]}", retryable=r.status_code >= 500)
        mid = r.json()["result"]["message_id"]
        # caption alone, so it can be copied in one long-press
        r2 = httpx.post(f"{api}/sendMessage", timeout=60, data={"chat_id": self.chat_id, "text": caption[:4000]})
        if r2.status_code != 200:
            raise UploadError(f"telegram sendMessage {r2.status_code}: {r2.text[:300]}")
        return UploadResult(publish_id=str(mid), status="SENT_TO_TELEGRAM", url=None)
