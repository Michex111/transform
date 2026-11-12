# Payment System Audit — Transform-To

**Date:** 2026-11-11
**Scope:** Stripe integration, subscription lifecycle, entitlements, webhook reliability, financial integrity, payment security, and test coverage.
**Method:** eight parallel read-only investigations, each tracing real execution paths in the repository; every finding is tagged **CONFIRMED** (proved by code) or **SUSPECTED** (depends on Stripe dashboard configuration or live behaviour).

---

## 1. Executive summary

The reported defect is **real, reproducible from code, and caused by two independent failures that each suffice on their own**:

1. **No state exists to expire.** `user_subscriptions` carried only `tier`; there was no payment status, no period, and no deadline. Nothing in the system could ever compute "this paid tier is no longer valid".
2. **The one signal that could downgrade was either a no-op or actively re-granted access.** `invoice.payment_failed` was a single `logger.info` line, and `customer.subscription.updated` treated *every* status except `canceled` — including `past_due` and `unpaid` — as a successful activation.

Because entitlement is derived from `tier` at nine call sites plus the converter worker, a customer who stopped paying kept paid storage, credits, AI model access, MCP scope, and queue priority indefinitely.

Six further defects of the same class were confirmed and are fixed or mitigated:

| # | Defect | Severity |
|---|---|---|
| 1 | Failed renewal never revoked entitlement | **P0** |
| 2 | `subscription.updated` re-granted the paid tier for `past_due`/`unpaid`/`incomplete_expired`/`paused` | **P0** |
| 3 | No grace deadline anywhere; nothing time-bounded access | **P0** |
| 4 | A late/out-of-order `invoice.payment_succeeded` resurrected a cancelled subscription permanently | **P1** |
| 5 | `subscription.current_period_end` does not exist on this API version → carry-over expiry was always `None` | **P1** |
| 6 | `/subscription/checkout` allowed a second concurrent subscription (double billing) | **P1** |
| 7 | `/subscription/status` reported `ACTIVE` for every non-`canceled` Stripe state, and on Stripe fetch failure | **P2** |
| 8 | No webhook event dedupe, ordering guard, or replay log | **P2** |

---

## 2. Architecture inventory (as audited)

### 2.1 Components

| Layer | Component | Responsibility |
|---|---|---|
| Domain | `domain/subscriptions/entities/subscription.py` | `{tier, used_storage_bytes}` + storage arithmetic. **No lifecycle.** |
| Domain | `domain/subscriptions/policies/tier_policy.py` | Quotas and monthly credit grants per tier |
| Domain | `domain/subscriptions/policies/credit_wallet.py` | Three-pool spend order (carryover → plan/purchased → other) |
| Domain | `domain/subscriptions/value_object/{tier,credit_period}.py` | Tier enum; UTC calendar-month bucketing |
| Application | `application/services/billing/credit_service.py` | Balance projection, plan allowance |
| Infrastructure | `adapters/payment/stripe_service.py` | All Stripe v1 calls (`StripeClient`) |
| Infrastructure | `adapters/repository/sql_subscription_repo.py` | `user_subscriptions` persistence |
| Infrastructure | `adapters/repository/sql_credit_repo.py` | `monthly_credits` + `credit_transactions` ledger |
| Presentation | `api/routers/v1/webhooks.py` | Single inline Stripe event handler |
| Presentation | `api/routers/v1/subscriptions.py` | Checkout, portal, status, cancel, resume, change-plan, invoices, payment methods |
| Presentation | `api/routers/v1/credits.py` | Balance, purchase, transactions |
| Workers | `workers/converter_workers/dependencies.py` | Worker-side credit spend |

### 2.2 Stripe touchpoints

