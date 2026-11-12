# Payment Remediation Plan — Transform-To

**Date:** 2026-11-11
**Companion documents:** `payment-system-audit.md` (findings), `payment-system-architecture.md` (target design), `payment-regression-tests.md` (verification), `payment-implementation-report.md` (what shipped).

---

## 1. Prioritisation

| Priority | Meaning | Policy |
|---|---|---|
| **P0** | Direct payment bypass, entitlement loss, or financial-integrity failure | Ship before anything else; blocks release |
| **P1** | Lifecycle or enforcement defect that produces wrong entitlement in a normal path | Same release |
| **P2** | Webhook reliability, reconciliation, observability | Next release |
| **P3** | Maintainability, hygiene, dead config | Backlog |

P0 and P1 items are implemented in this change. P2/P3 items are documented with a concrete approach so they are actionable, not aspirational.

---

## 2. Shared components (build order)

These had to exist before any dependent fix, because every dependent change reads or writes through them.

| # | Component | Why it is a prerequisite | Owners |
|---|---|---|---|
| C1 | `domain/subscriptions/value_object/payment_state.py` | Defines `OK`/`GRACE`/`REVOKED` and the Stripe status classification used by every handler | Domain |
| C2 | `domain/subscriptions/policies/payment_grace.py` | The single definition of "3 business days" and of entitlement derivation | Domain |
| C3 | Schema: lifecycle columns + `stripe_webhook_events` | Nothing can represent a deadline or an idempotency key without it | Persistence |
| C4 | Migration `0029_payment_grace` | Applies C3 additively and idempotently | Persistence |
| C5 | `SQLSubscriptionRepository`: `get_tier_for_user` derivation + guarded transitions | The choke point all nine call sites and the worker already use | Persistence |

Only after C1–C5 are the webhook, API, and worker changes expressible.

---

## 3. Implementation items

### P0-1 — Failed renewal did not change any state

| | |
|---|---|
| **Description** | `invoice.payment_failed` logged the event id and returned 200. No status, no deadline, no revocation. |
| **Root cause** | The handler had no state to write, and no policy to apply. |
| **Components** | `webhooks.py`, C1–C5 |
| **Solution** | New `_handle_invoice_payment_failed`: resolve the owner (subscription id → customer id → Stripe metadata), compute the deadline from the invoice's creation instant, and perform a guarded `OK → GRACE` update. Additional failures record the attempt without moving the deadline. `invoice.payment_action_required` is routed to the same handler, because an unpaid invoice that needs SCA is a payment failure with no clock otherwise. |
| **DB migration** | Yes (C3/C4) |
| **Tests** | Grace opens with a 3-business-day deadline; duplicate/replayed failures do not extend it; a failure on a FREE plan opens nothing. |
| **Dependencies** | C1–C5 |

### P0-2 — `subscription.updated` re-granted the paid tier

| | |
|---|---|
| **Description** | Only `status == "canceled"` downgraded. `past_due`, `unpaid`, `incomplete`, `incomplete_expired`, `paused` all fell through to `_activate_subscription`. |
| **Root cause** | The handler had no status model; "not canceled" was treated as "paid". |
| **Components** | `webhooks.py` |
| **Solution** | Explicit status dispatch: `active`/`trialing` → activate + clear grace; `past_due`/`unpaid`/`paused` → keep the plan, open/continue grace; `canceled`/`incomplete_expired`/`incomplete` → downgrade; unknown → mirror only. |
| **DB migration** | No (uses C3) |
| **Tests** | `past_due` opens grace and creates no plan bucket; `incomplete_expired` never grants; `active` clears grace. |
| **Dependencies** | P0-1, C5 |

### P0-3 — No time bound on entitlement

