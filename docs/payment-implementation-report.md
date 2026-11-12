# Payment Implementation Report — Transform-To

**Date:** 2026-11-11
**Branch:** `feature/cloud-workspace`
**Related:** `payment-system-audit.md`, `payment-system-architecture.md`, `payment-remediation-plan.md`, `payment-regression-tests.md`.

---

## 1. Summary

The reported defect — *a customer can stop paying and keep paid benefits indefinitely* — is fixed, and the **3-business-day grace period** is implemented as a server-side, restart-safe, non-extendable rule.

Two independent causes were removed:

1. **No state existed to expire.** A renewal failure now opens a persisted, absolute deadline. Entitlement is derived from that deadline **on every read**, so neither a stopped worker nor a restart can extend access.
2. **The failing-payment events were mishandled.** `invoice.payment_failed` was a log line; `customer.subscription.updated` re-applied the paid tier for `past_due`/`unpaid`. Both are now explicit, status-driven transitions.

Revocation removes entitlement only. **The Stripe subscription is never cancelled by the grace machinery**, and `stripe_subscription_id` is retained so a successful payment restores the plan.

---

## 2. Files changed

### New

| File | Purpose |
|---|---|
| `src/domain/subscriptions/value_object/payment_state.py` | `PaymentState` (`OK`/`GRACE`/`REVOKED`) and Stripe-status classification (`is_entitling`, `opens_grace`, `is_terminal`) |
| `src/domain/subscriptions/policies/payment_grace.py` | The single definition of the 3-business-day rule: `add_business_days`, `grace_deadline`, `entitlement_is_live`, `effective_tier`, `parse_holidays` |
| `src/infrastructure/database/models/webhook_event.py` | `StripeWebhookEventModel` — durable idempotency/replay/audit log keyed on `event.id` |
| `src/infrastructure/database/migrations/versions/0029_payment_grace.py` | Additive, idempotent migration for the lifecycle columns, the event table and two indexes |
| `workers/billing_worker/{__init__,main,dependencies,worker}.py` | The grace sweeper: converges stored state, records the revocation, clamps the paid credit bucket |
| `tests/unit/domain/test_payment_grace.py` | 22 tests pinning the business-day arithmetic and entitlement derivation |
| tests/unit/presentation/test_subscription_grace.py` | 21 tests pinning the lifecycle over a real SQLite schema |
| `docs/payment-*.md` | The five deliverables |

### Modified

| File | Change |
|---|---|
| `src/presentation/api/routers/v1/webhooks.py` | Event ledger + dedupe; status-driven dispatch; `_handle_invoice_payment_failed`; `_handle_subscription_deleted`; ordering and subscription-id guards; `_refresh_allowance_for_invoice` now requires a paid status; `_activate_subscription` clears grace and mirrors the Stripe state |
| `src/infrastructure/adapters/repository/sql_subscription_repo.py` | `get_tier_for_user`/`get_actor_tier` now return the **enforced** tier; guarded `open_grace`/`clear_grace`/`touch_payment_failure`/`mirror_stripe_state`; `get_due_grace_rows`; `revoke_entitlement`; lookup by Stripe subscription/customer id |
| `src/infrastructure/database/models/subscription.py` | 11 lifecycle columns (`stripe_status`, `payment_state`, grace timestamps, `previous_paid_tier`, `entitlement_revoked_at`, `current_period_end`, `cancel_at_period_end`, …) |
| `src/infrastructure/database/models/__init__.py` | Export the new model |
| `src/infrastructure/config/settings.py` | `PAYMENT_GRACE_*` settings, boot validation (business days, timezone, holidays, sweep interval, Stripe key/secret pairing) |
| `src/presentation/api/routers/v1/subscriptions.py` | `/status` derives the real status (incl. `PAST_DUE`, `GRACE_EXPIRED`, `grace_deadline_at`); `/checkout` refuses a second subscription; `/change-plan` uses a tier guard |
| `src/presentation/schemas/subscription.py` | `PAST_DUE`, `GRACE_EXPIRED`, `grace_deadline_at` |
| `src/infrastructure/adapters/payment/stripe_service.py` | `_subscription_period` reads periods from the subscription **item**; upgrade uses `payment_behavior="error_if_incomplete"` |
| `deployment/docker/compose.yaml`, `compose.cloud.yaml` | New `worker-billing` service on the production env override |
| `tests/unit/presentation/test_billing_history_and_resume.py` | Fake row updated with the new lifecycle defaults |

---

## 3. Database changes

Revision `0029_payment_grace`, parent `0028_cloud_workspaces`.

| Object | Change |
|---|---|
| `user_subscriptions.stripe_status` | `String(32)`, nullable |
| `user_subscriptions.payment_state` | `String(16)`, NOT NULL, default `'OK'` |
| `user_subscriptions.grace_started_at`, `grace_deadline_at`, `last_payment_failed_at`, `last_payment_event_at`, `entitlement_revoked_at`, `current_period_end` | `timestamptz`, nullable |
| `user_subscriptions.grace_source_invoice_id` | `String(255)`, nullable |
| `user_subscriptions.previous_paid_tier` | `String(20)`, nullable |
| `user_subscriptions.cancel_at_period_end` | `boolean`, NOT NULL, default false |
| `stripe_webhook_events` | New table: `event_id` PK, `event_type`, `event_created`, `received_at`, `outcome`, `error` |
| `ix_user_subscriptions_grace_deadline` | Partial index `WHERE payment_state = 'GRACE'` |
| `ix_user_subscriptions_stripe_subscription_id` | Plain index |
| `ix_stripe_webhook_events_event_type` | Plain index |

**No backfill.** `payment_state='OK'` is correct for every pre-existing row: the database holds no evidence of a past failure, and inventing grace state could revoke a paying customer.

The upgrade inspects columns/tables/indexes before acting and tolerates an already-applied run, because `RUN_MIGRATIONS=true` turns a migration error into a startup outage.

---

## 4. Behaviour changes (what a customer/operator will observe)

| Scenario | Before | After |
|---|---|---|
| Renewal payment fails | Nothing happens; paid access continues indefinitely | Entitlement continues until the end of the 3rd business day; `/status` reports `PAST_DUE` with the deadline |
| Grace window closes | Nothing | Entitlement drops to FREE (on read, immediately; persisted by the sweeper within 5 minutes); plan credits clamp to the FREE grant; purchased credits are untouched |
| Payment recovers during grace | Tier happened to stay paid | Grace cleared; plan intact; `/status` returns `ACTIVE` |
| Payment recovers after revocation | Tier happened to stay paid | Plan restored from the price/metadata; `previous_paid_tier` records what was removed |
| Abandoned checkout (`incomplete_expired`) | **Granted the paid tier** | No entitlement |
| Late `invoice.payment_succeeded` after cancellation | **Restored PRO permanently** | Downgraded, because the subscription status is terminal |
| Duplicate webhook | Re-processed | Deduped by `event.id` |
| Stale webhook (older than stored state) | Overwrote newer state | Ignored |
| Cancelling an old subscription | Could downgrade a paying customer | Ignored unless it is the stored subscription |
| `/status` for `past_due` / `unpaid` | `ACTIVE` | `PAST_DUE` / `GRACE_EXPIRED` |
| Second `/checkout` while subscribed | Created a second billable subscription | 409 |
| Upgrade with a failing proration charge | Granted the higher tier + full credit grant | Request fails; no local change |

---

## 5. Bugs fixed

1. **Indefinite paid access after non-payment** (P0) — root cause of the reported issue.
2. **`past_due`/`unpaid`/`incomplete_expired`/`paused` re-granted the plan** (P0).
3. **No time bound on entitlement** (P0).
4. **Paid plan credits survived revocation** (P0/P1).
5. **Late `invoice.payment_succeeded` resurrected a cancelled subscription** (P1).
6. **`current_period_end` always `None`** → carry-over never expired, `/status` periods blank (P1).
7. **Double subscription via `/checkout`** (P1).
8. **Upgrade granted before the proration was paid** (P1).
9. **Events keyed only on `user_id`** → stale events could downgrade a payer (P1).
10. **`/change-plan` 500 for a downgraded ex-customer** (P1).
11. **No webhook idempotency or ordering** (P1).
12. **`/status` reported `ACTIVE` for delinquent and unknown states** (P2).
13. **`checkout.session.async_payment_failed` ran the success handler** (P2).
14. **`"pro"` default on a missing `metadata.tier`** (P3).

---

## 6. Security improvements

| Change | Effect |
|---|---|
| Boot validation: `STRIPE_SECRET_KEY` without `STRIPE_WEBHOOK_SECRET` now fails startup | Prevents a configuration that charges customers while rejecting every fulfilment event |
| Subscription resolution prefers the Stripe subscription id | A forged or replayed event for another subscription id cannot overwrite the caller's state |
| Ordering guard on `event.created` | An attacker replaying a captured **valid** event within Stripe's tolerance cannot re-open a closed grace window (the transition is also state-guarded) |
| Grace window is write-once per window | Repeated failures, replays and restarts cannot extend it |
| Webhook event ledger | Every delivery is attributable; unhandled types are recorded rather than silently dropped |

No Stripe secret, signing secret, or payload is logged. The signature-verification failure path logs only the exception message.

---

## 7. Tests executed

See `docs/payment-regression-tests.md` for the full matrix. Results:

| Suite | Result |
|---|---|
| `tests/unit/domain/test_payment_grace.py` | **22 passed** |
| `tests/unit/presentation/test_subscription_grace.py` | **21 passed** |
| `tests/unit` (full) | **2133 passed, 0 failed** (2:55) |
| `tests/integration` (full) | **434 passed, 0 failed** (18:04) |
| **Total** | **2567 passed, 0 failed** |
| `pyrefly check` | **0 errors** |
| Migration `0029` | upgrade ×2 (idempotent), pre-existing row keeps `payment_state='OK'`, downgrade removes every added column — verified |

### 7.1 Proof the fix is behavioural, not a refactor

The new lifecycle suite was run with the four changed source files reverted
(`git stash push -- webhooks.py sql_subscription_repo.py subscriptions.py
stripe_service.py`) and the new tests kept:

```
20 failed, 23 passed
```

Every lifecycle test failed on the pre-fix code. The one lifecycle test that
*passes* there is `test_grace_before_the_deadline_still_yields_the_paid_tier_on_read` — the
old code returned the paid tier unconditionally, which is precisely the bug; that
test documents the "retained during grace" half of the rule.

The 22 domain tests pass in both states because `payment_grace.py` is a new
module: they pin the policy in isolation, and they are the specification the
handlers are built against.

After restoring, all 43 pass and the full suite is green.

### 7.2 Verification notes

- The lifecycle suite uses in-memory SQLite with the real ORM metadata, so the
  guarded `UPDATE` statements — which *are* the idempotency mechanism — are
  genuinely exercised.
- No test contacts Stripe, Redis, S3, or any network service.
- `pytest-timeout` is **not** a declared dev dependency, so the workspace task
  that passes `--timeout=90` cannot run. That is a pre-existing repository
  defect, unrelated to this change.
- The integration suite takes ~18 minutes in this environment; the unmodified
  workspace tests are the slow section, not the payment tests.

---

## 8. Outstanding risks

| # | Risk | Impact | Recommended next step |
|---|---|---|---|
| 1 | **No reconciliation loop** | A missed `checkout.session.completed` can leave a paying customer FREE; a missed failure relies on the grace path working from another event | Daily job reusing the webhook's status mapping |
| 2 | **Credit consumption is advisory** | Concurrent jobs can overspend a bucket | Reserve at job creation with a conditional atomic `UPDATE` |
| 3 | **One subscription per user (schema)** | A subscription created out-of-band cannot be represented; the checkout guard only prevents creating a second via the API | Model subscriptions as a collection, or hard-fail on a mismatch |
| 4 | **Credit-only buyers have no persisted Stripe customer** | Portal, invoices and saved cards unavailable to them | Persist `event_data["customer"]` on the credit path |
| 5 | **Invoices are not persisted** | Billing history depends on a live Stripe call | Add an `invoices` table |
| 6 | **Duplicate-vs-FK `IntegrityError`** | A credit grant can be silently skipped and answered 200 | Inspect the constraint name; re-raise anything that is not the unique reference |
| 7 | **Guest rate-limit bypass via headers** | Unbounded unauthenticated compute | Choose the bucket from the verified principal |
| 8 | **Unbounded chunked webhook body** | Memory exhaustion | Read the stream incrementally with a byte cap |
| 9 | **No refund/dispute handling** | Refunded credits remain spendable | Subscribe `charge.refunded` / `charge.dispute.created` |

---

## 9. Operational notes for deployment

1. Apply migration `0029` (it runs automatically when `RUN_MIGRATIONS=true`).
2. Deploy the API. `/ready` should stay green; existing checkouts are unaffected.
3. Build and start `worker-billing`. It logs:
   `Billing worker ready: grace=3 business days (tz=UTC), sweep every 300s`.
   It must use the **production** env override — it changes entitlement.
4. Confirm the Stripe webhook endpoint subscribes to at least:
   `invoice.payment_failed`, `invoice.payment_action_required`,
   `invoice.paid` (or `invoice.payment_succeeded`),
   `customer.subscription.updated`, `customer.subscription.deleted`,
   `checkout.session.completed`, `checkout.session.async_payment_succeeded`,
   `checkout.session.async_payment_failed`.
   Without `invoice.payment_failed` the grace window is opened only by
   `customer.subscription.updated(past_due)`, which is still correct but depends
   on that event alone.
5. **Do not** test recovery by cancelling or refunding a real subscription.
   Use Stripe test mode.
6. Existing customers currently in `past_due`/`unpaid` will have no local grace
   window until their next status event or the (not yet implemented)
   reconciliation pass. They are not revoked by this change — safe, but it means
   the first pass will not retroactively claw back access already granted.
