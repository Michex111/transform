"""Port that turns a subscription tier into the ``LlmPort`` that serves it.

WHY this is separate from ``llm_port``: ``llm_port`` describes *what a model
adapter is* (a model id and a streaming completion call) and is deliberately
free of any domain concept — it must stay importable by a vendor adapter that
knows nothing about subscriptions. "Which model does this plan get" is a
different question with a different reason to change, so it lives here, where
importing ``SubscriptionTier`` is natural.

WHY a resolver object and not a plain function: resolving a tier maps to a
concrete adapter instance, and that instance owns a connection pool. The caller
must be able to ask repeatedly and get the *same* instance back (see
``AssistantModelRegistry``), which is a property of an object with a cache, not
of a stateless function.
"""

from typing import Protocol, runtime_checkable

from src.application.ports.llm_port import LlmPort
from src.domain.subscriptions.value_object.tier import SubscriptionTier


@runtime_checkable
class AssistantModelResolver(Protocol):
    """Resolves the model transport that serves a given subscription tier."""

    def for_tier(self, tier: SubscriptionTier) -> LlmPort:
        """Return the ``LlmPort`` for ``tier``.

        Implementations must be free to cache: the returned port may be a
        shared, long-lived adapter (one per model id), so callers must not
        mutate it or assume a fresh connection per call. An unrecognised tier
        must resolve to the deployment's default model, never to an error —
        a tier this build does not know about is not a reason to break chat.
        """
        ...
