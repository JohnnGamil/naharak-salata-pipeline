# CPU-only orchestrator; builds on amd64 and arm64 (Oracle Cloud Ampere)
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 TZ=Africa/Cairo \
    HF_HUB_DISABLE_SYMLINKS_WARNING=1

# ffmpeg from Debian is built with libass + harfbuzz + fribidi (Arabic shaping)
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg fontconfig tzdata ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY assets ./assets
COPY kaggle_job ./kaggle_job
COPY scripts ./scripts
COPY src ./src
COPY tests ./tests

# state (db, jobs, logs, whisper model) lives in the mounted volume
VOLUME ["/app/data"]
CMD ["python", "-m", "src.main"]
