from __future__ import annotations

import httpx

from app.ai.base import AIProvider, AIRequest


class AnthropicProvider(AIProvider):
    name = "anthropic"

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key

    async def generate_note(self, request: AIRequest) -> str:
        if not self.api_key:
            raise RuntimeError("No Anthropic API key is configured.")
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": request.model,
                    "max_tokens": request.max_output_tokens,
                    "temperature": request.temperature,
                    "system": request.system_prompt,
                    "messages": [{"role": "user", "content": request.user_prompt}],
                },
            )
            response.raise_for_status()
        blocks = response.json().get("content", [])
        return "\n".join(
            str(block.get("text", "")) for block in blocks if block.get("type") == "text"
        )
