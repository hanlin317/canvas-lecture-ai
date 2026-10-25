from __future__ import annotations

import os

from app.ai.base import AIProvider, AIRequest


class OpenAIProvider(AIProvider):
    name = "openai"

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key

    async def generate_note(self, request: AIRequest) -> str:
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:
            raise RuntimeError("Install the ai extra to use OpenAI: pip install -e '.[ai]'") from exc
        api_key = self.api_key or os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("No OpenAI API key is configured.")
        client = AsyncOpenAI(api_key=api_key)
        response = await client.chat.completions.create(
            model=request.model,
            temperature=request.temperature,
            messages=[
                {"role": "system", "content": request.system_prompt},
                {"role": "user", "content": request.user_prompt},
            ],
        )
        return response.choices[0].message.content or ""


class OpenAICompatibleProvider(AIProvider):
    def __init__(self, *, name: str, base_url: str, api_key: str) -> None:
        self.name = name
        self.base_url = base_url
        self.api_key = api_key

    async def generate_note(self, request: AIRequest) -> str:
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:
            raise RuntimeError("Install the ai extra to use OpenAI-compatible endpoints.") from exc
        client = AsyncOpenAI(base_url=self.base_url, api_key=self.api_key)
        response = await client.chat.completions.create(
            model=request.model,
            temperature=request.temperature,
            messages=[
                {"role": "system", "content": request.system_prompt},
                {"role": "user", "content": request.user_prompt},
            ],
        )
        return response.choices[0].message.content or ""


class LocalOpenAICompatibleProvider(OpenAICompatibleProvider):
    def __init__(self) -> None:
        super().__init__(
            name="local_openai",
            base_url=os.environ.get("LOCAL_OPENAI_BASE_URL", "http://127.0.0.1:11434/v1"),
            api_key=os.environ.get("LOCAL_OPENAI_API_KEY", "local"),
        )
