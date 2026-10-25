from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass

import httpx

from app.ai.anthropic_provider import AnthropicProvider
from app.ai.base import AIProvider
from app.ai.gemini_provider import GeminiProvider
from app.ai.openai_provider import (
    LocalOpenAICompatibleProvider,
    OpenAICompatibleProvider,
    OpenAIProvider,
)

KEYCHAIN_SERVICE = "Canvas Lecture AI API Keys"
LEGACY_KEYCHAIN_SERVICE = "Dental Lecture Manager API Keys"


@dataclass(frozen=True)
class AIModelSpec:
    id: str
    label: str


@dataclass(frozen=True)
class AIProviderSpec:
    name: str
    label: str
    default_model: str
    api_key_env: str | None
    base_url: str | None = None
    models: tuple[AIModelSpec, ...] = ()

    @property
    def requires_key(self) -> bool:
        return self.api_key_env is not None

    @property
    def model_ids(self) -> tuple[str, ...]:
        return tuple(model.id for model in self.models)


def model(model_id: str, label: str) -> AIModelSpec:
    return AIModelSpec(id=model_id, label=label)


AI_PROVIDER_SPECS: tuple[AIProviderSpec, ...] = (
    AIProviderSpec(
        "openai",
        "OpenAI",
        "gpt-6.1-sol",
        "OPENAI_API_KEY",
        models=(
            model("gpt-6.1-sol", "GPT-6.1 Sol"),
            model("gpt-6-astra", "GPT-6 Astra"),
            model("gpt-6-luna", "GPT-6 Luna"),
        ),
    ),
    AIProviderSpec(
        "anthropic",
        "Anthropic Claude",
        "claude-sonnet-5",
        "ANTHROPIC_API_KEY",
        models=(
            model("claude-sonnet-5", "Claude Sonnet 5"),
            model("claude-opus-5", "Claude Opus 5"),
            model("claude-fable-5", "Claude Fable 5"),
            model("claude-sonnet-4-6", "Claude Sonnet 4.6"),
            model("claude-haiku-4-5-20251001", "Claude Haiku 4.5"),
        ),
    ),
    AIProviderSpec(
        "gemini",
        "Google Gemini",
        "gemini-3.8-flash",
        "GEMINI_API_KEY",
        models=(
            model("gemini-3.8-flash", "Gemini 3.8 Flash"),
            model("gemini-3.5-flash-lite", "Gemini 3.5 Flash-Lite"),
            model("gemini-3.1-pro-preview", "Gemini 3.1 Pro Preview"),
            model("gemini-2.5-pro", "Gemini 2.5 Pro"),
            model("gemini-2.5-flash", "Gemini 2.5 Flash"),
        ),
    ),
    AIProviderSpec(
        "openrouter",
        "OpenRouter",
        "openai/gpt-6.1-sol",
        "OPENROUTER_API_KEY",
        "https://openrouter.ai/api/v1",
        models=(
            model("openai/gpt-6.1-sol", "OpenAI GPT-6.1 Sol"),
            model("anthropic/claude-sonnet-5", "Anthropic Claude Sonnet 5"),
            model("google/gemini-3.8-flash", "Google Gemini 3.8 Flash"),
            model("x-ai/grok-4.7", "xAI Grok 4.7"),
            model("deepseek/deepseek-v4.1-flash", "DeepSeek V4.1 Flash"),
        ),
    ),
    AIProviderSpec(
        "xai",
        "xAI Grok",
        "grok-4.7",
        "XAI_API_KEY",
        "https://api.x.ai/v1",
        models=(
            model("grok-4.7", "Grok 4.7"),
            model("grok-4.6", "Grok 4.6"),
        ),
    ),
    AIProviderSpec(
        "deepseek",
        "DeepSeek",
        "deepseek-flash",
        "DEEPSEEK_API_KEY",
        "https://api.deepseek.com/v1",
        models=(
            model("deepseek-flash", "DeepSeek V4.1 Flash"),
            model("deepseek-v4-pro", "DeepSeek V4 Pro"),
        ),
    ),
    AIProviderSpec(
        "mistral",
        "Mistral",
        "mistral-medium-3-5",
        "MISTRAL_API_KEY",
        "https://api.mistral.ai/v1",
        models=(
            model("mistral-medium-3-5", "Mistral Medium 3.5"),
            model("mistral-large-4", "Mistral Large 4 Preview"),
            model("mistral-small-2603", "Mistral Small 4"),
            model("mistral-large-2512", "Mistral Large 3"),
        ),
    ),
    AIProviderSpec(
        "local_openai",
        "Local / Ollama",
        "llama3.2",
        None,
        models=(
            model("llama3.2", "Llama 3.2"),
            model("qwen2.5", "Qwen 2.5"),
            model("mistral", "Mistral (local)"),
        ),
    ),
)


