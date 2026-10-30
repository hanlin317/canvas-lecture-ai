from __future__ import annotations

import os

import httpx

from app.ai.base import AIProvider, AIRequest


class OpenAICompatibleProvider(AIProvider):
    max_tokens_field = "max_tokens"

    def __init__(self, *, name: str, base_url: str, api_key: str) -> None:
        self.name = name
        self.base_url = base_url
        self.api_key = api_key

    async def generate_note(self, request: AIRequest) -> str:
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(
                f"{self.base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": request.model,
                    "temperature": request.temperature,
                    self.max_tokens_field: request.max_output_tokens,
                    "messages": [
                        {"role": "system", "content": request.system_prompt},
                        {"role": "user", "content": request.user_prompt},
                    ],
                },
            )
            response.raise_for_status()
        choices = response.json().get("choices", [])
        return str(choices[0].get("message", {}).get("content", "")) if choices else ""


class OpenAIProvider(OpenAICompatibleProvider):
    name = "openai"
    max_tokens_field = "max_completion_tokens"

    def __init__(self, api_key: str | None = None) -> None:
        key = api_key or os.environ.get("OPENAI_API_KEY")
        if not key:
            raise RuntimeError("No OpenAI API key is configured.")
        super().__init__(name=self.name, base_url="https://api.openai.com/v1", api_key=key)


class LocalOpenAICompatibleProvider(OpenAICompatibleProvider):
    def __init__(self) -> None:
        super().__init__(
            name="local_openai",
            base_url=os.environ.get("LOCAL_OPENAI_BASE_URL", "http://127.0.0.1:11434/v1"),
            api_key=os.environ.get("LOCAL_OPENAI_API_KEY", "local"),
        )
