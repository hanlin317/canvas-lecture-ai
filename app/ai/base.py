from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class AIRequest:
    system_prompt: str
    user_prompt: str
    model: str
    temperature: float = 0.2
    max_output_tokens: int = 4096


class AIProvider(ABC):
    name: str

    @abstractmethod
    async def generate_note(self, request: AIRequest) -> str:
        raise NotImplementedError
