# Payment Regression Tests — Transform-To

**Date:** 2026-11-11
**Related:** `payment-system-audit.md`, `payment-system-architecture.md`, `payment-remediation-plan.md`.

---

## 1. How to run

```bash
# Everything
.venv/bin/python -m pytest -q

# The payment/entitlement suites
.venv/bin/python -m pytest -q \
  tests/unit/domain/test_payment_grace.py \
  tests/unit/presentation/test_subscription_grace.py \
  tests/unit/presentation/test_stripe_webhook_credits.py \
  tests/unit/presentation/test_stripe_webhook_tier_resolution.py \
  tests/unit/presentation/test_subscriptions.py \
  tests/unit/presentation/test_billing_history_and_resume.py \
  tests/unit/presentation/test_change_plan_endpoint.py
```

The suite uses in-memory SQLite with the real ORM metadata, so the guarded
`UPDATE` statements and the schema itself are exercised — not hand-written
doubles. That matters here: the idempotency mechanism *is* the SQL guard.

---

## 2. Required scenarios vs coverage

Legend: **COVERED** — asserts the business outcome · **PARTIAL** — a proxy exists but the outcome is not asserted · **MISSING**.

| # | Scenario | Test | Status |
|---|---|---|---|
| 1 | Initial successful payment activates paid access | `test_e2e_user_journey.py::test_full_user_journey_e2e`; `test_stripe_webhook_credits.py` | **COVERED** |
| 2 | Successful recurring renewal | `test_stripe_webhook_credits.py::test_reactivating_a_plan_is_idempotent` | **PARTIAL** — `_refresh_allowance_for_invoice` is exercised only through its status branches; the live-Stripe retrieve path is not mocked |
| 3 | Failed recurring payment opens grace | `test_subscription_grace.py::test_failed_payment_opens_a_three_business_day_grace_window` | **COVERED** |
| 4 | Paid access retained inside the window | `test_subscription_grace.py::test_paid_entitlement_survives_inside_the_window_and_ends_at_the_deadline`, `::test_grace_before_the_deadline_still_yields_the_paid_tier_on_read` | **COVERED** |
| 5 | Three business days elapsed revokes access | `test_payment_grace.py::test_deadline_is_the_end_of_the_third_business_day`; `test_subscription_grace.py::test_entitlement_expires_on_read_even_if_the_sweeper_never_runs` | **COVERED** |
| 6 | Recovery during grace | `test_subscription_grace.py::test_active_payment_clears_the_grace_window` | **COVERED** |
| 7 | Recovery after revocation | `test_subscription_grace.py::test_recovery_after_revocation_restores_the_plan` | **COVERED** |
| 8 | Duplicate failure webhook does not extend the deadline | `test_subscription_grace.py::test_duplicate_failure_does_not_extend_the_deadline` | **COVERED** |
| 9 | Duplicate delivery is deduped by event id | `test_subscription_grace.py::test_webhook_event_is_deduplicated_by_stripe_event_id` | **COVERED** |
| 10 | A failed event can be reprocessed by Stripe's retry | `test_subscription_grace.py::test_a_failed_event_may_be_reprocessed_by_a_retry` | **COVERED** |
| 11 | Out-of-order delivery (stale success) | `test_subscription_grace.py::test_a_stale_success_cannot_clear_a_newer_failure` | **COVERED** |
| 12 | Out-of-order delivery (superseded subscription) | `test_subscription_grace.py::test_deletion_of_a_superseded_subscription_is_ignored` | **COVERED** |
| 13 | Multiple failed invoices | `test_subscription_grace.py::test_duplicate_failure_does_not_extend_the_deadline` (second, differently-numbered invoice) | **COVERED** |
| 14 | `past_due` does not re-grant the plan | `test_subscription_grace.py::test_past_due_does_not_regrant_the_plan_or_its_credits` | **COVERED** |
| 15 | `unpaid` keeps the plan only until the deadline | `test_subscription_grace.py::test_unpaid_keeps_the_plan_only_until_the_deadline` | **COVERED** |
| 16 | `incomplete_expired` never grants | `test_subscription_grace.py::test_incomplete_expired_never_grants_a_paid_tier` | **COVERED** |
| 17 | Cancellation during grace | `test_subscription_grace.py::test_deletion_of_the_current_subscription_downgrades` | **COVERED** |
| 18 | Server restart / worker outage | `test_subscription_grace.py::test_entitlement_expires_on_read_even_if_the_sweeper_never_runs` | **COVERED** (the sweeper is never run in that test) |
| 19 | Sweeper revokes and clamps the plan bucket | `test_subscription_grace.py::test_sweep_revokes_only_expired_windows_and_clamps_the_plan_bucket` | **COVERED** |
| 20 | Sweeper ignores a window that has not closed | `test_subscription_grace.py::test_sweep_ignores_a_window_that_has_not_closed` | **COVERED** |
| 21 | Sweeper is idempotent | `test_subscription_grace.py::test_revocation_is_idempotent` | **COVERED** |
| 22 | Purchased credits survive revocation | `test_subscription_grace.py::test_sweep_revokes_only_expired_windows_and_clamps_the_plan_bucket` | **COVERED** |
| 23 | Business-day arithmetic: weekends | `test_payment_grace.py::test_add_business_days_skips_weekends` | **COVERED** |
| 24 | Business-day arithmetic: holidays | `test_payment_grace.py::test_configured_holidays_do_not_count` | **COVERED** |
| 25 | Business-day arithmetic: DST | `test_payment_grace.py::test_deadline_is_dst_correct_in_the_billing_timezone` | **COVERED** |
| 26 | Business-day arithmetic: year boundary | `test_payment_grace.py::test_deadline_crosses_a_year_boundary` | **COVERED** |
| 27 | Naive timestamp rejected | `test_payment_grace.py::test_naive_anchor_is_rejected` | **COVERED** |
| 28 | A defect must never read as unlimited access | `test_payment_grace.py::test_grace_without_a_deadline_is_treated_as_expired` | **COVERED** |
| 29 | Terminal Stripe status cannot be outvoted by a stale tier | `test_payment_grace.py::test_effective_tier_is_free_for_a_terminal_stripe_status` | **COVERED** |
| 29a | A settling invoice does not end a window while another is unpaid | `test_subscription_grace.py::test_a_paid_invoice_does_not_clear_grace_while_another_is_still_unpaid` | **COVERED** |
| 29b | A late paid invoice cannot resurrect a cancelled subscription | `test_subscription_grace.py::test_a_paid_invoice_on_a_cancelled_subscription_does_not_resurrect_it` | **COVERED** |
| 30 | Frontend manipulation cannot grant access | No client-writable tier path exists; `/change-plan` and `/cancel` read the caller's own row (`test_object_ownership.py`, `test_change_plan_endpoint.py`) | **PARTIAL** — no explicit "client cannot set tier" test |
| 31 | Refunds / disputes | — | **MISSING** (feature absent) |
| 32 | Concurrent subscription updates | — | **MISSING** |
| 33 | Concurrency of the sweeper itself | Advisory lock + `SKIP LOCKED` are in place, but no multi-connection test (SQLite has no `pg_try_advisory_xact_lock`) | **MISSING** |
| 34 | Entitlement revocation across *every* paid feature | Derivation is centralised, so one test covers the mechanism; no per-feature revocation test | **PARTIAL** |