| | |
|---|---|
| **Description** | Nine call sites and the worker read `row.tier` verbatim. |
| **Root cause** | Entitlement was a stored plan, not a derived decision. |
| **Components** | `sql_subscription_repo.py`, C2 |
| **Solution** | `get_tier_for_user` / `get_actor_tier` now return `effective_tier(...)`. No call site changes were needed — the single choke point repaired all of them. |
| **DB migration** | Yes (C3/C4) |
| **Tests** | Expired grace yields FREE on read without the sweeper; live grace yields the paid tier. |
| **Dependencies** | C2, C3 |

### P0-4 — Paid credits survived revocation

| | |
|---|---|
| **Description** | Even a correct downgrade left the current month's paid `monthly_credits` bucket spendable. |
| **Components** | `sql_subscription_repo.py::revoke_entitlement` |
| **Solution** | Clamp `allowance`/`remaining` to the FREE grant in the same transaction as the tier change. Purchased and carry-over credits are left alone (one was paid for, the other has its own expiry). |
| **DB migration** | No |
| **Tests** | Revocation clamps 500/320 → 50/50 while `purchased_credits` stays 1000. |
| **Dependencies** | P0-3 |

### P1-1 — Late `invoice.payment_succeeded` resurrected a cancelled subscription

| | |
|---|---|
| **Description** | The handler retrieved the subscription and activated its metadata tier **without checking its status**. A event delivered after `subscription.deleted` restored PRO permanently. |
| **Components** | `webhooks.py::_refresh_allowance_for_invoice` |
| **Solution** | Activate only when the retrieved status is `active`/`trialing`; terminal → downgrade; payment-failure statuses → open grace; unknown → mirror only. |
| **Tests** | Positive: active activates. Negative: terminal downgrades (covered through the `subscription.updated` path, same predicate). |
| **Dependencies** | P0-2 |

### P1-2 — `current_period_end` was always `None`

| | |
|---|---|
| **Description** | Stripe moved `current_period_start`/`current_period_end` from `Subscription` to `SubscriptionItem`. Verified against the installed SDK: `stripe/_subscription_item.py:67` declares it; `stripe/_subscription.py` does not. |
| **Impact** | Carry-over expiry was always `None` (so "expiring" credits never expired), and `/status` period dates were blank. |
| **Components** | `stripe_service.py` |
| **Solution** | New `_subscription_period(subscription, key)` reads from `items.data[0]` with a subscription-level fallback. Used by `get_subscription` and `change_subscription_plan`. Webhook parsing has an equivalent `_first_period_end`. |
| **Tests** | Existing period-display tests updated; the item shape is what real payloads carry. |
| **Dependencies** | None |

### P1-3 — `/checkout` allowed a second subscription

| | |
|---|---|
| **Description** | Any authenticated user could POST `/subscription/checkout` while already subscribed; checkout always creates a *new* subscription → double billing. Because the local schema models one subscription, cancelling either one dropped the account to FREE while the other kept charging. |
| **Components** | `subscriptions.py::create_checkout_session` |
| **Solution** | 409 when the caller already has a `stripe_subscription_id` **and** an enforced paid tier. The enforced tier is used deliberately: an account whose grace window closed must be allowed to start paying again. |
| **Tests** | Guard logic is exercised through the existing checkout tests (single plan path) and the grace tests (a `/change-plan` equivalent guard on FREE). |
| **Dependencies** | P0-3 |

### P1-4 — Upgrade granted the plan before the proration was paid

| | |
|---|---|
| **Description** | `subscriptions.update` used `proration_behavior="always_invoice"` with Stripe's default `payment_behavior=allow_incomplete`, so the upgrade applied even when the charge failed, and the router granted the new tier + full credit grant. |
| **Components** | `stripe_service.py::change_subscription_plan` |
| **Solution** | `payment_behavior="error_if_incomplete"` — a failed charge now raises, so no local write happens and the customer sees a 502 rather than a free upgrade. |
| **Tests** | Existing change-plan tests use a fake service; the real behaviour is Stripe-side. |
| **Dependencies** | None |

### P1-5 — Events keyed only on `user_id`

