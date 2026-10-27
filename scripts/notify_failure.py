"""Telegram alert when a GitHub run fails (workflow `if: failure()` step)."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

from src.config import get_settings  # noqa: E402

s = get_settings()
url = f"{os.environ.get('GITHUB_SERVER_URL', '')}/{os.environ.get('GITHUB_REPOSITORY', '')}/actions/runs/{os.environ.get('GITHUB_RUN_ID', '')}"
what = sys.argv[1] if len(sys.argv) > 1 else "run"
if s.telegram_bot_token and s.telegram_chat_id:
    httpx.post(f"https://api.telegram.org/bot{s.telegram_bot_token}/sendMessage", timeout=30,
               json={"chat_id": s.telegram_chat_id, "disable_web_page_preview": True,
                     "text": f"🚨 GitHub {what} فشل - الكمبيوتر هيكمل لو فضل واقف 3 ساعات\n{url}"})
