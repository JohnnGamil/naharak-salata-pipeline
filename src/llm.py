"""Text/vision LLM access: Gemini first (model list fallback), Pollinations text second."""
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
        """writer=True: creative writing, strong models only (no lite model, no Pollinations) - raises
        LLMUnavailable when they are all busy, so the caller waits instead of writing a weak script."""
        if writer:
            if not self.gemini_enabled:
                raise LLMUnavailable("GEMINI_API_KEY not set")
            return self._gemini_call(prompt, json_mode, temperature, self.s.gemini_writer_list)
        if self.gemini_enabled:
            try:
                return self._gemini_call(prompt, json_mode, temperature)
            except LLMUnavailable as e:
                log.warning("falling back to Pollinations text: %s", e)
        try:
            return self._pollinations_text(prompt, json_mode, temperature)
        except Exception as e:
            raise LLMUnavailable(f"no text LLM available: {e}") from e

    def vision_json(self, images: list[bytes], prompt: str) -> dict:
        """Gemini vision only (Pollinations' anonymous tier has no reliable vision)."""
        if not self.gemini_enabled:
            raise LLMUnavailable("GEMINI_API_KEY not set")
        from google.genai import types

        parts = [types.Part.from_bytes(data=b, mime_type="image/jpeg") for b in images]
        return extract_json(self._gemini_call([*parts, prompt], json_mode=True, temperature=0.2))
