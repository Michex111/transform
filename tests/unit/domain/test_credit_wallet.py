"""The credit-wallet spending rules.

These encode the money behaviour, so they are pinned exactly: which population
is spent, in what order, and what happens at an expiry boundary. The domain
module is pure and takes ``now`` explicitly, which is what lets the expiry
boundary be tested at the instant rather than approximately.
"""

from datetime import UTC, datetime, timedelta

import pytest

from src.domain.subscriptions.policies.credit_wallet import (
    WalletBalances,
    available_total,
    consume_from_wallet,
    live_carryover,
)

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
LATER = NOW + timedelta(days=10)
EARLIER = NOW - timedelta(seconds=1)


# ---------------------------------------------------------------------------
# Expiry
# ---------------------------------------------------------------------------


class TestLiveCarryover:
    def test_unexpired_carryover_is_fully_spendable(self) -> None:
        assert live_carryover(320, LATER, NOW) == 320

    def test_expired_carryover_is_gone(self) -> None:
        assert live_carryover(320, EARLIER, NOW) == 0

    def test_the_boundary_instant_itself_is_expired(self) -> None:
        # `>=`, not `>`: an expiry of 12:00 means the credits are gone AT 12:00,
        # so a job running exactly then cannot spend them.
        assert live_carryover(320, NOW, NOW) == 0

    def test_one_second_before_expiry_is_still_spendable(self) -> None:
        assert live_carryover(320, NOW + timedelta(seconds=1), NOW) == 320

    def test_no_recorded_expiry_means_it_does_not_expire(self) -> None:
        # Reading a missing timestamp as "expired" would silently destroy
        # credits, which is the exact failure this module exists to prevent.
        assert live_carryover(320, None, NOW) == 320

    @pytest.mark.parametrize("amount", [0, -5])
    def test_a_non_positive_bucket_is_never_spendable(self, amount: int) -> None:
        assert live_carryover(amount, LATER, NOW) == 0


class TestAvailableTotal:
    def test_sums_all_three_populations(self) -> None:
        balances = WalletBalances(plan_remaining=1820, carryover_credits=320, purchased_credits=1000)
        assert available_total(balances, carryover_expires_at=LATER, now=NOW) == 3140

    def test_excludes_expired_carryover(self) -> None:
        balances = WalletBalances(plan_remaining=1820, carryover_credits=320, purchased_credits=1000)
        # The carryover is already spent (expired); purchased credits survive it.
        assert available_total(balances, carryover_expires_at=EARLIER, now=NOW) == 2820


# ---------------------------------------------------------------------------
# Spending order
# ---------------------------------------------------------------------------


class TestSpendingOrder:
    def test_carryover_is_spent_first_even_when_purchased_credits_are_preferred(self) -> None:
        """Carryover goes first regardless of preference — it is the only pool
        that can be lost, so leaving it unspent would burn credits the user paid
        for previously."""
        result = consume_from_wallet(
            WalletBalances(plan_remaining=100, carryover_credits=50, purchased_credits=100),
            30,
            carryover_expires_at=LATER,
            now=NOW,
            purchased_credits_first=True,
        )
        assert result == WalletBalances(plan_remaining=100, carryover_credits=20, purchased_credits=100)

    def test_expired_carryover_is_skipped_not_spent(self) -> None:
        result = consume_from_wallet(
            WalletBalances(plan_remaining=100, carryover_credits=50, purchased_credits=0),
            30,
            carryover_expires_at=EARLIER,
            now=NOW,
        )
        # Only the plan pays; the dead carryover is left as stored (it is the
        # number that expired), and the plan bears the full 30.
        assert result == WalletBalances(plan_remaining=70, carryover_credits=50, purchased_credits=0)

    def test_plan_is_spent_before_purchased_by_default(self) -> None:
        result = consume_from_wallet(
            WalletBalances(plan_remaining=100, carryover_credits=0, purchased_credits=1000),
            100,
            carryover_expires_at=None,
            now=NOW,
        )
        assert result == WalletBalances(plan_remaining=0, carryover_credits=0, purchased_credits=1000)

    def test_purchased_first_reverses_that(self) -> None:
        result = consume_from_wallet(
            WalletBalances(plan_remaining=100, carryover_credits=0, purchased_credits=1000),
            100,
            carryover_expires_at=None,
            now=NOW,
            purchased_credits_first=True,
        )
        assert result == WalletBalances(plan_remaining=100, carryover_credits=0, purchased_credits=900)

    def test_spilling_from_one_pool_into_the_next(self) -> None:
        result = consume_from_wallet(
            WalletBalances(plan_remaining=10, carryover_credits=0, purchased_credits=1000),
            25,
            carryover_expires_at=None,
            now=NOW,
        )
        assert result == WalletBalances(plan_remaining=0, carryover_credits=0, purchased_credits=985)

    def test_carryover_then_plan_then_purchased_in_one_call(self) -> None:
        result = consume_from_wallet(
            WalletBalances(plan_remaining=10, carryover_credits=20, purchased_credits=1000),
            60,
            carryover_expires_at=LATER,
            now=NOW,
        )
        assert result == WalletBalances(plan_remaining=0, carryover_credits=0, purchased_credits=970)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdges:
    def test_overspending_clamps_to_zero_and_never_goes_negative(self) -> None:
        """Matches the worker's existing clamp. Raising here would turn "out of
        credits" into a 500 instead of a refusal the user can act on."""
        result = consume_from_wallet(
            WalletBalances(plan_remaining=5, carryover_credits=5, purchased_credits=5),
            100,
            carryover_expires_at=LATER,
            now=NOW,
        )
        assert result == WalletBalances(plan_remaining=0, carryover_credits=0, purchased_credits=0)

    def test_spending_exactly_everything_leaves_zeroes(self) -> None:
        result = consume_from_wallet(
            WalletBalances(plan_remaining=10, carryover_credits=20, purchased_credits=30),
            60,
            carryover_expires_at=LATER,
            now=NOW,
        )
        assert available_total(result, carryover_expires_at=LATER, now=NOW) == 0

    def test_zero_units_is_a_no_op(self) -> None:
        balances = WalletBalances(plan_remaining=10, carryover_credits=20, purchased_credits=30)
        assert (
            consume_from_wallet(balances, 0, carryover_expires_at=LATER, now=NOW) == balances
        )

    @pytest.mark.parametrize("units", [-1, -100])
    def test_negative_units_is_rejected(self, units: int) -> None:
        # A negative spend would *add* credits, so it must be impossible.
        with pytest.raises(ValueError, match="negative"):
            consume_from_wallet(
                WalletBalances(plan_remaining=10, carryover_credits=0, purchased_credits=0),
                units,
                carryover_expires_at=None,
                now=NOW,
            )

    def test_a_negative_balance_cannot_be_constructed(self) -> None:
        with pytest.raises(ValueError, match="negative"):
            WalletBalances(plan_remaining=-1, carryover_credits=0, purchased_credits=0)

    def test_consuming_returns_a_new_value(self) -> None:
        """Frozen value semantics: a caller holding a balance for a response
        must never see it change underneath them."""
        original = WalletBalances(plan_remaining=10, carryover_credits=20, purchased_credits=30)
        result = consume_from_wallet(original, 5, carryover_expires_at=LATER, now=NOW)

        assert result is not original
        assert original.carryover_credits == 20
        assert result.carryover_credits == 15