| File | Function | Stripe call | Local write |
|---|---|---|---|
| `stripe_service.py` | `create_checkout_session` | `checkout.sessions.create` | none |
| `stripe_service.py` | `create_credit_purchase_session` | `checkout.sessions.create` | none |
| `stripe_service.py` | `change_subscription_plan` | `subscriptions.retrieve`, `subscriptions.update` / schedule rewrite | none (router writes) |
| `stripe_service.py` | `cancel_subscription` | `subscriptions.update(cancel_at_period_end)` | none |
| `stripe_service.py` | `resume_subscription` | `subscriptions.update(cancel_at_period_end=False)` | none |
| `stripe_service.py` | `get_subscription` | `subscriptions.retrieve` | none |
| `stripe_service.py` | `create_customer` | `customers.create` | none (**zero callers**) |
| `stripe_service.py` | `create_portal_session` | `billing_portal.sessions.create` | none |
| `stripe_service.py` | `list_invoices` | `invoices.list(limit=12)` | none (not persisted) |
| `webhooks.py` | `_activate_subscription` | — | `user_subscriptions` upsert + `monthly_credits` |
| `webhooks.py` | `_grant_purchased_credits` | — | `purchased_credits` + ledger |
| `webhooks.py` | `_refresh_allowance_for_invoice` | `subscriptions.retrieve` | `user_subscriptions` + `monthly_credits` |
| `webhooks.py` | `_downgrade_to_free` | — | `user_subscriptions.tier = FREE` |

### 2.3 Event handling (as audited)

| Event | Before | After |
|---|---|---|
| `checkout.session.completed` | activate subscription / grant credits | same, now grace-aware, no `"pro"` default |
| `checkout.session.async_payment_succeeded` | activate | same |
| `checkout.session.async_payment_failed` | **ran the activation handler** | skipped explicitly |
| `invoice.payment_succeeded` / `invoice.paid` | activate unconditionally from subscription metadata | only when Stripe status is `active`/`trialing` |
| `invoice.payment_failed` | **`logger.info` only** | opens a bounded grace window |
| `invoice.payment_action_required` | unhandled | opens a bounded grace window |
| `customer.subscription.updated` | `canceled` → FREE, **everything else → activate** | status-driven state machine |
| `customer.subscription.deleted` | FREE if `metadata.user_id` present | FREE, guarded on subscription id |
| everything else | 200, no effect | 200, recorded as `skipped:unhandled` |

---

## 3. Root-cause analysis of the indefinite-subscription bug

### 3.1 Execution path (pre-fix)

| Step | What happened | Evidence |
|---|---|---|
| 1 | Checkout completes; session metadata carries `user_id`/`tier` on the session **and** the subscription | `stripe_service.py` checkout params |
| 2 | `checkout.session.completed` → `_activate_subscription` → `tier = PRO`, credits granted | `webhooks.py::_handle_checkout_completed` |
| 3 | Renewal fails. Stripe sends `invoice.payment_failed`. **Handler logs the event id and returns 200.** No state written. | `webhooks.py` (pre-fix) |
| 4 | Stripe sends `customer.subscription.updated` with `status=past_due`. Handler sees `status != "canceled"` and calls `_activate_subscription`, **re-applying `tier = PRO`** (and able to top up the plan bucket). | `webhooks.py::_handle_subscription_updated` (pre-fix) |
| 5 | Retries exhaust. Stripe either cancels (→ `deleted`, which does downgrade) **or** marks the subscription `unpaid` and sends `updated`. The `unpaid` path re-activates PRO again, and **no further events ever arrive.** | same |
| 6 | Every gate reads `row.tier` | `sql_subscription_repo.py::get_tier_for_user` |
| 7 | Each new calendar month, no `monthly_credits` row exists for the new `%Y-%m` key, so the full tier grant is re-created **from the tier, not from a payment** | `credit_service.py`, `workers/converter_workers/dependencies.py` |
| 8 | Nothing expires it: no deadline, no sweep, no reconciliation | no such code |

### 3.2 Why it was not an edge case

The bug did not require an unusual trigger. Stripe's default dunning behaviour produces exactly this path for **every** declined renewal:

- The first failure fires `invoice.payment_failed` (ignored).
- `past_due` fires `customer.subscription.updated` (re-grants).
- The terminal state is either `canceled` (recovered by `deleted`) or `unpaid` (**not** recovered — no downgrade, no further events).

So the app's behaviour depended entirely on a Stripe dashboard setting, and one of the two outcomes was indefinite free access.

### 3.3 Blast radius

