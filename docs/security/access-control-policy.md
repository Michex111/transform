# Access Control Policy

**ISO 27001:2022 A.5.15–A.5.18, A.8.2–A.8.5 · Transform (File Conversion SaaS)**
**Document owner:** CISO / InfoSec Lead · **Classification:** Internal — Confidential
**Last updated:** 2026-08-22

This policy details the authentication (AuthN) and authorization (AuthZ)
mechanisms, token and API-key lifecycle, tier-based limits, ownership checks,
least privilege, and the audit trail for access events. It is grounded directly
in the repository's auth implementation.

---

## 1. Authentication model

The platform supports **two** authentication methods, resolved by the
`get_current_user` dependency in `src/presentation/api/dependencies/auth_dependencies.py`:

| Method | Mechanism | When used |
|---|---|---|
| **JWT bearer token** | `Authorization: Bearer <jwt>` | Interactive web app users |
| **API key** | `X-API-Key: tr_…` | Programmatic / server-to-server clients |

The dependency tries `X-API-Key` first; if absent, it falls back to JWT. Both
paths resolve to an active `UserModel`, then call
`set_audit_context(...)` and `log_auth_success(...)`.

> **Note:** an API key takes precedence over a bearer token. If a client sends
> both, the API key decides the identity & limit. This is intentional for
> per-key rate limiting, but operators should document it (a mis-set header can
> authenticate as the key owner rather than the JWT subject).

---

## 2. JWT access & refresh tokens

Implementation: `src/infrastructure/auth/jwt_provider.py`.

| Property | Access token | Refresh token |
|---|---|---|
| Lifetime | `ACCESS_TOKEN_EXPIRE_MINUTES = 30` | `REFRESH_TOKEN_EXPIRE_DAYS = 7` |
| `type` claim | `"access"` (or absent, accepted) | `"refresh"` (required) |
| `exp` claim | required | required |
| `sub` claim | required | required |
| Algorithm | `HS256` (configurable) | HS256 |

- **Verification:** `verify_access_token` decodes with the algorithm allow-list
  `[ALGORITHM]` and requires `exp` and `sub`. It rejects tokens whose `type` is not
  `access`/absent, and refuses a refresh token where an access token is required
  (and vice-versa). This prevents token-type confusion.
- **Refresh flow:** refresh tokens are long-lived and are used to mint new access
  tokens; they are not themselves used for resource requests.
- **Signing key:** `SECRET_KEY` — a `SecretStr`. In production,
  `Settings.validate()` rejects a weak (<32 chars) or known-insecure value
  (`settings.py`).
- **Session/caching:** the app uses Redis (`redis_session_adapter.py`) for
  upload-session caches; JWT itself is stateless (no server-side session store),
  so revocation of an individual access token is not immediate — mitigated by
  short `ACCESS_TOKEN_EXPIRE_MINUTES`.

**Token-type confusion guard:** the `type` claim disambiguates access vs refresh
tokens, so a stolen refresh token cannot be used directly as an access token.

---

## 3. API-key lifecycle & hashing

Implementation: `src/application/services/api_key_service.py`,
`src/presentation/api/routers/v1/api_keys.py`.

| Phase | Behaviour |
|---|---|
| **Generation** | `secrets.token_urlsafe(32)` → prefixed with `tr_`; 32 bytes of entropy. |
| **Storage** | Only the **SHA-256 hash** is persisted (`hash_api_key`); plaintext is never stored. |
| **Display** | The plaintext is returned **exactly once** in the create response `key` field. It cannot be retrieved again. |
| **Expiry** | Optional `expires_in_days` (default 30); `is_valid()` checks status + expiry. |
| **Revocation** | `revoke(key_id, user_id)` marks the status `REVOKED` and checks the key is owned by `user_id` (`int(api_key.user_id) != user_id → None`). |
| **Deletion** | `delete(key_id, user_id)` permanently removes an owned key. |
| **Last-used** | `touch_last_used` updates `last_used_at` on each successful authenticate. |

- **Keys are never stored in plaintext** (A.5.17). The `tr_` prefix allows
  recognition without reveal. The prefix is also exposed in list responses
  (`prefix=tr_`).
- **Per-key rate limit:** the rate limiter keys on
  `sha256(x-api-key)` (hashed, so the raw key is never part of the Redis key) →
  `RATE_LIMIT_API_KEY_DEFAULT = 1000` (`rate_limit.py`).

---

## 4. Authorization / ownership checks (least privilege)

Authorization is resource-level and enforced in application services, not just
the UI. Representative evidence:

