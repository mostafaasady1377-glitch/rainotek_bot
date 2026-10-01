from __future__ import annotations

import json
from typing import Any

import aiohttp
from pydantic import BaseModel, ConfigDict, Field

from bot.config import Settings, get_settings

GROQ_BASE_URL = "https://api.groq.com/openai/v1"


class LaptopVoiceFilters(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    brand: str | None = Field(default=None, max_length=120)
    model: str | None = Field(default=None, max_length=180)
    cpu: str | None = Field(default=None, max_length=120)
    ram: str | None = Field(default=None, max_length=80)
    gpu: str | None = Field(default=None, max_length=120)
    storage: str | None = Field(default=None, max_length=80)

    def as_filters(self) -> dict[str, str | None]:
        return self.model_dump(exclude_none=True)


class AIVoiceService:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        if not self.settings.GROQ_API_KEY:
            raise ValueError("کلید GROQ_API_KEY تنظیم نشده است.")

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.settings.GROQ_API_KEY}"}

    async def transcribe_audio(self, audio: bytes) -> str:
        form = aiohttp.FormData()
        form.add_field("file", audio, filename="voice.ogg", content_type="audio/ogg")
        form.add_field("model", self.settings.GROQ_STT_MODEL)
        form.add_field("language", "fa")
        form.add_field("response_format", "json")
        timeout = aiohttp.ClientTimeout(total=60)
        async with aiohttp.ClientSession(timeout=timeout) as client:
            async with client.post(
                f"{GROQ_BASE_URL}/audio/transcriptions",
                headers=self._headers(),
                data=form,
            ) as response:
                payload = await response.json(content_type=None)
                if response.status >= 400:
                    raise RuntimeError(_api_error(payload, response.status))
        transcript = str(payload.get("text", "")).strip()
        if not transcript:
            raise ValueError("گفتار قابل تشخیصی در ویس پیدا نشد.")
        return transcript

    async def extract_laptop_filters(self, transcript: str) -> LaptopVoiceFilters:
        schema = LaptopVoiceFilters.model_json_schema()
        request_body: dict[str, Any] = {
            "model": self.settings.GROQ_LLM_MODEL,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "متن گفتار فارسی درباره جستجوی لپ‌تاپ را به JSON تبدیل کن. "
                        "فقط فیلدهای brand, model, cpu, ram, gpu, storage را برگردان؛ "
                        "هر فیلدی که صریحاً درخواست نشده null باشد. هیچ مقدار یا مشخصه‌ای را حدس نزن. "
                        f"JSON schema مرجع: {json.dumps(schema, ensure_ascii=False)}"
                    ),
                },
                {"role": "user", "content": transcript},
            ],
        }
        timeout = aiohttp.ClientTimeout(total=45)
        async with aiohttp.ClientSession(timeout=timeout) as client:
            async with client.post(
                f"{GROQ_BASE_URL}/chat/completions",
                headers={**self._headers(), "Content-Type": "application/json"},
                json=request_body,
            ) as response:
                payload = await response.json(content_type=None)
                if response.status >= 400:
                    raise RuntimeError(_api_error(payload, response.status))

        try:
            content = payload["choices"][0]["message"]["content"]
            parsed = json.loads(content)
            return LaptopVoiceFilters.model_validate(parsed)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError("پاسخ ساختاریافته‌ی دستیار صوتی معتبر نبود.") from exc

    async def understand_audio(self, audio: bytes) -> tuple[str, LaptopVoiceFilters]:
        transcript = await self.transcribe_audio(audio)
        filters = await self.extract_laptop_filters(transcript)
        if not filters.as_filters():
            raise ValueError("مدل یا مشخصه‌ای از درخواست صوتی تشخیص داده نشد؛ لطفاً مدل را واضح‌تر بگویید.")
        return transcript, filters


def _api_error(payload: Any, status: int) -> str:
    if isinstance(payload, dict):
        error = payload.get("error", {})
        if isinstance(error, dict) and error.get("message"):
            return f"سرویس هوش مصنوعی خطا داد ({status}): {error['message']}"
    return f"سرویس هوش مصنوعی خطا داد ({status})."