| | |
|---|---|
| **Description** | A delayed `customer.subscription.deleted` for an **old** subscription downgraded a **new, paying** one; a stale success for a cancelled subscription re-activated it. |
| **Components** | `webhooks.py` (`_resolve_subscription_row`, both subscription handlers) |
| **Solution** | Resolve by `stripe_subscription_id` first; ignore an event whose subscription id does not match the stored one, logging at INFO. New index on `stripe_subscription_id` so this needs no Stripe round-trip. |
| **Tests** | Deleting a superseded subscription leaves the payer on PRO; deleting the current one downgrades. |
| **Dependencies** | C3 (index) |

### P1-6 — `/change-plan` 500 for a downgraded ex-customer

| | |
|---|---|
| **Description** | `_downgrade_to_free` retained `stripe_subscription_id` (correctly), but `/change-plan` used its presence as the "has a subscription" test → Stripe update against a cancelled subscription → 500. |
| **Components** | `subscriptions.py::change_plan` |
| **Solution** | Guard on `row.tier != FREE` as well. |
| **Tests** | Covered by the existing change-plan suite plus the FREE-tier conflict expectation. |
| **Dependencies** | None |

### P1-7 — No webhook idempotency or ordering

| | |
|---|---|
| **Description** | Only the credit ledger had a unique reference. Subscription state had no dedupe, no event log, and no ordering guard. |
| **Components** | `webhooks.py`, `models/webhook_event.py`, migration |
| **Solution** | `stripe_webhook_events` (PK = `event.id`); claim before handling; record outcome; allow reprocessing of a failed claim. `last_payment_event_at` provides the ordering guard inside the guarded updates. |
| **Tests** | Claim is deduped; a failed event may be retried; stale success cannot clear a newer failure. |
| **Dependencies** | C3/C4 |

### P2-1 / P2-2 — Misleading status reporting

| | |
|---|---|
| **Description** | `/status` returned `ACTIVE` for every non-`canceled` Stripe state and on a failed Stripe fetch; `EXPIRED`/`TRIAL` were dead enum values and no `PAST_DUE` existed. |
| **Solution** | Status is derived from persisted lifecycle state; added `PAST_DUE` and `GRACE_EXPIRED`, plus `grace_deadline_at` in the response. Period data is read from the persisted columns and only refreshed from Stripe when absent. |
| **Tests** | Existing status tests updated; grace states asserted in the new suite. |

---

## 4. Database changes

| Change | Type | Safety |
|---|---|---|
| 11 columns on `user_subscriptions` | Additive; 1 non-nullable with server default, 10 nullable | PostgreSQL 11+ adds a non-null column with a constant default as metadata only (no rewrite). No backfill; `payment_state='OK'` is correct for every existing row. |
| `stripe_webhook_events` table | New | Empty on creation; bounded retention via the cleanup worker. |
| Partial index on `(grace_deadline_at) WHERE payment_state='GRACE'` | Additive | Keeps the sweep proportional to delinquent subscriptions. |
| Index on `stripe_subscription_id` | Additive | Avoids a Stripe round-trip per invoice event. |

Revision `0029_payment_grace`, `down_revision = "0028_cloud_workspaces"`. The upgrade inspects columns/tables/indexes first and tolerates an already-applied run — mandatory here, because `RUN_MIGRATIONS=true` turns a migration error into a startup outage.

**No destructive operation, no data rewrite, no change to customer files, credit balances, or Stripe identifiers.**

---

## 5. Agent responsibilities (as executed)

