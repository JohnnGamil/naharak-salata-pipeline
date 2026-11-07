"""Logging, retry and subprocess helpers shared by all modules."""
from __future__ import annotations

import json
import logging
import logging.handlers
import subprocess
import sys
from pathlib import Path

from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

log = logging.getLogger("pipeline")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key in ("job_id", "stage"):
            if hasattr(record, key):
                data[key] = getattr(record, key)
        if record.exc_info:
            data["exc"] = self.formatException(record.exc_info)
        return json.dumps(data, ensure_ascii=False)


def setup_logging(data_dir: Path, level: str = "INFO") -> None:
    root = logging.getLogger()
    if getattr(root, "_pipeline_configured", False):
        return
    root.setLevel(level)
    (data_dir / "logs").mkdir(parents=True, exist_ok=True)
    fh = logging.handlers.RotatingFileHandler(
        data_dir / "logs" / "pipeline.jsonl", maxBytes=5_000_000, backupCount=5, encoding="utf-8"
    )
    fh.setFormatter(JsonFormatter())
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # Arabic in Windows consoles
    except Exception:
        pass
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(fh)
    root.addHandler(sh)
    for noisy in ("httpx", "httpcore", "apscheduler.executors", "faster_whisper", "google_genai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    root._pipeline_configured = True  # type: ignore[attr-defined]


def _ensure_ffmpeg_on_path() -> None:
    """The PC backup starts from the Startup folder, whose PATH can miss the winget ffmpeg (2026-11-07:
    every night job failed with "The system cannot find the file specified"). Add it when missing."""
    import os
    import shutil
    if shutil.which("ffmpeg") or sys.platform != "win32":
        return
    base = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet"
    for exe in [base / "Links" / "ffmpeg.exe", *sorted((base / "Packages").glob("*FFmpeg*/*/bin/ffmpeg.exe"))]:
        if exe.exists():
            os.environ["PATH"] = str(exe.parent) + os.pathsep + os.environ.get("PATH", "")
            return


_ensure_ffmpeg_on_path()


class RetryableError(Exception):
    """Transient failure worth retrying (network, 5xx, rate limit)."""


def net_retry(attempts: int = 5, max_wait: float = 60):
    """Exponential backoff with jitter for network calls."""
    return retry(
        reraise=True,
        stop=stop_after_attempt(attempts),
        wait=wait_exponential_jitter(initial=2, max=max_wait),
        retry=retry_if_exception_type((RetryableError, OSError, TimeoutError)),
        before_sleep=before_sleep_log(log, logging.WARNING),
    )


def run(cmd: list[str], timeout: float | None = None, **kw) -> subprocess.CompletedProcess:
    """Run a command, raise with stderr tail on failure."""
    p = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, **kw
    )
    if p.returncode != 0:
        raise RuntimeError(f"{cmd[0]} failed ({p.returncode}): {p.stderr[-1500:]}")
    return p


def ffprobe_duration(path: Path) -> float:
    p = run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)])
    return float(p.stdout.strip())


def ffprobe_json(path: Path) -> dict:
    p = run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)])
    return json.loads(p.stdout)


def ff_path(path: Path) -> str:
    """Path escaped for use inside an ffmpeg filter argument (ass=..., fontsdir=...)."""
    s = str(Path(path).resolve()).replace("\\", "/")
    return s.replace(":", "\\:").replace("'", "\\'")
