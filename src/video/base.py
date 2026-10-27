"""Video engine interface."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

W, H, FPS = 1080, 1920, 30


@dataclass
class SceneSpec:
    index: int
    character_id: str
    image: Path  # character reference image
    meta: dict  # mouth box etc.
    audio: Path
    duration: float
    emotion: str
    prompt: str  # English visual prompt
    extra: dict = field(default_factory=dict)


class QuotaWait(Exception):
    """A free daily allowance (pictures, voices) is used up: pause this job until it resets
    instead of making a worse-looking video."""


class EngineUnavailable(Exception):
    """Engine can't run right now (quota, credentials, service down) -> use the fallback."""


class VideoEngine(ABC):
    name: str

    @abstractmethod
    def generate(self, scenes: list[SceneSpec], out_dir: Path, seed: int = 0) -> list[Path]:
        """Return one silent clip per scene. Clips may be any length/size; the composer fits them."""
