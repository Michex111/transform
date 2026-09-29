"""Tier → model adapter registry: one ``LlmPort`` per model id, cached.

WHY a registry object and not a per-request ``build_llm_port`` call: the OpenAI
adapter owns an ``httpx.AsyncClient``, and therefore a connection pool (and the
provider's keep-alive). Building one per message would hand every turn a cold
pool and leak the previous client, which shows up as a slow, resource-hungry
assistant under load. The numbers are cheap to *resolve* (a dict lookup) but
expensive to *construct*, so the resolution is mapped onto a cache keyed by the
fully-resolved model id — two tiers that share a level (PRO and PREMIUM, say)
get the exact same adapter instance.

The cache is a plain dict on the instance, deliberately not ``functools.lru_cache``:
the resolver is built from a settings object, and ``lru_cache`` on a
settings-derived function would keep a stale adapter (and its pool) alive across
a reload of the settings.
"""

from src.application.ports.llm_port import LlmPort
from src.domain.assistant.policies.assistant_policy import model_level_for_tier
from src.domain.subscriptions.value_object.tier import SubscriptionTier
from src.infrastructure.adapters.ai.factory import build_llm_port


class AssistantModelRegistry:
    """Resolves a subscription tier to a cached model transport.

    Implements :class:`AssistantModelResolver`. Construct once per process (see
    ``get_assistant_model_registry``); the instance is stateless apart from the
    adapter cache and is safe to share across requests.
    """

    def __init__(self, settings) -> None:
        self._settings = settings
        #: model id -> adapter. Keyed by the resolved id, not by tier, so tiers
        #: that share a level genuinely share the one pooled adapter.
        self._ports: dict[str, LlmPort] = {}

    def for_tier(self, tier: SubscriptionTier) -> LlmPort:
        """The ``LlmPort`` for ``tier``, building it at most once per model id.

        The tier is mapped to a domain model *level* here, and the level to a
        concrete model id by the deployment settings — so swapping providers is
        a configuration change, while "which plan gets the better model" stays
        a domain decision.
        """
        level = model_level_for_tier(tier)
        model_id = self._settings.ai_model_for_level(level)
        port = self._ports.get(model_id)
        if port is None:
            port = build_llm_port(self._settings, model=model_id)
            self._ports[model_id] = port
        return port
