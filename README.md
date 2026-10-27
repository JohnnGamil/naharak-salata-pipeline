# AI TikTok Video Pipeline

Fully automatic "talking fruits & vegetables" videos in Egyptian Arabic:
idea → character images → voice → video clips → captions + music → QA → TikTok → Telegram.
Cost: $0 (free tiers only). The host only orchestrates; heavy AI runs on Kaggle / free APIs.

---

## بالعربي (المختصر)

**السيستم بيعمل إيه:** كل شوية بيألف اسكتش مصري قصير (طماطم وبصلة بيتخانقوا مثلاً)، يعمل صور للشخصيات،
يسجل الصوت بـ edge-tts بلهجة مصرية، يعمل الفيديو (على Kaggle GPU، ولو مش متاح يعمل أنيميشن بسيط على الـCPU
البق بيتحرك مع الصوت)، يحط ترجمة عربي ومزيكا، يراجع الفيديو لوحده (مدة/صوت/صورة)، وبعدين يرفعه على تيك توك
ويبعتلك رسالة على تليجرام. مفيش موافقة منك على كل فيديو.

**المطلوب منك مرة واحدة بس:**
1. **Gemini** (مجاني): ادخل https://aistudio.google.com/apikey → Create API key → حطه في `GEMINI_API_KEY`.
   ده أهم مفتاح: بيكتب الاسكتشات وبيراجع الصور ويحدد مكان البق.
2. **Pollinations** (مجاني، اختياري بس مهم): https://enter.pollinations.ai → سجل → اعمل `sk_` key → `POLLINATIONS_KEY`.
   من غيره بيطلع صورة واحدة تقريبا كل ساعة، والباقي بيترسم كرتون بسيط.
2b. **Cloudflare Workers AI** (مجاني، للصور): https://dash.cloudflare.com/profile/api-tokens → Create Token →
   template "Workers AI" → حط `CLOUDFLARE_API_TOKEN` و `CLOUDFLARE_ACCOUNT_ID` (من اللينك بعد dash.cloudflare.com/).
   لو مفيش: الصور بتترسم على Kaggle GPU، وبعدين AI Horde (مجاني من غير تسجيل).
3. **Kaggle** (مجاني): اعمل حساب → **وثّق رقم الموبايل** (لازم عشان الـGPU والإنترنت) →
   Settings → API → Create token → حط `KAGGLE_USERNAME` و `KAGGLE_API_TOKEN` (أو `KAGGLE_KEY`).
   بعدين مرة واحدة: `python -m scripts.kaggle_setup --weights` ولما يخلص حط القيمة في `KAGGLE_WEIGHTS_KERNEL`.
4. **TikTok**: https://developers.tiktok.com → Manage apps → Create app → ضيف **Login Kit** و **Content Posting API**
   (فعّل Direct Post) → سكوبات `user.info.basic, video.publish, video.upload` → حط Redirect URI (https) →
   حط الـclient key/secret في `.env` → شغل `python -m scripts.tiktok_oauth` وافتح اللينك ووافق والصق اللينك اللي رجعلك.
   ⚠️ لحد ما التطبيق يعدي الـaudit: الفيديوهات بتتنشر **خاصة (SELF_ONLY)** وحساب تيك توك نفسه لازم يكون **Private**.
   بعد الـaudit غيّر `TIKTOK_PRIVACY_LEVEL=PUBLIC_TO_EVERYONE`.
4b. **YouTube Shorts** (مجاني): https://console.cloud.google.com → مشروع جديد → فعّل **YouTube Data API v3** →
   OAuth consent screen (External، وبعدين **Publish app** عشان الدخول مايخلصش كل 7 أيام) →
   Credentials → OAuth client ID نوعه **Desktop app** → حط `YOUTUBE_CLIENT_ID/SECRET` → `python -m scripts.youtube_oauth`.
   ⚠️ لحد ما Google يراجع المشروع (فورم audit مجاني) الفيديوهات بتنزل **Private**.
