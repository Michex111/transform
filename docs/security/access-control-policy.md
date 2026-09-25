# Access Control Policy

**ISO 27001:2022 A.5.15–A.5.18, A.8.2–A.8.5 · Transform (File Conversion SaaS)**
**Document owner:** CISO / InfoSec Lead · **Classification:** Internal — Confidential
**Last updated:** 2026-09-22

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

### Email address verification (sign-up)

Before either method above can be used, the account must prove it controls its
email address. Implementation: `src/domain/security/enitities/email_verification.py`,
`src/application/services/email_templates.py`,
`src/infrastructure/adapters/email/`, and
`src/presentation/api/routers/v1/users.py`.

| Step | Endpoint | Behaviour |
|---|---|---|
| Register | `POST /api/users/register` | Creates the user with `email_verified = false` and emails a single-use activation link. |
| Activate | `POST /api/users/verify-email` | Consumes the token and returns `{ok, already_verified, username, message}`. |
| Resend | `POST /api/users/resend-verification` | Issues a fresh link; **always** `202`, whether or not the address exists, so the endpoint cannot be used to enumerate accounts. |
| Sign in | `POST /api/users/token` | `403` `{"detail": {"code": "EMAIL_NOT_VERIFIED", …}}` while unverified — evaluated *after* the password check, so it cannot be used to discover which usernames exist. |

Token properties:

- 32 bytes of CSPRNG entropy (`secrets.token_urlsafe`), persisted **only** as a
  SHA-256 digest. A leaked database (backup, replica, log dump) therefore cannot
  be replayed to verify — and thus take over — arbitrary accounts. SHA-256 rather
  than argon2 is deliberate: the token is high-entropy random rather than
  attacker-guessable, and verification is an indexed digest lookup.
- Single-use (the digest is cleared on success) and time-boxed by
  `EMAIL_VERIFICATION_TTL_HOURS` (default 24). Activation is a single conditional
  `UPDATE … RETURNING` matching only an unexpired, still-stored digest, so two
  concurrent clicks cannot both succeed and a missing expiry fails closed.
- Resends are throttled by `EMAIL_VERIFICATION_RESEND_COOLDOWN_SECONDS` (default
  60), which also stops the endpoint being used as a mail-bomb against a third
  party's inbox.
- `verify-email` and `resend-verification` are both unauthenticated and act on a
  secret or trigger a send, so they share the strict auth bucket
  (`RATE_LIMIT_AUTH=10`, `rate_limit.py`).
- Verification failures return one generic message for forged, expired, and
  already-used tokens, so probing cannot distinguish the three cases.

> **Deployment caveat (G11).** The gate is enforced only when a real email
> transport is configured (`EMAIL_BACKEND` resolving to `resend` or `smtp`).
> With no transport the API fails **open**: it logs an ERROR at boot and permits
> unverified sign-in. This is intentional — refusing sign-in for a link that can
> never be delivered would permanently lock out every new registration — and it
> self-heals once a transport is configured. `EMAIL_VERIFICATION_REQUIRED=false`
> disables the gate explicitly as an operational escape hatch.

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
| **Last-used** | `touch_last_used` updates `last_used_at` on a successful authenticate, coalesced to at most once per 5 minutes (`_last_used_is_stale`) so a hot API key does not force a row update on every request. |

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

| Tier | Rate limit (req/min) | Max file size | Storage quota |
|---|---|---|---|
| guest | `RATE_LIMIT_GUEST=10` | 50 MB | none (ephemeral, 24h retention) |
| free | `RATE_LIMIT_FREE=30` | 5 GB | 5 GB |
| pro | `RATE_LIMIT_PRO=100` | 5 GB | 50 GB |
| pro_plus | `RATE_LIMIT_PRO_PLUS=200` | 5 GB | 100 GB |
| enterprise | `RATE_LIMIT_ENTERPRISE=500` | 5 GB | 1 TB |

The per-file cap and the storage quota are **independent controls**: the cap is
how large one file may be (`*_MAX_FILE_SIZE`, capped by
`MAX_UPLOAD_FILE_SIZE_BYTES`), the quota is how much the account may hold in
total (`TierPolicy.storage_quota_bytes`). A FREE account has both at 5 GB, so it
may upload one 5 GB file and then nothing more — that is intended. Both are
checked on upload; the quota check reads `SUM(user_files.file_size_bytes)` (the
same figure the dashboard shows) and is enforced authoritatively at upload
finalize under a row lock on the user. Uploads at or above
`MULTIPART_THRESHOLD_BYTES` use S3 multipart upload with presigned part URLs.

Defined in `settings.py` and enforced by `rate_limit.py` and `FileService`. Tier
also drives the credit multiplier discount (see `settings.py`
`CREDIT_MULTIPLIER_*`) and the credit-based gate in `processor.py`.

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
- **Open action:** the sign-up email-verification control above added both a new
  auth step and a new out-of-scope supplier (the email relay). `risk-assessment.md`
  has not yet been re-scored for it — the register needs a risk entry covering
  account-identity impersonation (registering an address the user does not
  control) and abuse of the sign-up/resend path as a mail vector, with G11 as its
  residual. Tracked here rather than silently assumed.
