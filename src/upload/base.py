"""Uploader interface (swap TikTok for anything else later)."""
from __future__ import annotations

import json
import shutil
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass
class UploadResult:
    publish_id: str
    status: str
    url: str | None = None


class UploadError(Exception):
    def __init__(self, msg: str, retryable: bool = True):
        super().__init__(msg)
        self.retryable = retryable


class Uploader(ABC):
    name: str

    @abstractmethod
    def upload(self, video: Path, caption: str) -> UploadResult: ...


class OutboxUploader(Uploader):
    """Used when TikTok isn't connected yet: 'posts' into data/outbox so the loop still runs."""

    name = "outbox"

    def __init__(self, outbox: Path):
        self.outbox = outbox

    def upload(self, video: Path, caption: str) -> UploadResult:
        self.outbox.mkdir(parents=True, exist_ok=True)
        stem = time.strftime("%Y%m%d_%H%M%S")
        dst = self.outbox / f"{stem}.mp4"
        shutil.copyfile(video, dst)
        (self.outbox / f"{stem}.json").write_text(json.dumps({"caption": caption}, ensure_ascii=False),
                                                  encoding="utf-8")
        return UploadResult(publish_id=stem, status="OUTBOX", url=str(dst))