4c. **Instagram Reels** (مجاني): حساب إنستجرام **Professional (Creator)** → https://developers.facebook.com → Create app →
   **Instagram API with Instagram Login** → ضيف حسابك كـ tester → حط `INSTAGRAM_APP_ID/SECRET/REDIRECT_URI` →
   `python -m scripts.instagram_oauth`. محتاج كمان **Cloudflare R2** bucket عام (`R2_*`) لأن إنستجرام بياخد الفيديو من لينك.
5. **Telegram**: كلم @BotFather → `/newbot` → خد التوكن. ابعت أي رسالة للبوت، وافتح
   `https://api.telegram.org/bot<TOKEN>/getUpdates` وخد `chat.id`.
6. انسخ `.env.example` لـ `.env` واملاه → `docker compose up -d`.

**لو مفيش مفاتيح خالص** السيستم برضه بيشتغل: اسكتشات جاهزة + كرتون مرسوم + الفيديوهات بتتحط في `data/outbox`
بدل تيك توك. يعني تقدر تجربه دلوقتي.

---

## English

### How it works
```
generate_idea → character_images → tts → clips (Kaggle | fallback) → compose → QA
      → (fail: regenerate the failing part, max MAX_RETRIES, then fallback engine) → upload → notify
```
Each run is a row in `jobs`; status = last completed stage
(`NEW → IDEA → IMAGES → AUDIO → CLIPS → COMPOSED → QA_PASSED → UPLOADED`, or `FAILED`).
Every stage writes its files to `data/jobs/<id>/` before the status moves, so a killed container
resumes at the next stage. Voice is made *before* clips because clip length and the mouth animation follow it.

| Stage | Primary | Fallback(s) |
|---|---|---|
| Idea | Gemini (model list in `GEMINI_MODELS`, strict JSON, pydantic-validated, last 50 titles excluded, formats rotate, recurring character library) | Pollinations text → built-in skit bank |
| Images | Pollinations (keyed only) → Cloudflare Workers AI FLUX schnell → Kaggle T4 Playground v2.5 (one kernel for all characters) → AI Horde (anonymous); 2 candidates, Gemini picks; cached per character | PIL-drawn cartoon character |
| Mouth position | Gemini vision box | no mouth overlay, squash-and-stretch "talking" |
| Voice | edge-tts `ar-EG-ShakirNeural` / `ar-EG-SalmaNeural`, rate/pitch per emotion, word timings | — (retries) |
| Video | Kaggle T4: LTX-Video 0.9.5 image-to-video (diffusers, fp16), all scenes in one kernel run | CPU mouth-flap animation (RMS-driven mouth, zoom/pan, punch-in, bounce) — also fills single scenes Kaggle missed |
| Compose | FFmpeg: 1080×1920 / 30 fps / H.264 veryfast / AAC, `.ass` captions via libass (Noto Kufi Arabic), music ducked with sidechaincompress, hook title for 2 s | — |
| QA | tech (15–90 s, 1080×1920, h264, audio, not silent, size) → faster-whisper small int8 CPU similarity → Gemini vision 6 frames | visual check is skipped when Gemini is unavailable unless `QA_REQUIRE_VISUAL=true` |
| Upload | Every connected platform per slot: TikTok (Content Posting API, `is_aigc`), YouTube Shorts (Data API v3 resumable, `containsSyntheticMedia`), Instagram Reels (Graph API via an R2 public URL). Each platform retries on its own (3 tries / 2 days); a series part waits for the previous one | `data/outbox/` when nothing is connected |
| Notify | Telegram (success, failures, quota, token problems, no-upload-24h, empty buffer) | log only |

Arabic caption rules (libass): override tags only at the start of a line (never mid-line — it scrambles
RTL word order), no punctuation, max 3 words / 15 chars per caption, one caption on screen at a time.

