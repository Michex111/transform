"""Assistant availability and per-tier entitlements — pure domain policy.

Kept free of FastAPI, Redis and SQL so the numbers can be unit-tested and
changed in one place. This is the SINGLE source of truth for every assistant
allowance: the enforcement code (service/toolbox), ``GET /assistant/status``
and the public ``GET /subscription/plans`` page all read these tables, so the
pricing page cannot drift from what the server actually enforces. Never repeat
one of these numbers anywhere else.

The policy answers, per subscription tier: may the assistant be used at all,
how many requests per hour, which model *level* to serve, and the per-turn
budgets (attachments, document bytes read, mutating actions, tool round-trips).

Every table maps a tier to a value, and every lookup fails closed for a tier
this build does not know about: the tables' most restrictive row is GUEST, so
an unknown tier gets 0 (and the ``standard`` model level) rather than whatever
the next tier happens to be.
"""

from typing import Literal

from src.domain.subscriptions.value_object.tier import SubscriptionTier

#: One mebibyte in bytes. Spelled out once so each document budget below reads
#: as "N MiB" instead of a magic product (``2 * 1024 * 1024``) nobody dares edit.
_MiB: int = 1024 * 1024

#: Capability class of the model a tier is served, independent of the concrete
#: model id (which is deployment configuration, see ``Settings.AI_MODEL_*``).
#:
#: A three-level vocabulary rather than raw model names because the tier → model
#: mapping is a business decision ("which plan gets the good model") while the
#: model id itself is an operator choice ("which provider/model implements
#: 'advanced'"). Keeping them apart lets a deployment swap providers without
#: re-teaching the domain what a paid plan deserves.
ModelLevel = Literal["standard", "advanced", "priority"]

#: Assistant turns allowed per rolling hour, per tier.
#:
#: GUEST is 0 on purpose: guests have no account, no stored files and no
#: quota to charge, so the assistant has nothing to talk about and every turn
#: would be unbilled compute on an unauthenticated endpoint. The paid tiers
#: scale roughly with their credit allowance, so a heavier plan feels heavier.
#:
#: PREMIUM and PRO share a value because they share a price point — PREMIUM is
#: a legacy alias kept for existing rows (see ``migration 0010``), not a
#: distinct entitlement.
AI_REQUESTS_PER_HOUR: dict[SubscriptionTier, int] = {
    SubscriptionTier.GUEST: 0,
    SubscriptionTier.FREE: 20,
    SubscriptionTier.PREMIUM: 60,
    SubscriptionTier.PRO: 60,
    SubscriptionTier.PRO_PLUS: 120,
    SubscriptionTier.ENTERPRISE: 600,
}

#: Model level served to each tier, as the default resolution.
#:
#: GUEST (and an unknown tier) resolves to ``standard``: a tier with no
#: allowance must not be handed the premium model even if some other check is
#: ever relaxed. PREMIUM mirrors PRO for the same legacy-alias reason as above.
_MODEL_LEVEL_BY_TIER: dict[SubscriptionTier, ModelLevel] = {
    SubscriptionTier.GUEST: "standard",
    SubscriptionTier.FREE: "standard",
    SubscriptionTier.PREMIUM: "advanced",
    SubscriptionTier.PRO: "advanced",
    SubscriptionTier.PRO_PLUS: "priority",
    SubscriptionTier.ENTERPRISE: "priority",
}

#: Human label for a model level, used by the public plans page and the status
#: endpoint. The label is deliberately the ONLY thing those endpoints expose:
#: the concrete model id (an operator/provider detail) never leaves the server.
MODEL_LEVEL_LABELS: dict[str, str] = {
    "standard": "Standard",
    "advanced": "Advanced",
    "priority": "Priority",
}

#: Files a user may attach to a SINGLE chat message.
#:
#: Bounded because every attachment is read, ownership-checked and injected
#: into the prompt: an unbounded list is an unbounded prompt and an unbounded
#: read. GUEST is 0 (no assistant at all); FREE gets one so the feature is
#: usable to try, and heavier plans scale from there.
_ATTACHMENTS_BY_TIER: dict[SubscriptionTier, int] = {
    SubscriptionTier.GUEST: 0,
    SubscriptionTier.FREE: 1,
    SubscriptionTier.PREMIUM: 3,
    SubscriptionTier.PRO: 3,
    SubscriptionTier.PRO_PLUS: 5,
    SubscriptionTier.ENTERPRISE: 5,
}

