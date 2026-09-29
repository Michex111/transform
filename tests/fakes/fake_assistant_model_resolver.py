"""Fake ``AssistantModelResolver`` for tests.

The assistant resolves a model per subscription tier, so a test needs to (a)
supply a scripted ``LlmPort`` for every tier a code path touches and (b) assert
which tier was asked for. This double does both: it maps tiers to ports (with an
optional catch-all default) and records the resolutions in order.

Recording the *tier* is the point — it is how "a FREE caller is served the
standard model and a PRO caller the advanced one" is asserted without a
provider. Tests that also care about the resolved model id should use distinct
``FakeLlmPort(model=...)`` instances per tier.
"""

from src.application.ports.llm_port import LlmPort
from src.domain.subscriptions.value_object.tier import SubscriptionTier


class FakeAssistantModelResolver:
    """Resolves every tier to a mapped port, falling back to a default."""

    def __init__(
        self,
        default: LlmPort | None = None,
        by_tier: dict[SubscriptionTier, LlmPort] | None = None,
    ) -> None:
        self._default = default
        self._by_tier = dict(by_tier or {})
        #: Tiers asked for, in order. Assert on this to prove the tier (not a
        #: global model) drove resolution.
        self.resolved: list[SubscriptionTier] = []

    def for_tier(self, tier: SubscriptionTier) -> LlmPort:
        self.resolved.append(tier)
        port = self._by_tier.get(tier, self._default)
        if port is None:
            raise AssertionError(
                f"FakeAssistantModelResolver has no port for tier {tier!r}; "
                "pass a default or a by_tier entry."
            )
        return port