### Scheduling
- `POSTS_PER_DAY` slots spread over `POST_WINDOWS` (Cairo time) with random jitter, planned per day in SQLite.
- A producer keeps `BUFFER_SIZE` QA-passed videos ready, so posting never waits on Kaggle.
- A slot missed by more than 4 h (machine off) is skipped; 3 failed runs in a row → production backs off.
- Hourly health check: no successful upload in 24 h → Telegram alert. Kaggle GPU hours are tracked per
  7 days against `KAGGLE_WEEKLY_GPU_HOURS_BUDGET`; near the budget it switches to the fallback engine.

### One-time setup
1. **Kaggle**: account → phone verification (required for GPU + internet) → Settings → API → token.
   Set `KAGGLE_USERNAME` + `KAGGLE_API_TOKEN` (or legacy `KAGGLE_KEY`).
   - `python -m scripts.kaggle_setup --check`
   - `python -m scripts.kaggle_setup --weights` (one-time CPU kernel that saves fp16 weights, ~13 GB, as
     its output). When it's complete set `KAGGLE_WEIGHTS_KERNEL=<user>/tiktok-clipgen-weights`.
     Without it each run downloads the weights from Hugging Face (works, ~5 min slower).
   - `python -m scripts.kaggle_setup --test-one` runs one scene of the latest job on the GPU.
2. **Gemini** key from Google AI Studio → `GEMINI_API_KEY`.
3. **Pollinations** (optional, recommended): key from enter.pollinations.ai → `POLLINATIONS_KEY`.
4. **TikTok**: developer app with Login Kit + Content Posting API (Direct Post), scopes
   `user.info.basic,video.publish,video.upload`, an https redirect URI. Fill the three `TIKTOK_*` values,
   run `python -m scripts.tiktok_oauth` (add `--pkce` for a desktop app), then submit the app for audit.
   Until the audit passes posts are `SELF_ONLY` and the TikTok account must be private.
4b. **YouTube Shorts**: Google Cloud project -> enable YouTube Data API v3 -> OAuth consent screen
   (External, then *Publish app* so the refresh token doesn't expire after 7 days) -> OAuth client of type
   *Desktop app* -> `YOUTUBE_CLIENT_ID/SECRET` -> `python -m scripts.youtube_oauth`. Uploads stay private
   until the project passes Google's API audit.
4c. **Instagram Reels**: Professional (Creator) IG account -> Meta app with *Instagram API with Instagram
   Login*, your account as tester -> `INSTAGRAM_APP_ID/SECRET/REDIRECT_URI` -> `python -m scripts.instagram_oauth`.
   Needs a public Cloudflare R2 bucket (`R2_*`): Instagram downloads the video from a URL.
5. **Telegram**: @BotFather → token; message the bot; read `chat.id` from `getUpdates`.
6. `cp .env.example .env`, fill it, `docker compose up -d`.

Running the OAuth script inside the container: `docker compose run --rm pipeline python -m scripts.tiktok_oauth`.

### Commands
```bash
docker compose up -d                                   # run forever (restart: always)
docker compose logs -f                                 # follow logs
docker compose exec pipeline python -m src.main --status
docker compose exec pipeline python -m src.main --once      # make one video now
docker compose exec pipeline python -m src.main --post-now  # post the oldest ready video now
docker compose exec pipeline python -m pytest -q tests
```
Without Docker: `python -m venv .venv`, `pip install -r requirements.txt`, ffmpeg with libass on PATH,
`python -m src.main`.

### Oracle Cloud Free Tier (ARM)
Ampere A1 VM (Ubuntu) → install Docker → copy the project + `.env` → `docker compose up -d`.
Everything on the host is CPU-only and has arm64 wheels; ~2 GB RAM is enough.

### Files
- `data/pipeline.db` – SQLite (jobs, characters, quota, tokens, post slots)
- `data/jobs/<id>/` – story.json, images, audio, clips, compose/, final.mp4, qa.json
- `data/logs/pipeline.jsonl` – structured logs (rotated)
- `data/outbox/` – videos "posted" while TikTok isn't connected
- `assets/music/` – background loops synthesized by `scripts/make_music.py` (no licensing issues)
- `assets/fonts/` – Noto Kufi / Naskh Arabic (OFL)