| Resource | Ownership guard | Location |
|---|---|---|
| Conversion jobs | `if job.user_id is not None and job.user_id != user_id: 404` | `conversions.py:236` (read), `:294` (output download) |
| Job event stream (SSE) | `job.user_id != current_user.id: 404` | `events.py:47` |
| Files | `row.user_id != user_id → FileRecordNotFoundError` | `file_service.py:227` (`get_file`) |
| Folders | `folder.user_id != user_id → FolderNotFoundError`; move/cycle guard | `file_service.py:134,178` |
| Upload sessions | `session.user_id != str(current_user.id)` | `upload.py:56,75,110` |
| API keys | `int(api_key.user_id) != user_id → None` | `api_key_service.py:67,77` |
| Credits (subscription) | `owner_id` scoped to user | `credits.py:135` |

**Guest (unauthenticated) scope:** guest conversions and uploads are ownerless
(`user_id IS NULL`), so they have no cross-user ownership risk; they are subject
to strict rate limits (`RATE_LIMIT_GUEST=10`) and a short retention window
(24h).

**Tier-based limits (authorization by subscription):**

| Tier | Rate limit (req/min) | Max file size |
|---|---|---|
| guest | `RATE_LIMIT_GUEST=10` | 50 MB |
| free | `RATE_LIMIT_FREE=30` | 100 MB |
| pro | `RATE_LIMIT_PRO=100` | 500 MB |
| pro_plus | `RATE_LIMIT_PRO_PLUS=200` | 1 GB |
| enterprise | `RATE_LIMIT_ENTERPRISE=500` | 1 GB |

Defined in `settings.py` and enforced by `rate_limit.py`. Tier also drives the
credit multiplier discount (see `settings.py` `CREDIT_MULTIPLIER_*`) and the
credit-based gate in `processor.py`.

---

## 5. Least privilege & segregation

- **Service accounts / workers:** converter and cleanup workers run as the
  non-root `appuser` (uid 10001) inside containers (`worker.Dockerfile`), which
  limits the blast radius of a converter exploit.
- **Network segregation:** each service (api, worker, worker-cleanup,
  postgres, redis, minio, nginx) is a distinct container on `converter-net`;
  the API is the only publicly exposed service (nginx → api), while workers
  connect directly to cloud providers via `.env` (`compose.yaml`).
- **Per-user crypto isolation:** each file is encrypted under a key derived via
  HKDF from the master key bound to the owner (`encryption.py`), so ciphertext
  written for one user cannot be decrypted by another, even with storage access.

---

## 6. Session / token lifetimes & revocation

| Credential | Lifetime | Revocation |
|---|---|---|
| JWT access | 30 min | No server-side revocation (stateless); short TTL is the mitigation. |
| JWT refresh | 7 days | No server-side revocation; short `REFRESH_TOKEN_EXPIRE_DAYS`. |
| API key | configurable (default 30 days) | Immediate — `revoke()`/`delete()`; `is_valid()` excludes revoked/expired. |
| Upload session cache | `UPLOAD_URL_TTL_MINUTES=15` (Redis) | Expires via TTL (`redis_session_adapter.py`). |

**Recommended hardening (gaps):**
- Add a **token revocation denylist** (Redis) for high-risk revocations (A.8.5).
- Add **MFA** for administrative/privileged accounts (G7).
- **Rotate** `SECRET_KEY` and API keys on a defined schedule (see
  `security-policy.md` §4.3, G8).

---

## 7. Audit trail of access events

All authentication/authorization actions emit structured, JSON, secret-free
events via `src/infrastructure/logging/audit.py`:

| Event | Trigger | Level | Example fields |
|---|---|---|---|
| `auth_failure` | missing/invalid/expired token, bad API key, inactive user | WARN | `reason`, `user_id` (sometimes) |
| `auth_success` | successful JWT or API-key auth | INFO | `user_id`, `method` |
| `rate_limited` | limit exceeded | WARN | `scope`, `key` (truncated), `limit` |
| `permission_denied` | ownership/authz denial | WARN | `resource` |
| `data_access` | data read/write action | INFO | `user_id`, `action`, `resource` |

- Every event carries a `correlation_id` and `actor` via
  `set_audit_context(...)`/`clear_audit_context()`, so a single request/job can
  be correlated across events.
- The audit logger **never** logs secrets, tokens, passwords, or file contents;
  rate-limit keys are truncated (`key.rsplit(":", 1)[-1][:16]`) and API keys are
  only referenced by hash (`rate_limit.py`).
- These events are the primary source for the IR plan's detection/triage phase
  (`incident-response-plan.md`).

---

## 8. Enforcement & review

- The `get_current_user` dependency is the single choke point for AuthN; every
  protected router uses it (`CurrentUser`).
- Ownership guards live in the domain/application layer where they can be
  unit-tested, not only in the router.
- This policy is reviewed at least annually; changes to auth model (new token
  type, new auth provider) require ISMS risk reassessment.