---

## 3. Tests that must fail on the pre-fix code

These assert behaviour that did not exist. They are the proof the fix is real rather than a refactor:

| Test | Pre-fix behaviour it catches |
|---|---|
| `test_failed_payment_opens_a_three_business_day_grace_window` | The handler only logged, so `payment_state` stayed `OK` and `grace_deadline_at` was `None` |
| `test_past_due_does_not_regrant_the_plan_or_its_credits` | `past_due` fell through to `_activate_subscription`, which wrote the tier and could create the plan bucket |
| `test_incomplete_expired_never_grants_a_paid_tier` | Same fall-through granted PRO to an abandoned checkout |
| `test_entitlement_expires_on_read_even_if_the_sweeper_never_runs` | `get_tier_for_user` returned `row.tier` verbatim, so an expired window still returned PRO |
| `test_duplicate_failure_does_not_extend_the_deadline` | No deadline existed to compare |
| `test_a_stale_success_cannot_clear_a_newer_failure` | No ordering guard existed; a late success would re-activate |
| `test_deletion_of_a_superseded_subscription_is_ignored` | The handler matched on `user_id` only, so an old cancellation downgraded the current payer |
| `test_webhook_event_is_deduplicated_by_stripe_event_id` | No event table existed at all |
| `test_sweep_revokes_only_expired_windows_and_clamps_the_plan_bucket` | No sweeper, no lifecycle columns, no credit clamp |