Every paid capability inherited the defect because all of them resolve entitlement from the same repository call:

storage quota (`files/service.py`), plan credits (`credit_service.py`, `credits.py`), conversion queue priority (`conversions.py`, `workflows.py`, `queue_priority_router.py`), AI assistant model level/quota/attachments/actions (`assistant.py`, `assistant_policy.py`, `sql_assistant_account_adapter.py`), dashboard (`dashboard.py`), MCP scope (`mcp/dependencies.py`), and worker-side credit gating (`workers/converter_workers/dependencies.py`).

That single choke point is also why the fix is small: correcting the repository's tier derivation repairs all nine call sites at once.

---

## 4. Consolidated findings

Severity: **P0** direct entitlement/revenue loss · **P1** lifecycle or enforcement defect · **P2** reliability/reconciliation · **P3** hygiene.

### P0

| ID | Finding | Evidence | Status |
|---|---|---|---|
| P0-1 | `invoice.payment_failed` performed no state change | `webhooks.py` (pre-fix) | **Fixed** |
| P0-2 | `subscription.updated` activated the paid tier for `past_due`, `unpaid`, `incomplete`, `incomplete_expired`, `paused` | `webhooks.py::_handle_subscription_updated` (pre-fix) | **Fixed** |
| P0-3 | Entitlement was `row.tier` with no time bound; no grace deadline existed | `sql_subscription_repo.py`, schema | **Fixed** |
| P0-4 | Calendar-month plan credits were re-granted from the tier every month regardless of payment | `credit_service.py`, worker `_consume_once` | **Fixed** (revocation clamps the paid bucket to the FREE grant) |

### P1

| ID | Finding | Evidence | Status |
|---|---|---|---|
| P1-1 | Late `invoice.payment_succeeded` re-activated a cancelled subscription | `_refresh_allowance_for_invoice` (pre-fix) | **Fixed** — status must be `active`/`trialing` |
| P1-2 | `current_period_end` read from the subscription object is `None` in this API version (it lives on the item) → carry-over expiry always `None` | `stripe_service.py`, verified against `stripe/_subscription_item.py` | **Fixed** |
| P1-3 | `/checkout` had no existing-subscription guard → two concurrent subscriptions, double billing | `subscriptions.py::create_checkout_session` | **Fixed** (409) |
| P1-4 | Upgrade applied the new tier + full credit grant before the proration invoice was paid (`payment_behavior` default `allow_incomplete`) | `stripe_service.py::change_subscription_plan` | **Fixed** (`error_if_incomplete`) |
| P1-5 | Events were keyed only on `user_id`; a stale event for an old subscription could downgrade a paying user | `_handle_subscription_updated`, `_handle_subscription_deleted` | **Fixed** (subscription-id guard) |
| P1-6 | `_downgrade_to_free` kept `stripe_subscription_id`, so `/change-plan` from a FREE ex-customer hit Stripe and returned 500 | `subscriptions.py::change_plan` | **Fixed** (tier guard) |
| P1-7 | No webhook event idempotency or ordering guard | `webhooks.py` | **Fixed** (event ledger + `last_payment_event_at`) |
| P1-8 | Credit consumption and purchase were unlocked read-modify-write (lost updates under concurrency) | `webhooks.py`, worker `_consume_once` | **Partially fixed** (purchase path is transactional; consumption remains advisory — see §6) |

### P2

| ID | Finding | Evidence | Status |
|---|---|---|---|
| P2-1 | `/subscription/status` reported `ACTIVE` for all non-`canceled` states and on Stripe failure | `subscriptions.py` | **Fixed** |
| P2-2 | `SubscriptionStatus.EXPIRED`/`TRIAL` were dead enum values; no `PAST_DUE` existed | `schemas/subscription.py` | **Fixed** (`PAST_DUE`, `GRACE_EXPIRED`) |
| P2-3 | No reconciliation: a missed webhook left a paid customer FREE or a lapsed one paid | no such code | **Not implemented** (see §6) |
| P2-4 | Credit-only buyers never get a `stripe_customer_id` persisted → portal/invoices/payment-methods unavailable | `_handle_checkout_completed` early return | **Open** |
| P2-5 | Invoices are proxied live, not persisted; Stripe errors render as "no invoices" | `stripe_service.py::list_invoices` | **Open** |
| P2-6 | `IntegrityError` in the credit grant is always treated as a duplicate, including FK violations → paid customer gets nothing and Stripe sees 200 | `_grant_purchased_credits` | **Open** |
| P2-7 | Webhook endpoint reads an unbounded body for chunked requests | `webhooks.py` | **Open** |

