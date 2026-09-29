"""Assistant model selection and package exports."""

from src.application.ports.llm_port import LlmPort
from src.infrastructure.adapters.ai.echo_llm_adapter import EchoLlmAdapter
from src.infrastructure.adapters.ai.openai_llm_adapter import OpenAiLlmAdapter

__all__ = [
    "EchoLlmAdapter",
    "OpenAiLlmAdapter",
    "build_llm_port",
]


def build_llm_port(settings, model: str | None = None) -> LlmPort:
    """Return the ``LlmPort`` implementation for the configured backend.

    Takes the settings object explicitly (rather than calling ``get_settings``)
    so tests can drive every branch without touching the environment, mirroring
    ``build_email_sender``.

    ``model`` overrides the model id for this instance (used by
    ``AssistantModelRegistry`` to build the per-level models); when omitted the
    deployment default ``AI_MODEL`` is used, so the single-argument call still
    means "the model this deployment is configured with".

    The backend is resolved through ``settings._resolve_ai_backend()`` rather
    than read directly, so the ``auto`` rule (a key means a real model) lives in
    exactly one place.
    """
    if settings._resolve_ai_backend() == "openai":
        # validate_settings() already rejected ``openai`` without a key, so the
        # None check here is only to satisfy the type — not a second policy.
        if settings.AI_API_KEY is None:
            raise RuntimeError("AI_BACKEND=openai requires AI_API_KEY.")
        return OpenAiLlmAdapter(
            api_key=settings.AI_API_KEY.get_secret_value().strip(),
            base_url=settings.AI_BASE_URL,
            model=model or settings.AI_MODEL,
            max_tokens=settings.AI_MAX_TOKENS,
            temperature=settings.AI_TEMPERATURE,
            timeout_seconds=settings.AI_REQUEST_TIMEOUT_SECONDS,
        )
    return EchoLlmAdapter()
