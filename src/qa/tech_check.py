"""Technical checks: duration, resolution, audio present and not silent, file size."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from ..util import ffprobe_json

MIN_S, MAX_S = 15.0, 90.0
MIN_MB, MAX_MB = 0.3, 280.0


def mean_volume_db(path: Path) -> float:
    p = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vn", "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    m = re.search(r"mean_volume:\s*(-?[\d.]+|-inf) dB", p.stderr)
    if not m or m.group(1) == "-inf":
        return -120.0
    return float(m.group(1))


def check(path: Path, width: int = 1080, height: int = 1920) -> dict:
    problems = []
    if not path.exists():
        return {"passed": False, "problems": ["file missing"]}
    size_mb = path.stat().st_size / 1e6
    info = ffprobe_json(path)
    dur = float(info["format"].get("duration", 0))
    v = [s for s in info["streams"] if s["codec_type"] == "video"]
    a = [s for s in info["streams"] if s["codec_type"] == "audio"]
    if not MIN_S <= dur <= MAX_S:
        problems.append(f"duration {dur:.1f}s outside {MIN_S}-{MAX_S}s")
    if not v:
        problems.append("no video stream")
    elif (int(v[0]["width"]), int(v[0]["height"])) != (width, height):
        problems.append(f"resolution {v[0]['width']}x{v[0]['height']}")
    elif v[0].get("codec_name") != "h264":
        problems.append(f"codec {v[0].get('codec_name')}")
    vol = None
    if not a:
        problems.append("no audio stream")
    else:
        vol = mean_volume_db(path)
        if vol < -40:
            problems.append(f"audio near silent ({vol:.1f} dB)")
    if not MIN_MB <= size_mb <= MAX_MB:
        problems.append(f"file size {size_mb:.1f} MB")
    return {"passed": not problems, "problems": problems, "duration": dur, "size_mb": round(size_mb, 2),
            "mean_volume_db": vol}