| Agent | Investigation output | Implementation ownership |
|---|---|---|
| 1 — Stripe Integration | Touchpoint inventory; double-checkout, detach-guard, proration, idempotency-key findings | `stripe_service.py` (period extraction, `payment_behavior`) |
| 2 — Webhook Reliability | Event mapping, idempotency/ordering analysis, 200-without-write paths | `webhooks.py` dispatch, event ledger |
| 3 — Subscription Lifecycle | AS-IS state machine, root cause, missing-state enumeration | Domain value objects, `payment_grace.py` |
| 4 — Entitlement & Access | Enforcement inventory, caching analysis, choke-point identification | `sql_subscription_repo.py` derivation |
| 5 — Payment Failure & Recovery | Restart/outage analysis, business-day algorithm, sweeper placement | `workers/billing_worker`, settings |
| 6 — Billing & Financial Integrity | Credit accounting, `current_period_end` SDK verification, upgrade arithmetic | `revoke_entitlement` credit clamp |
| 7 — Security & Payment Abuse | Adversarial review; confirmed auth boundary, secret handling | Boot validation for Stripe key/secret pairing |
| 8 — Testing & Reliability | Coverage matrix, test-quality assessment, suite gaps | New regression suites |

---

## 6. Verification requirements

1. Domain grace arithmetic: weekends, holidays, DST, year boundary, naive-anchor rejection.
2. Webhook lifecycle: initiation, non-extension, revocation, recovery, out-of-order, superseded subscription.
3. Entitlement derivation: read-time expiry with the sweeper disabled.
4. Sweeper: only overdue rows, idempotency, credit clamp, purchased credits preserved.
5. Event ledger: dedupe, retry-after-failure.
6. Existing suite: no regression.
7. Migration: applies cleanly and is re-runnable (idempotency check).

See `docs/payment-regression-tests.md` for the implemented matrix.

---

## 7. Rollout

| Step | Action | Verification |
|---|---|---|
| 1 | Merge with migration `0029` | `alembic upgrade head` applies; re-run is a no-op |
| 2 | Deploy API | `/ready` healthy; existing checkouts unaffected |
| 3 | Deploy `worker-billing` | It logs `Billing worker ready: grace=3 business days …` |
| 4 | Confirm the Stripe endpoint subscribes to `invoice.payment_failed`, `invoice.payment_action_required`, `customer.subscription.updated`, `customer.subscription.deleted`, `invoice.paid` | Stripe Dashboard → Webhooks |
| 5 | Smoke test in Stripe test mode | A failed test invoice opens grace; the status endpoint reports `PAST_DUE` with a deadline |
| 6 | Do **not** run a destructive Stripe operation to test recovery | Use test clocks / test mode only |

**Rollback:** revert the code; the columns and table are additive and simply become unused. Do not run `alembic downgrade` in production as a remedy.

---

## 8. Deferred work (P2/P3) and recommended approach

| Item | Approach | Risk if deferred |
|---|---|---|
| Stripe reconciliation loop | Daily pass over rows with a `stripe_subscription_id`; call `get_subscription`; apply the same status mapping the webhook uses; anchor a discovered failure to `current_period_end` rather than "now" | A missed event can leave a customer wrongly FREE (revenue/support) |
| Credit reservation at job creation | One atomic `UPDATE monthly_credits SET remaining = remaining - cost WHERE remaining >= cost`, reconciled to actual cost on completion | Concurrent jobs overspend the bucket |
| Persist invoices | Add an `invoices` table written from `invoice.paid`/`invoice.finalized` | Billing history is unavailable during a Stripe outage |
| Distinguish duplicate from FK violation in the credit grant | Inspect `exc.orig` / constraint name; re-raise anything that is not the unique-reference violation | A paid customer silently receives nothing |
| Guest rate limit bypass | Decide the bucket from the verified principal, not the raw header; check guest paths before header inspection | Unbounded unauthenticated compute |
| Bound chunked webhook bodies | Read the stream incrementally with a byte counter | Memory exhaustion from an unauthenticated caller |
| Refund / dispute handling | Subscribe `charge.refunded`, `charge.dispute.created`; reverse the granted credits via the ledger | Refunded credits remain spendable |
| Dead rate-limit config | Read per-key and per-tier limits, or remove the advertised values | Cosmetic oversell of "API calls/month" |
