"""AI assistant adapters.

Import from the concrete modules (``openai_llm_adapter``, ...) rather than this
package when you need a specific class; this module re-exports the public
surface for convenience.
"""

from src.application.ports.llm_port import LlmUnavailableError
from src.infrastructure.adapters.ai.echo_llm_adapter import EchoLlmAdapter
from src.infrastructure.adapters.ai.factory import build_llm_port
from src.infrastructure.adapters.ai.openai_llm_adapter import (
    LlmRequestError,
    OpenAiLlmAdapter,
)
from src.infrastructure.adapters.ai.redis_quota import RedisAssistantQuota
from src.infrastructure.adapters.ai.registry import AssistantModelRegistry

__all__ = [
    "AssistantModelRegistry",
    "EchoLlmAdapter",
    "LlmRequestError",
    "LlmUnavailableError",
    "OpenAiLlmAdapter",
    "RedisAssistantQuota",
    "build_llm_port",
]
