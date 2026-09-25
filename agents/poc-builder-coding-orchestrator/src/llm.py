"""LLM provider selection for POC Builder agents."""

from __future__ import annotations

import logging
import os
from typing import Any, cast

from langchain_core.language_models import BaseChatModel

logger = logging.getLogger(__name__)

DEFAULT_PROVIDER = "openai"
DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-4-6",
    "cerebras": "qwen-3-235b-a22b-instruct-2507",
    "gemini": "gemini-2.5-flash",
    "openai": "gpt-5.1",
    "xai": "grok-4.6",
}


def build_llm(
    provider: str | None = None,
    temperature: float = 0,
) -> BaseChatModel:
    """Create a configured provider model using environment-only credentials."""
    configured_provider = (
        (provider or os.getenv("LLM_PROVIDER", DEFAULT_PROVIDER)).strip().lower()
    )
    model_name = os.getenv(
        f"{configured_provider.upper()}_MODEL",
        DEFAULT_MODELS.get(configured_provider, ""),
    )
    gemini_key = os.getenv("GEMINI_API_KEY", "")
    openai_key = os.getenv("OPENAI_API_KEY", "")
    anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")
    cerebras_key = os.getenv("CEREBRAS_API_KEY", "")
    xai_key = os.getenv("XAI_API_KEY", "")

    def build_gemini() -> BaseChatModel:
        from langchain_google_genai import ChatGoogleGenerativeAI

        kwargs: dict[str, Any] = {
            "api_key": gemini_key,
            "model": model_name,
            "temperature": temperature,
        }
        if model_name.startswith("gemini-2.5-"):
            kwargs["thinking_budget"] = 0
        model_class: Any = ChatGoogleGenerativeAI
        return cast(BaseChatModel, model_class(**kwargs))

    def build_openai() -> BaseChatModel:
        from langchain_openai import ChatOpenAI

        kwargs: dict[str, Any] = {
            "api_key": openai_key,
            "model": model_name,
            "temperature": temperature,
        }
        base_url = os.getenv("OPENAI_BASE_URL", "")
        api_key_header = os.getenv("OPENAI_API_KEY_HEADER", "")
        if base_url:
            kwargs["base_url"] = base_url.rstrip("/")
            if "/openai/deployments/" in base_url.lower():
                kwargs["default_query"] = {
                    "api-version": os.getenv(
                        "AZURE_OPENAI_API_VERSION", "2024-12-01-preview"
                    )
                }
                kwargs["default_headers"] = {"api-key": openai_key}
            if api_key_header:
                kwargs.setdefault("default_headers", {})[api_key_header] = openai_key
        model_class: Any = ChatOpenAI
        return cast(BaseChatModel, model_class(**kwargs))

    def build_anthropic() -> BaseChatModel:
        from langchain_anthropic import ChatAnthropic

        kwargs: dict[str, Any] = {
            "api_key": anthropic_key,
            "model_name": model_name,
            "temperature": temperature,
        }
        base_url = os.getenv("ANTHROPIC_BASE_URL", "")
        api_key_header = os.getenv("ANTHROPIC_API_KEY_HEADER", "")
        if base_url:
            kwargs["base_url"] = base_url.rstrip("/")
            if api_key_header:
                kwargs["default_headers"] = {api_key_header: anthropic_key}
        model_class: Any = ChatAnthropic
        return cast(BaseChatModel, model_class(**kwargs))

    def build_cerebras() -> BaseChatModel:
        from langchain_cerebras import ChatCerebras

        model_class: Any = ChatCerebras
        return cast(
            BaseChatModel,
            model_class(
                api_key=cerebras_key, model=model_name, temperature=temperature
            ),
        )

    def build_xai() -> BaseChatModel:
        from langchain_xai import ChatXAI

        model_class: Any = ChatXAI
        return cast(
            BaseChatModel,
            model_class(api_key=xai_key, model=model_name, temperature=temperature),
        )

    builders = {
        "gemini": (gemini_key, build_gemini),
        "openai": (openai_key, build_openai),
        "anthropic": (anthropic_key, build_anthropic),
        "cerebras": (cerebras_key, build_cerebras),
        "xai": (xai_key, build_xai),
    }
    if configured_provider not in builders:
        supported = ", ".join(sorted(builders))
        raise RuntimeError(
            f"Unsupported provider {configured_provider!r}. Use one of: {supported}."
        )
    provider_key, provider_builder = builders[configured_provider]
    if not provider_key:
        raise RuntimeError(
            f"POC Builder is configured for {configured_provider!r}, but its API key is missing."
        )
    logger.info("Using %s LLM: %s", configured_provider, model_name)
    return provider_builder()
