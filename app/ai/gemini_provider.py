from __future__ import annotations

from urllib.parse import quote

import httpx

from app.ai.base import AIProvider, AIRequest


class GeminiProvider(AIProvider):
    name = "gemini"

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key

    async def generate_note(self, request: AIRequest) -> str:
        if not self.api_key:
            raise RuntimeError("No Google Gemini API key is configured.")
        model = quote(request.model, safe="-._")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(
                url,
                params={"key": self.api_key},
                json={
                    "systemInstruction": {"parts": [{"text": request.system_prompt}]},
                    "contents": [{"role": "user", "parts": [{"text": request.user_prompt}]}],
                    "generationConfig": {"temperature": request.temperature},
                },
            )
            response.raise_for_status()
        candidates = response.json().get("candidates", [])
        if not candidates:
            return ""
        parts = candidates[0].get("content", {}).get("parts", [])
        return "\n".join(str(part.get("text", "")) for part in parts if part.get("text"))
