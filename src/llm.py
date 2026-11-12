"""Text/vision LLM access: Gemini first (model list fallback), Cloudflare gpt-oss second, Pollinations last."""
from __future__ import annotations

import json
import logging
import re
import time

import httpx

from .config import Settings
from .util import RetryableError, net_retry

log = logging.getLogger(__name__)


class LLMUnavailable(Exception):
    pass


def extract_json(text: str):
    """Parse JSON from a model reply, tolerating ```json fences and leading prose."""
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = min([i for i in (text.find("{"), text.find("[")) if i >= 0], default=-1)
        if start < 0:
            raise
        end = max(text.rfind("}"), text.rfind("]"))
        return json.loads(text[start : end + 1])


class LLM:
    def __init__(self, settings: Settings, db=None):
        self.s = settings
        self.db = db
        self._client = None
        # models that returned quota/not-found errors recently: name -> retry-after ts
        self._cooldown: dict[str, float] = {}

    @property
    def gemini_enabled(self) -> bool:
        return bool(self.s.gemini_api_key)

    def _gemini(self):
        if self._client is None:
            from google import genai
            from google.genai import types

            # a hung model (seen: gemini-flash-latest >60 s) must not block the pipeline
            self._client = genai.Client(api_key=self.s.gemini_api_key,
                                        http_options=types.HttpOptions(timeout=120_000))
        return self._client

    def _gemini_call(self, contents, json_mode: bool, temperature: float, models: list[str] | None = None) -> str:
        from google.genai import types

        last: Exception | None = None
        for model in models or self.s.gemini_model_list:
            if self._cooldown.get(model, 0) > time.time():
                continue
            try:
                cfg = types.GenerateContentConfig(
                    temperature=temperature,
                    response_mime_type="application/json" if json_mode else None,
                )
                resp = self._gemini().models.generate_content(model=model, contents=contents, config=cfg)
                if self.db is not None:
                    self.db.add_quota("gemini", 1, model)
                text = resp.text or ""
                if not text.strip():
                    raise RuntimeError("empty Gemini response")
                return text
            except Exception as e:  # SDK raises its own error classes; classify by message
                msg = str(e)
                last = e
                if any(k in msg for k in ("429", "RESOURCE_EXHAUSTED", "quota")):
                    self._cooldown[model] = time.time() + 15 * 60
                elif any(k in msg for k in ("404", "NOT_FOUND", "not found", "not supported")):
                    self._cooldown[model] = time.time() + 24 * 3600
                elif any(k in msg for k in ("500", "503", "UNAVAILABLE", "overloaded", "timed out")):
                    self._cooldown[model] = time.time() + 60
                log.warning("gemini %s failed: %s", model, msg[:300])
        raise LLMUnavailable(f"all Gemini models failed: {last}")

    @net_retry(attempts=3, max_wait=30)
    def groq_chat(self, content, json_mode: bool, temperature: float, model: str, max_tokens: int = 6000) -> str:
        """Groq (2026-11-12, free 1000 requests/day per model): gpt-oss-120b writes, qwen3.8 sees pictures."""
        body = {"model": model, "messages": [{"role": "user", "content": content}],
                "temperature": temperature, "max_tokens": max_tokens}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        if "gpt-oss" in model:
            body["reasoning_effort"] = "low"
        r = httpx.post("https://api.groq.com/openai/v1/chat/completions", json=body, timeout=150,
                       headers={"Authorization": f"Bearer {self.s.groq_api_key}"})
        if r.status_code >= 500 or (r.status_code == 429 and "per day" not in r.text):
            raise RetryableError(f"groq {r.status_code}")  # per-minute limit: wait a moment
        if r.status_code >= 400:
            raise LLMUnavailable(f"groq {model} {r.status_code}: {r.text[:200]}")
        text = (r.json()["choices"][0]["message"].get("content") or "").strip()
        if not text:
            raise RetryableError("groq returned nothing")
        if json_mode:
            extract_json(text)
        return text

    def _groq_text(self, prompt: str, json_mode: bool, temperature: float) -> str:
        last = None
        for m in self.s.groq_text_list:
            if self._cooldown.get("groq:" + m, 0) > time.time():
                continue
            try:
                return self.groq_chat(prompt, json_mode, temperature, m)
            except Exception as e:  # noqa: BLE001
                last = e
                if "per day" in str(e) or "429" in str(e):
                    self._cooldown["groq:" + m] = time.time() + 3600
                log.warning("groq %s failed: %s", m, str(e)[:200])
        raise LLMUnavailable(f"groq failed: {last}")

    @net_retry(attempts=2)
    def _cloudflare_text(self, prompt: str, json_mode: bool, temperature: float) -> str:
        """gpt-oss-120b on Workers AI (2026-11-11): a second brain when every Gemini model is down."""
        url = f"https://api.cloudflare.com/client/v4/accounts/{self.s.cloudflare_account_id}/ai/v1/chat/completions"
        body = {"model": self.s.cloudflare_text_model, "messages": [{"role": "user", "content": prompt}],
                "temperature": temperature, "max_tokens": 6000,
                "reasoning_effort": "low"}  # gpt-oss thinks first: on high it ran out of tokens with no answer
        r = httpx.post(url, json=body, timeout=150, headers={"Authorization": f"Bearer {self.s.cloudflare_api_token}"})
        if r.status_code >= 500 or (r.status_code == 429 and "daily" not in r.text):
            raise RetryableError(f"cloudflare text {r.status_code}")
        r.raise_for_status()
        text = (r.json()["choices"][0]["message"].get("content") or "").strip()
        if not text:
            raise RetryableError("cloudflare text returned nothing")
        if json_mode:
            extract_json(text)  # raises when the answer is not JSON
        return text

    @net_retry(attempts=3)
    def _pollinations_text(self, prompt: str, json_mode: bool, temperature: float) -> str:
        messages = [{"role": "user", "content": prompt}]
        if self.s.pollinations_key:
            url = "https://gen.pollinations.ai/v1/chat/completions"
            body = {"model": "openai", "messages": messages, "temperature": temperature}
            if json_mode:
                body["response_format"] = {"type": "json_object"}
            r = httpx.post(
                url, json=body, timeout=90,
                headers={"Authorization": f"Bearer {self.s.pollinations_key}"},
            )
        else:
            body = {"model": "openai", "messages": messages, "jsonMode": json_mode, "seed": int(time.time())}
            r = httpx.post("https://text.pollinations.ai/", json=body, timeout=90)
        if r.status_code >= 500 or r.status_code == 429:
            raise RetryableError(f"pollinations text {r.status_code}")
        r.raise_for_status()
        text = r.text
        try:  # OpenAI-shaped reply
            data = r.json()
            if isinstance(data, dict) and "choices" in data:
                data = data["choices"][0]["message"]
            if isinstance(data, dict) and "role" in data:  # message object (content may be missing
                text = data.get("content") or ""        # when a reasoning model ran out of tokens)
                if not text.strip():
                    raise RetryableError("pollinations returned a message without content")
            elif isinstance(data, (dict, list)) and json_mode:
                text = json.dumps(data, ensure_ascii=False)
        except ValueError:
            pass
        return text

    def text(self, prompt: str, json_mode: bool = True, temperature: float = 1.0, writer: bool = False) -> str:
        """Gemini first, then Groq, then Cloudflare (2026-11-12, John: "never stop because Gemini is down").
        writer=True: creative writing, strong models only (no lite Gemini, no Pollinations); the editor,
        check_writing and proofread still judge whatever any of them wrote."""
        tries = []
        if self.gemini_enabled:
            tries.append(("gemini", lambda: self._gemini_call(prompt, json_mode, temperature,
                                                              self.s.gemini_writer_list if writer else None)))
        if self.s.groq_api_key:
            tries.append(("groq", lambda: self._groq_text(prompt, json_mode, temperature)))
        if self.s.cloudflare_account_id and self.s.cloudflare_api_token:
            tries.append(("cloudflare", lambda: self._cloudflare_text(prompt, json_mode, temperature)))
        if not writer:
            tries.append(("pollinations", lambda: self._pollinations_text(prompt, json_mode, temperature)))
        errors = []
        for name, call in tries:
            try:
                return call()
            except Exception as e:  # noqa: BLE001 - next provider
                errors.append(f"{name}: {str(e)[:120]}")
                log.warning("%s text failed, trying the next: %s", name, str(e)[:150])
        raise LLMUnavailable("no text model available: " + " | ".join(errors))

    def vision_json(self, images: list[bytes], prompt: str) -> dict:
        """Gemini vision only (Pollinations' anonymous tier has no reliable vision)."""
        if not self.gemini_enabled:
            raise LLMUnavailable("GEMINI_API_KEY not set")
        from google.genai import types

        parts = [types.Part.from_bytes(data=b, mime_type="image/jpeg") for b in images]
        return extract_json(self._gemini_call([*parts, prompt], json_mode=True, temperature=0.2))