#: Largest document (bytes) a tier's assistant may read with its file tools.
#:
#: Distinct from the deployment's ``AI_MAX_DOCUMENT_BYTES`` on purpose: that is
#: the operator's hard ceiling (what the process will ever buffer), while this
#: is the per-plan entitlement. Enforcement takes the MINIMUM of the two, so
#: lowering either one on a deployment or a plan takes effect immediately.
_DOCUMENT_BYTES_BY_TIER: dict[SubscriptionTier, int] = {
    SubscriptionTier.GUEST: 0,
    SubscriptionTier.FREE: 2 * _MiB,
    SubscriptionTier.PREMIUM: 5 * _MiB,
    SubscriptionTier.PRO: 5 * _MiB,
    SubscriptionTier.PRO_PLUS: 10 * _MiB,
    SubscriptionTier.ENTERPRISE: 10 * _MiB,
}

#: Mutating actions (conversions started) allowed within a SINGLE turn.
#:
#: Separate from the hourly request quota because the two risks are different:
#: the hourly quota bounds cost and abuse over time, while this bounds the blast
#: radius of one instruction — a prompt like "convert everything to pdf" must
#: not be able to enqueue a hundred jobs (and burn a hundred times the credits)
#: from a single request. GUEST is 0 (no assistant); ENTERPRISE's 10 reflects
#: the batch workflows that plan is sold for.
_ACTIONS_BY_TIER: dict[SubscriptionTier, int] = {
    SubscriptionTier.GUEST: 0,
    SubscriptionTier.FREE: 1,
    SubscriptionTier.PREMIUM: 3,
    SubscriptionTier.PRO: 3,
    SubscriptionTier.PRO_PLUS: 5,
    SubscriptionTier.ENTERPRISE: 10,
}

#: Tool round-trips (model ⇄ tool exchanges) allowed within a SINGLE turn.
#:
#: This is an *entitlement*, not the deployment bound: the effective loop limit
#: is ``min(AI_MAX_TOOL_ITERATIONS, max_tool_iterations_for_tier(tier))``, so a
#: plan may ask for fewer steps than the server allows but never more. Bounded
#: per plan because each additional round-trip is another provider call on the
#: user's behalf.
_ITERATIONS_BY_TIER: dict[SubscriptionTier, int] = {
    SubscriptionTier.GUEST: 0,
    SubscriptionTier.FREE: 4,
    SubscriptionTier.PREMIUM: 6,
    SubscriptionTier.PRO: 6,
    SubscriptionTier.PRO_PLUS: 8,
    SubscriptionTier.ENTERPRISE: 8,
}


def _for_tier(table: dict[SubscriptionTier, int], tier: SubscriptionTier) -> int:
    """Read ``table`` for ``tier``, failing closed for an unknown tier.

    ``0`` is the fail-closed default because every table's GUEST row is 0 — a
    tier this build does not recognise (a row written by a newer release, say)
    therefore gets the most restrictive allowance, never the most generous.
    """
    return table.get(tier, 0)


def hourly_quota(tier: SubscriptionTier) -> int:
    """Assistant turns allowed per hour for ``tier`` (0 means unavailable)."""
    return AI_REQUESTS_PER_HOUR.get(tier, 0)


def can_use_assistant(tier: SubscriptionTier) -> bool:
    """Whether ``tier`` may use the assistant at all.

    An unknown tier is treated as unavailable (``hourly_quota`` returns 0): a
    tier this build does not know about must not be silently granted access.
    """
    return hourly_quota(tier) > 0


def model_level_for_tier(tier: SubscriptionTier) -> ModelLevel:
    """Model level served to ``tier`` (``"standard"`` when the tier is unknown)."""
    return _MODEL_LEVEL_BY_TIER.get(tier, "standard")


def model_label_for_tier(tier: SubscriptionTier) -> str:
    """Human label for ``tier``'s model level, safe to show on a pricing page."""
    return MODEL_LEVEL_LABELS[model_level_for_tier(tier)]


def max_attachments_for_tier(tier: SubscriptionTier) -> int:
    """Files attachable to one message for ``tier`` (0 when unknown)."""
    return _for_tier(_ATTACHMENTS_BY_TIER, tier)


def max_document_bytes_for_tier(tier: SubscriptionTier) -> int:
    """Largest document the tools may read for ``tier`` (0 when unknown)."""
    return _for_tier(_DOCUMENT_BYTES_BY_TIER, tier)


def max_actions_per_turn(tier: SubscriptionTier) -> int:
    """Mutating actions (conversions started) allowed in one turn for ``tier``."""
    return _for_tier(_ACTIONS_BY_TIER, tier)


def max_tool_iterations_for_tier(tier: SubscriptionTier) -> int:
    """Tool round-trips ``tier`` is entitled to within one turn (0 when unknown)."""
    return _for_tier(_ITERATIONS_BY_TIER, tier)