def _run_security(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["security", *arguments],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return subprocess.CompletedProcess(
            ["security", *arguments],
            returncode=127,
            stdout="",
            stderr="macOS security tool is unavailable.",
        )


def get_ai_provider_spec(name: str) -> AIProviderSpec:
    try:
        return next(spec for spec in AI_PROVIDER_SPECS if spec.name == name)
    except StopIteration as exc:
        raise ValueError(f"Unknown AI provider: {name}") from exc


def api_key_status(name: str) -> str:
    spec = get_ai_provider_spec(name)
    if not spec.requires_key:
        return "not required"
    if os.environ.get(spec.api_key_env or ""):
        return "environment"
    for service in (KEYCHAIN_SERVICE, LEGACY_KEYCHAIN_SERVICE):
        result = _run_security(["find-generic-password", "-a", name, "-s", service])
        if result.returncode == 0:
            return "Keychain"
    return "missing"


def load_api_key(name: str) -> str | None:
    spec = get_ai_provider_spec(name)
    if not spec.requires_key:
        return None
    environment_key = os.environ.get(spec.api_key_env or "")
    if environment_key:
        return environment_key
    for service in (KEYCHAIN_SERVICE, LEGACY_KEYCHAIN_SERVICE):
        result = _run_security(["find-generic-password", "-a", name, "-s", service, "-w"])
        key = result.stdout.strip()
        if result.returncode == 0 and key:
            return key
    raise RuntimeError(
        f"No {spec.label} API key is configured. Save one in AI Settings or set {spec.api_key_env}."
    )


def save_api_key(name: str, value: str) -> None:
    spec = get_ai_provider_spec(name)
    key = value.strip()
    if not spec.requires_key:
        raise ValueError(f"{spec.label} does not require an API key.")
    if not key:
        raise ValueError("API key cannot be empty.")
    result = _run_security(
        [
            "add-generic-password",
            "-U",
            "-a",
            name,
            "-s",
            KEYCHAIN_SERVICE,
            "-w",
            key,
        ]
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Could not save API key in macOS Keychain.")


def _supports_note_generation(provider: str, model_id: str, payload: dict) -> bool:
    lowered = model_id.lower()
    blocked = (
        "audio",
        "embed",
        "image",
        "moderation",
        "ocr",
        "realtime",
        "search",
        "transcribe",
        "tts",
        "video",
        "voice",
        "whisper",
    )
    if any(term in lowered for term in blocked):
        return False
    if provider == "openai":
        return lowered.startswith(("gpt-", "o1", "o3", "o4"))
    if provider == "anthropic":
        return lowered.startswith("claude-")
    if provider == "gemini":
        methods = payload.get("supportedGenerationMethods", [])
        return lowered.startswith("gemini-") and "generateContent" in methods
    if provider == "xai":
        return lowered.startswith("grok-") and "imagine" not in lowered
    if provider == "deepseek":
        return lowered.startswith("deepseek-")
    if provider == "mistral":
        return any(
            lowered.startswith(prefix)
            for prefix in ("mistral-", "ministral-", "magistral-", "devstral-")
        )
    if provider == "openrouter":
        output_modalities = payload.get("architecture", {}).get("output_modalities", [])
        return not output_modalities or "text" in output_modalities
    return True


async def fetch_provider_models(name: str) -> tuple[AIModelSpec, ...]:
    spec = get_ai_provider_spec(name)
    api_key = load_api_key(name)
    headers: dict[str, str] = {}
    params: dict[str, str] = {}
    if name == "openai":
        url = "https://api.openai.com/v1/models"
        headers["Authorization"] = f"Bearer {api_key}"
    elif name == "anthropic":
        url = "https://api.anthropic.com/v1/models"
        headers.update({"x-api-key": api_key or "", "anthropic-version": "2023-06-01"})
    elif name == "gemini":
        url = "https://generativelanguage.googleapis.com/v1beta/models"
        params["key"] = api_key or ""
    elif name == "local_openai":
        base_url = os.environ.get("LOCAL_OPENAI_BASE_URL", "http://127.0.0.1:11434/v1")
        url = f"{base_url.rstrip('/')}/models"
        local_key = os.environ.get("LOCAL_OPENAI_API_KEY")
        if local_key:
            headers["Authorization"] = f"Bearer {local_key}"
    else:
        url = f"{(spec.base_url or '').rstrip('/')}/models"
        headers["Authorization"] = f"Bearer {api_key}"

    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        response = await client.get(url, headers=headers, params=params)
        response.raise_for_status()
    body = response.json()
    items = body.get("models", []) if name == "gemini" else body.get("data", [])
    discovered: list[tuple[int, AIModelSpec]] = []
    for item in items:
        raw_id = str(item.get("name" if name == "gemini" else "id", ""))
        model_id = raw_id.removeprefix("models/")
        if not model_id or not _supports_note_generation(name, model_id, item):
            continue
        label = str(
            item.get("displayName") or item.get("display_name") or item.get("name") or model_id
        ).removeprefix("models/")
        discovered.append((int(item.get("created", 0) or 0), model(model_id, label)))

    discovered.sort(key=lambda entry: (-entry[0], entry[1].label.casefold()))
    limit = 200 if name == "openrouter" else 100
    unique: dict[str, AIModelSpec] = {}
    for _, discovered_model in discovered:
        unique.setdefault(discovered_model.id, discovered_model)
        if len(unique) >= limit:
            break
    if not unique:
        raise RuntimeError(f"{spec.label} returned no compatible text-generation models.")
    return tuple(unique.values())


def get_ai_provider(name: str) -> AIProvider:
    spec = get_ai_provider_spec(name)
    api_key = load_api_key(name)
    if name == "openai":
        return OpenAIProvider(api_key=api_key)
    if name == "anthropic":
        return AnthropicProvider(api_key=api_key)
    if name == "gemini":
        return GeminiProvider(api_key=api_key)
    if name == "local_openai":
        return LocalOpenAICompatibleProvider()
    return OpenAICompatibleProvider(name=name, base_url=spec.base_url or "", api_key=api_key or "")
