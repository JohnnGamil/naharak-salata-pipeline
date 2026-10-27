"""Settings loaded from .env (pydantic-settings)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- keys -------------------------------------------------------------
    gemini_api_key: str = ""
    # comma separated, tried in order (free-tier model names change often)
    gemini_models: str = "gemini-3.5-flash,gemini-flash-lite-latest,gemini-flash-latest"
    pollinations_key: str = ""  # optional sk_ key; empty = legacy anonymous endpoint
    pollinations_image_model: str = "flux"
    # voice: "gemini" (natural Egyptian delivery, free) with edge-tts fallback, or "edge"
    tts_engine: str = "gemini"
    # model:voice lists, tried in order. Every TTS model has its own ~10 requests/day free quota, and
    # the voice names are shared between models, so falling back keeps the same character voice.
    gemini_tts_male: str = ("gemini-3.8-flash-tts:Puck,gemini-3.1-flash-tts-preview:Puck,"
                            "gemini-3.8-flash-lite-tts:Puck,gemini-2.5-flash-preview-tts:Puck")
    gemini_tts_female: str = ("gemini-2.5-flash-preview-tts:Leda,gemini-3.1-flash-tts-preview:Leda,"
                              "gemini-3.8-flash-tts:Leda,gemini-3.8-flash-lite-tts:Leda")
    cloudflare_account_id: str = ""  # Workers AI free daily allowance (FLUX schnell, ~2 s a picture)
    cloudflare_api_token: str = ""
    cloudflare_image_model: str = "@cf/black-forest-labs/flux-1-schnell"
    # AI Horde: volunteer GPUs, no sign-up ("0000000000" = anonymous, lowest queue priority)
    horde_api_key: str = "0000000000"
    horde_models: str = "AlbedoBase XL (SDXL),Juggernaut XL"

    kaggle_username: str = ""
    kaggle_key: str = ""
    kaggle_api_token: str = ""  # new-style token (alternative to username/key)
    kaggle_kernel_slug: str = "tiktok-clipgen"
    kaggle_weights_kernel: str = ""  # optional "<user>/<slug>" whose output holds the weights
    kaggle_video_model: str = "Lightricks/LTX-Video-0.9.5"
    kaggle_timeout_min: int = 75

    tiktok_client_key: str = ""
    tiktok_client_secret: str = ""
    tiktok_redirect_uri: str = ""
    tiktok_privacy_level: str = "SELF_ONLY"  # unaudited apps can only post SELF_ONLY
    # where every video goes (each one only if it is connected). "tiktok_manual" = send the video +
    # caption to Telegram for posting by hand (while the TikTok app waits for its audit)
    platforms: str = "tiktok,youtube,instagram"
    youtube_client_id: str = ""
    youtube_client_secret: str = ""
    youtube_redirect_uri: str = "http://127.0.0.1:8765"  # Google "Desktop app" client: loopback is allowed
    youtube_privacy: str = "private"  # unverified API projects are forced to private until Google's audit
    youtube_made_for_kids: bool = False  # COPPA flag; True = no comments/personalised ads
    facebook_app_id: str = "1421729130112215"  # the Meta app (Facebook side) for the Page token
    facebook_app_secret: str = ""
    instagram_app_id: str = ""
    instagram_app_secret: str = ""
    instagram_redirect_uri: str = ""  # https URL registered in the Meta app (Instagram Login)
    instagram_api_version: str = "v23.0"
    # Cloudflare R2 bucket with public access: Instagram downloads the video from a URL
    r2_account_id: str = ""
    r2_access_key: str = ""
    r2_secret_key: str = ""
    r2_bucket: str = ""
    r2_public_base: str = ""  # e.g. https://pub-xxxx.r2.dev
    # shared encrypted state in R2 for the GitHub runner + PC backup (src/state_sync.py)
    state_key: str = ""
    runner: str = "pc"  # "github" on the Actions runner
    state_ns: str = "state"  # R2 prefix; "dryrun" = a test copy that never touches the real state

    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # --- schedule ---------------------------------------------------------
    posts_per_day: int = 2
    post_windows: str = "13:00-15:00,20:00-23:00"
    timezone: str = "Africa/Cairo"
    buffer_size: int = 2
    upload_enabled: bool = True

    # --- quality / retries -----------------------------------------------
    max_retries: int = 3
    # content mix: a 3-part mini-series, then this many standalone episodes, then a series again
    # the channel's name (John, 2026-10-27); its hashtag goes on every post on every platform
    channel_name: str = "نهارك سلطة"
    series_enabled: bool = True
    standalone_between_series: int = 2
    qa_visual_min_score: float = 7.0
    qa_audio_min_similarity: float = 0.75
    qa_require_visual: bool = False  # if Gemini vision is unavailable, still pass
    whisper_model: str = "small"

    # --- engines ----------------------------------------------------------
    kaggle_weekly_gpu_hours_budget: float = 28.0
    video_engine: str = Field(default="kaggle")  # kaggle | fallback

    # --- paths ------------------------------------------------------------
    data_dir: Path = ROOT / "data"
    log_level: str = "INFO"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "pipeline.db"

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "jobs"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def models_dir(self) -> Path:
        return self.data_dir / "models"

    @property
    def gemini_model_list(self) -> list[str]:
        return [m.strip() for m in self.gemini_models.split(",") if m.strip()]

    @property
    def kaggle_enabled(self) -> bool:
        return bool(self.kaggle_username and (self.kaggle_key or self.kaggle_api_token))

    @property
    def windows(self) -> list[tuple[int, int]]:
        """POST_WINDOWS as (start_minute, end_minute) pairs."""
        out = []
        for part in self.post_windows.split(","):
            a, b = part.strip().split("-")
            ha, ma = map(int, a.split(":"))
            hb, mb = map(int, b.split(":"))
            out.append((ha * 60 + ma, hb * 60 + mb))
        return out


ASSETS = ROOT / "assets"
FONTS_DIR = ASSETS / "fonts"
MUSIC_DIR = ASSETS / "music"
CAPTION_FONT = "Noto Kufi Arabic Black"


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    for d in (s.data_dir, s.jobs_dir, s.cache_dir, s.models_dir):
        d.mkdir(parents=True, exist_ok=True)
    return s
