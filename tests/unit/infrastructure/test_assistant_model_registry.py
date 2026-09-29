"""Unit tests for the tier → model adapter registry.

The registry is where the two halves of model selection meet — the domain's
"which level does this plan get" and the deployment's "which model id is that
level" — and where the caching that protects the provider connection pool lives.
Both are asserted here rather than through the API, because the interesting
behaviour (identity of the returned adapter) is invisible at the HTTP layer.
"""

from src.domain.subscriptions.value_object.tier import SubscriptionTier
from src.infrastructure.adapters.ai.registry import AssistantModelRegistry
from src.infrastructure.config.settings import Settings


def _settings(**overrides) -> Settings:
    """Minimal valid settings, defaulting to a two-model OpenAI deployment.

    The per-level AI models are pinned BLANK here so the suite is hermetic:
    pydantic-settings reads the developer's real ``.env`` on top of these
    values, so without the explicit blanks a machine that sets
    ``AI_MODEL_STANDARD`` would silently change what "blank falls back" means
    and the fallback tests would fail for a reason that is not in the code.
    """
    base = {
        "ENVIRONMENT": "development",
        "SECRET_KEY": "s" * 40,
        "REDIS_URL": "redis://localhost:6379/0",
        "DATABASE_URL": "postgresql+asyncpg://u:p@localhost/db",
        "BACKBLAZE_ENDPOINT": "s3.amazonaws.com",
        "BACKBLAZE_ACCESS_KEY": "ak",
        "BACKBLAZE_SECRET_KEY": "sk",
        "BASE_TARGET_KEY": "output/",
        "AI_BACKEND": "openai",
        "AI_API_KEY": "sk-test",
        "AI_MODEL": "base-model",
        "AI_MODEL_STANDARD": "",
        "AI_MODEL_ADVANCED": "",
        "AI_MODEL_PRIORITY": "",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[call-arg]


def test_each_level_resolves_to_its_configured_model() -> None:
    registry = AssistantModelRegistry(
        _settings(
            AI_MODEL_STANDARD="std-model",
            AI_MODEL_ADVANCED="adv-model",
            AI_MODEL_PRIORITY="pri-model",
        )
    )

    assert registry.for_tier(SubscriptionTier.FREE).model == "std-model"
    assert registry.for_tier(SubscriptionTier.PRO).model == "adv-model"
    assert registry.for_tier(SubscriptionTier.PRO_PLUS).model == "pri-model"


def test_blank_levels_share_the_default_model_and_one_adapter() -> None:
    """A single-model deployment must build exactly one transport."""
    registry = AssistantModelRegistry(_settings(AI_MODEL="base-model"))

    free = registry.for_tier(SubscriptionTier.FREE)
    pro = registry.for_tier(SubscriptionTier.PRO)

    assert free.model == "base-model"
    assert pro.model == "base-model"
    # Same resolved model id -> the very same cached adapter (one connection pool).
    assert free is pro


def test_tiers_that_share_a_model_share_the_cached_adapter() -> None:
    registry = AssistantModelRegistry(
        _settings(
            AI_MODEL_STANDARD="shared",
            AI_MODEL_ADVANCED="shared",
            AI_MODEL_PRIORITY="shared",
        )
    )

    assert registry.for_tier(SubscriptionTier.FREE) is registry.for_tier(
        SubscriptionTier.ENTERPRISE
    )


def test_distinct_models_get_distinct_cached_adapters() -> None:
    registry = AssistantModelRegistry(
        _settings(AI_MODEL_STANDARD="std-model", AI_MODEL_PRIORITY="pri-model")
    )

    standard = registry.for_tier(SubscriptionTier.FREE)
    priority = registry.for_tier(SubscriptionTier.PRO_PLUS)

    assert standard is not priority
    # A repeat lookup is served from the cache, not rebuilt.
    assert standard is registry.for_tier(SubscriptionTier.FREE)
    assert priority is registry.for_tier(SubscriptionTier.ENTERPRISE)


def test_the_echo_backend_resolves_for_every_tier() -> None:
    """With no key configured every plan still resolves (to the offline engine)."""
    registry = AssistantModelRegistry(_settings(AI_BACKEND="echo", AI_API_KEY=None))

    for tier in SubscriptionTier:
        assert registry.for_tier(tier).model == "echo"