### P3

| ID | Finding | Status |
|---|---|---|
| P3-1 | Webhook signature tolerance is implicit (SDK default 300 s) | Open |
| P3-2 | `monthly_api_conversion_credits` is advertised but has no reader | Open |
| P3-3 | Per-key `rate_limit_per_minute` is stored and ignored; tier rate limits are dead config | Open |
| P3-4 | No refund/dispute handling (`charge.refunded`, `charge.dispute.created`) | Open |
| P3-5 | No Stripe idempotency keys on any write | Open |

---

## 5. Security findings (adversarial review)

The authentication boundary held up well: no IDOR was found on billing objects, tier is never accepted from the client, API-key and MCP principals resolve tier through the same repository call, and no Stripe secret is logged or returned.

| ID | Finding | Severity | Status |
|---|---|---|---|
| S-1 | Broken object-level authorization: **none found** on subscriptions, credits, payment methods, invoices, MCP downloads | — | Verified |
| S-2 | Webhook forgery: signature verified over raw bytes before parsing; secret is a `SecretStr`; failure path logs neither secret nor payload | — | Verified |
| S-3 | Client-writable tier: **none** — `/change-plan` and `/cancel` derive the subscription from the caller's own row | — | Verified |
| S-4 | Guest rate limit is selectable by an unverified header: any `X-API-Key` header moves a guest request to the 1000/min bucket | P2 | **Open** |
| S-5 | `STRIPE_SECRET_KEY` set without `STRIPE_WEBHOOK_SECRET` charged customers while rejecting every event | P1 | **Fixed** (boot validation) |
| S-6 | Credit gate fails open on any DB error in the worker | P1 | **Open** |
| S-7 | Guest uploads skip magic-byte validation and are never deleted when rejected | P2 | **Open** |

---

## 6. Outstanding risks

These are documented rather than fixed, because each either needs a product decision or expands scope beyond the entitlement defect:

1. **No reconciliation from Stripe.** A missed event still requires a human. The grace design is bounded (a missed *failure* cannot grant indefinite access once any status event arrives), but a customer can be stuck FREE after a missed `checkout.session.completed`. Recommended: a daily pass over non-terminal rows calling `get_subscription` and applying the same status mapping the webhook uses.
2. **Credit consumption remains advisory.** Jobs are not reserved at creation; the worker charges after completion and clamps at zero. Concurrent jobs can overspend. Fix: reserve at job creation in one atomic `UPDATE ... WHERE remaining >= cost`.
3. **One subscription per user.** The schema has `user_id UNIQUE` on `user_subscriptions`. The checkout guard now prevents *creating* a second one, but a second subscription created out-of-band (Dashboard, Portal) still cannot be represented.
4. **Credit-only buyers have no persisted customer.**
5. **No refunds/disputes handling.**
6. **Holiday calendar is opt-in and unset.** Deadlines already persisted are never recomputed, so enabling holidays later does not retroactively change existing windows.

---

## 7. Production-safety notes

- The migration is **additive only**: nullable columns, one non-nullable column with a server default, one new table, two indexes. No destructive operation, and it does not touch customer files, credit balances, or Stripe identifiers.
- **No backfill is performed.** `payment_state` defaults to `OK`, which is correct for every existing row: the database holds no evidence of a past failure, and inventing grace state could revoke a paying customer.
- Existing `stripe_subscription_id` and `stripe_customer_id` values are preserved and are now *load-bearing* for recovery, so they are deliberately not cleared on downgrade.
- Authored credit balances (`purchased_credits`) are never revoked by the grace machinery; only the plan bucket is clamped.
