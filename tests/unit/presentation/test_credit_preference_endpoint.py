"""The credit spend-order preference endpoint.

The wallet balances are deliberately NOT settable here. They are derived from
payments (purchases) and consumption (conversions), so an endpoint that could
write them would be a way to mint credits. Only the ordering preference — which
is a genuine user choice with no balance consequence — is accepted.
"""

import asyncio
from types import SimpleNamespace
from typing import Any, cast

from src.presentation.api.routers.v1.credits import set_credit_preference
from src.presentation.schemas.credit import (
    CreditPreferenceRequest,
    CreditPreferenceResponse,
)


class _RecordingSubscriptionRepo:
    def __init__(self) -> None:
        self.calls: list[tuple[int, bool]] = []

    async def set_credit_preference(self, user_id: int, purchased_credits_first: bool) -> None:
        self.calls.append((user_id, purchased_credits_first))


def _call(value: bool, repo) -> CreditPreferenceResponse:
    # Duck-typed doubles cast at the injection boundary, matching the
    # convention used by the other direct-router-call tests.
    return asyncio.run(
        set_credit_preference(
            payload=CreditPreferenceRequest(purchased_credits_first=value),
            current_user=cast(Any, SimpleNamespace(id=7)),
            subscription_repo=cast(Any, repo),
        )
    )


def test_records_the_preference_and_echoes_it_back() -> None:
    repo = _RecordingSubscriptionRepo()

    response = _call(True, repo)

    assert repo.calls == [(7, True)]
    assert response.purchased_credits_first is True


def test_turning_it_off_is_also_recorded() -> None:
    # The default is false; clearing it must be as expressible as setting it.
    repo = _RecordingSubscriptionRepo()

    response = _call(False, repo)

    assert repo.calls == [(7, False)]
    assert response.purchased_credits_first is False