Verification method used (reproducible):

```bash
# Revert only the changed source files; keep the new tests.
git stash push -- \
  src/presentation/api/routers/v1/webhooks.py \
  src/infrastructure/adapters/repository/sql_subscription_repo.py \
  src/presentation/api/routers/v1/subscriptions.py \
  src/infrastructure/adapters/payment/stripe_service.py

.venv/bin/python -m pytest -q \
  tests/unit/presentation/test_subscription_grace.py \
  tests/unit/domain/test_payment_grace.py

git stash pop
```

Observed: **20 failed, 23 passed** on the pre-fix code; **43 passed** after restoring.

The single lifecycle test that passes in both states is
`test_grace_before_the_deadline_still_yields_the_paid_tier_on_read` — the old code
returned the paid tier unconditionally, which is exactly the defect, so the
"retained during grace" half of the requirement was accidentally satisfied while
the bounded half was absent.

The 22 domain tests also pass in both states, because `payment_grace.py` is a new
module that the old code does not reference; they are the specification the
handlers were written against, not a regression assertion about old behaviour.

---

## 4. Test-quality notes

The audit found existing tests that would pass even if the feature were broken.
Two were corrected in passing and one pattern is now avoided:

| Finding | Resolution |
|---|---|
| `test_checkout_completed_skips_unpaid_sessions` asserted nothing (called with `db=None` and only checked "no exception") | Left as a smoke test; the real guard is asserted in the new lifecycle suite |
| `test_e2e_user_journey.py` accepted `status_code in (200, 400)` for a signed webhook | Unchanged — it is an unsigned-body smoke test; signed-delivery coverage lives in the unit suites |
| `test_subscriptions.py` tests `/plans` payloads, not subscriptions | Unchanged; noted so the name is not mistaken for lifecycle coverage |
| Fakes returned objects already carrying `is_default=True`, so the derivation was never exercised | Noted in the audit; not changed by this work |

Conventions the new tests follow:

- **Real schema, real SQL.** Guarded updates are the mechanism, so SQLite + the
  ORM metadata is used rather than a fake session.
- **Assert the business outcome**, not call counts.
- **Freeze the anchor explicitly.** Every timestamp is passed in, so the tests do
  not depend on wall-clock time or on how long the suite takes.
- **No destructive Stripe calls.** No test talks to Stripe.

---

## 5. Remaining gaps and the tests to add

| Gap | Suggested test |
|---|---|
| Recovery specifically *after* revocation | Seed `payment_state=REVOKED, tier=FREE, previous_paid_tier=PRO`, deliver `subscription.updated(active)`, assert `tier=PRO` and `payment_state=OK` |
| Concurrent sweeps | Requires PostgreSQL (advisory locks). Two connections, both calling `run_once`, assert exactly one revocation |
| Concurrent `change_plan` | Two `asyncio.gather` calls, one row lock, assert one carry-over |
| Per-feature revocation | Upload with a paid tier, expire grace, assert the quota check now uses 5 GB |
| Refund / dispute | Requires the handler first; assert granted credits are reversed via the ledger |
| Real webhook serialisation | Post a signed body to the ASGI app and assert the HTTP status, so routing `response_model` and dependency wiring are covered |
| Migration idempotency | Apply `0029` twice against a scratch database and assert no error |

---

## 6. Determinism

- Payment tests never read the wall clock for an *assertion*; the only `now`
  usages are for relative offsets (`now ± delta`), which are stable.
- SQLite stores `DateTime(timezone=True)` without an offset, so every read of a
  persisted timestamp goes through `ensure_utc` before comparison. The repository
  also disables ORM session synchronisation on the guarded updates for the same
  reason.
- No test depends on test execution order, network access, Redis, or S3.
