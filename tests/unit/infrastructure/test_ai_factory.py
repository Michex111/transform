"""Tests for assistant model transport selection (``build_llm_port``).

The factory is where a configured backend becomes a concrete transport. The
behaviour worth asserting is that ``gemini`` reaches Google's OpenAI-compatibility
endpoint (and never inherits the OpenAI default), and that ``echo`` still needs
no key — a mis-resolution here is invisible until a user's turn 401s.
"""

from src.infrastructure.adapters.ai.echo_llm_adapter import EchoLlmAdapter
from src.infrastructure.adapters.ai.factory import build_llm_port
from src.infrastructure.adapters.ai.openai_llm_adapter import OpenAiLlmAdapter
from src.infrastructure.config.settings import Settings


def _chat_endpoint(port: OpenAiLlmAdapter) -> str:
    """The URL the adapter would POST a completion to.

    Built through the adapter's own client, so the assertion covers the base
    URL *and* the path join the real request uses.
    """
    return str(port._client.build_request("POST", "/chat/completions").url)


def _settings(**overrides) -> Settings:
    base = {
        "ENVIRONMENT": "development",
        "SECRET_KEY": "s" * 40,
        "REDIS_URL": "redis://localhost:6379/0",
        "DATABASE_URL": "postgresql+asyncpg://u:p@localhost/db",
        "BACKBLAZE_ENDPOINT": "s3.amazonaws.com",
        "BACKBLAZE_ACCESS_KEY": "ak",
        "BACKBLAZE_SECRET_KEY": "sk",
        "BASE_TARGET_KEY": "output/",
        "AI_MODEL_STANDARD": "",
        "AI_MODEL_ADVANCED": "",
        "AI_MODEL_PRIORITY": "",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[call-arg]


def test_gemini_builds_the_openai_wire_adapter_at_googles_endpoint() -> None:
    settings = _settings(
        AI_BACKEND="gemini",
        AI_API_KEY="AIza-test",
        AI_MODEL="gemini-2.5-flash-lite",
    )

    port = build_llm_port(settings)

    assert isinstance(port, OpenAiLlmAdapter)
    assert port.model == "gemini-2.5-flash-lite"
    # The request the adapter actually makes — the provider-aware base URL, not
    # AI_BASE_URL's OpenAI default. Asserting the resolved endpoint (rather than
    # the raw base_url) is also immune to httpx's trailing-slash normalisation.
    assert _chat_endpoint(port) == (
        "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
    )


def test_gemini_honours_an_explicit_model_override() -> None:
    settings = _settings(AI_BACKEND="gemini", AI_API_KEY="AIza-test", AI_MODEL="base")

    assert build_llm_port(settings, model="gemini-flash-lite-latest").model == (
        "gemini-flash-lite-latest"
    )


def test_openai_keeps_ai_base_url() -> None:
    settings = _settings(
        AI_BACKEND="openai",
        AI_API_KEY="sk-test",
        AI_BASE_URL="https://api.groq.com/openai/v1",
    )

    port = build_llm_port(settings)

    assert isinstance(port, OpenAiLlmAdapter)
    assert _chat_endpoint(port) == "https://api.groq.com/openai/v1/chat/completions"


def test_echo_needs_no_key() -> None:
    settings = _settings(AI_BACKEND="echo", AI_API_KEY=None)

    assert isinstance(build_llm_port(settings), EchoLlmAdapter)
