# Control Implementation Matrix

**ISO 27001:2022 · Transform (File Conversion SaaS) — "How do we prove it"**
**Document owner:** AppSec Engineer · **Classification:** Internal — Confidential
**Last updated:** 2026-09-22

This matrix maps every assessed ISO control to the concrete repository artifact
(file path / config / Dockerfile / middleware / service) that a TÜV Süd auditor
can open to verify it. It is the quick reference for evidence tracing during
Stage 2. Status codes: **I** = Implemented, **P** = Partially implemented,
**N/A** = Not applicable, **PL** = Planned (see `statement-of-applicability.md`
for full rationale & gaps).

> All paths are relative to the repository root.

---

## A.5 — Organizational

| Control | Status | Artifact / Evidence |
|---|---|---|
| A.5.1 Policies | I | `docs/security/security-policy.md`; `Agents.MD` (repo guidance). |
| A.5.2 Roles & responsibilities | I | `docs/security/isms-overview.md` §5; module boundaries `domain/`, `application/`, `infrastructure/`, `presentation/`. |
| A.5.3 Segregation of duties | P | `deploy.replicas: 3` (`deployment/docker/compose.yaml`); `RUN_MIGRATIONS=false` option (`settings.py`). |
| A.5.4 Management responsibility | I | `docs/security/isms-overview.md` §5 (RACI). |
| A.5.5 Contact with authorities | P | `docs/security/incident-response-plan.md` §4 (escalation). |
| A.5.6 Special interest groups | P | Not systematically recorded. |
| A.5.7 Threat intelligence | P | Managed-provider advisories; Prometheus metrics. |
| A.5.8 InfoSec in project mgmt | I | Secure-by-design composition `src/presentation/api/main.py` (middleware + validation + routers). |
| A.5.9 Asset inventory | P | `docs/security/isms-overview.md` §3.1; `docs/security/risk-assessment.md` §2. |
| A.5.10 Acceptable use | I | `docs/security/security-policy.md` §4.1, §4.5. |
| A.5.11 Return of assets | N/A | SaaS-only. |
| A.5.12 Classification | P | `SecretStr` typing (`settings.py`); PII/file-content identified. |
| A.5.13 Labelling | N/A | No physical media. |
| A.5.14 Information transfer | I | `src/application/services/file_transfer_service.py`; `src/infrastructure/adapters/security/encryption.py`; `src/infrastructure/adapters/storage/sanitize.py`. |
| A.5.15 Access control | I | `docs/security/access-control-policy.md`; `src/presentation/api/dependencies/auth_dependencies.py`. |
| A.5.16 Identity mgmt | I | `src/infrastructure/database/migrations/versions/0002_create_users.py`; `user.is_active` check in `auth_dependencies.py`. **Verified email ownership:** `0015_email_verification.py` (`users.email_verified` + hashed token columns, existing accounts grandfathered); sign-in refuses an unverified address (`src/presentation/api/routers/v1/users.py`). |
| A.5.17 Authentication info | I | `jwt_provider.py` (argon2 via `pwdlib`); `api_key_service.py` (SHA-256 hash, show-once). Email-verification tokens: 32-byte CSPRNG values stored **only** as a SHA-256 digest, single-use, time-boxed (`src/domain/security/enitities/email_verification.py`). |
| A.5.18 Access rights | I | `file_service.py`, `conversions.py:236,294`, `events.py:47`, `upload.py:56,75,110`, `api_key_service.py:67,77`. |
| A.5.19 Supplier relationships | P | `deployment/docker/compose.yaml` (managed providers); `docs/security/isms-overview.md` §3.2. Includes the transactional-email relay once `RESEND_API_KEY`/`SMTP_HOST` is configured. |
| A.5.20 Supplier agreements | PL | No DPA/SLA artifact in-repo. |
| A.5.21 ICT supply chain | P | `pyproject.toml` + `uv.lock`; `worker.Dockerfile` (frozen deps). |
| A.5.22 Monitor/review suppliers | P | `/health`, `/ready` (`main.py`); provider dashboards. |
| A.5.23 Cloud services | P | Managed Neon/Upstash/B2; secrets in `.env`. |
| A.5.24 Plan incident mgmt | I | `docs/security/incident-response-plan.md`. |
| A.5.25 Assess/decide events | I | `src/infrastructure/logging/audit.py` (event schema). |
| A.5.26 Respond to incidents | I | `docs/security/incident-response-plan.md` §6; `restart: unless-stopped` (`compose.yaml`). |
| A.5.27 Learn from incidents | I | `docs/security/incident-response-plan.md` §9. |
| A.5.28 Collection of evidence | I | `audit.py` (JSON, correlation_id, actor); Prometheus counters (`main.py`). |
| A.5.29 InfoSec during disruption | PL | `docs/security/backup-recovery.md` §7 runbook (untested). |
| A.5.30 ICT readiness for BC | I | Managed HA (Neon PITR, B2 versioning); 3 replicas; `backup-recovery.md`. |
| A.5.31 Legal/regulatory | P | GDPR overlap in `backup-recovery.md` §5, `security-policy.md` §4.4. |
| A.5.32 IP rights | P | `LICENSE`, `README.md`. |
| A.5.33 Protection of records | P | Retention settings + `workers/cleanup_worker/worker.py`. |
| A.5.34 Privacy & PII | I | `encryption.py`; retention windows; `security-policy.md` §4.4. |
| A.5.35 Independent review | PL | No internal audit / pen-test artifact. |
| A.5.37 Documented procedures | I | `workers/converter_workers/processor.py`; `compose.yaml`; runbooks. |

---

## A.6 — People

| Control | Status | Artifact / Evidence |
|---|---|---|
| A.6.1 Screening | N/A (in-repo) | Not an in-repo control. |
| A.6.2 Terms of employment | N/A (in-repo) | HR control. |
| A.6.3 Awareness & training | P | `Agents.MD`, `.github/skills/` guidance. |
| A.6.4 Disciplinary process | PL | `security-policy.md` §Enforcement. |
| A.6.5 After termination | PL | SaaS-only. |
| A.6.6 NDAs | P | `security-policy.md` §4. |
| A.6.7 Remote working | N/A | SaaS-only. |
| A.6.8 Event reporting | I | `incident-response-plan.md` §4; `audit.py`. |

---

## A.7 — Physical

| Control | Status | Artifact / Evidence |
|---|---|---|
| A.7.1–A.7.9, A.7.11–A.7.14 | N/A | Provider-managed; no owned premises. |
| A.7.10 Storage media | P | At-rest encryption (`encryption.py`) **when** master key set. |

---

## A.8 — Technological

| Control | Status | Artifact / Evidence |
|---|---|---|
| A.8.1 End point devices | N/A | SaaS. |
| A.8.2 Privileged access rights | I | `CurrentUser` dependency; ownership checks; API-key per-tier limits. |
| A.8.3 Information access restriction | I | `file_service.py`, `conversions.py`, `events.py`, `upload.py`, `api_keys.py`. |
| A.8.4 Source code access | P | Repo branch protections (assumed). |
| A.8.5 Secure authentication | I | `src/infrastructure/auth/jwt_provider.py`; `api_key_service.py`; token `type` claim enforcement. Sign-up requires a **verified email address** before credentials are accepted (see A.5.16; deployment caveat G11). |
| A.8.6 Capacity mgmt | I | `settings.py` tier size/rate limits; worker consumer group (`WORKER_CONSUMER_GROUP`) + `WORKER_CONVERSION_TIMEOUT` (the per-read batch size `WORKER_BATCH_SIZE` is defined but **not yet wired** into the consumer — see the worker note below); `nginx.conf` `client_max_body_size 1024M`. |
| A.8.7 Malware protection | P | Non-root container + frozen deps; **no AV scan** (G4). |
| A.8.8 Technical vulnerabilities | P | Pinned deps + boot validation; **no SAST/DAST/CVE gate** (G8). |
| A.8.9 Configuration mgmt | I | `src/infrastructure/config/settings.py`; `.env.example`; `RUN_MIGRATIONS` toggle. |
| A.8.10 Information deletion | I | `workers/cleanup_worker/worker.py` + settings. |
| A.8.11 Data masking | P | `audit.py` never logs secrets; API keys hashed. |
| A.8.12 Data leakage prevention | P | At-rest encryption + sanitize + ownership isolation. |
| A.8.13 Information backup | I | `docs/security/backup-recovery.md` (Neon PITR, B2 versioning). |
| A.8.14 Redundancy | I | `deploy.replicas: 3`; `restart: unless-stopped`; managed HA. |
| A.8.15 Logging | I | `src/infrastructure/logging/audit.py`; `loggers.py`; Prometheus counters. |
| A.8.16 Monitoring | I | `/health`, `/ready`, `/metrics` (`main.py`); `rate_limited`/`webhook_failure` events. |
| A.8.17 Clock synchronisation | P | `datetime.now(UTC)` everywhere; **no NTP doc**. |
| A.8.18 Privileged utilities | P | Non-root `appuser` (uid 10001) in `worker.Dockerfile`. |
| A.8.19 Software installation | P | `uv sync --frozen`; **no install-approval policy**. |
| A.8.20 Network security | I | `deployment/docker/nginx.conf`; `compose.yaml` networks. |
| A.8.21 Network services | P | `converter-net`; **no egress restriction / firewall ruleset**. |
| A.8.22 Segregation of networks | I | Separate per-service containers + `converter-net` + direct cloud connections. |
| A.8.23 Web filtering | N/A | No outbound proxy required. |
| A.8.24 Use of cryptography | I | `src/infrastructure/adapters/security/encryption.py` (AES-256-GCM, HKDF, AAD, rotation); JWT; SHA-256. |
| A.8.25 Secure development lifecycle | I | Hexagonal layers; `settings.validate()` fail-fast; `sanitize.py`; `Agents.MD`. |
| A.8.26 Application security reqs | I | `src/presentation/api/middleware/security_headers.py`; `settings.validate()` (G2/G3 gaps). |
| A.8.27 Secure architecture | I | Clean architecture; tiered limits; ownership; per-owner keys. |
| A.8.28 Secure coding | I | `sanitize.py`; arg-list `subprocess.run` (no shell); SQLAlchemy ORM parameterization. |
| A.8.29 Security testing in dev | P | `tests/unit|integration|api`; **no SAST/DAST/pen-test** (G8). |
| A.8.30 Outsourced development | N/A | No outsourced dev. |
| A.8.31 Separation of envns | P | `ENVIRONMENT` flag + prod guards; same `.env` shape across envs (G3). |
| A.8.32 Change management | I | `uv.lock` frozen; `RUN_MIGRATIONS`; isolated worker/converter code. |
| A.8.33 Test information | I | `tests/fakes/`, `tests/fixtures/` (never real data). |
| A.8.34 Protection during audit testing | I | Read-only audit logs; non-mutating `/metrics`; isolated fixtures. |

---

## Repository quick-reference (evidence landmarks)

| Concern | File |
|---|---|
| Config & boot validation | `src/infrastructure/config/settings.py` |
| Audit logging (JSON events) | `src/infrastructure/logging/audit.py` |
| Security headers (CSP/HSTS/etc.) | `src/presentation/api/middleware/security_headers.py` |
| Rate limiting (Redis + fallback) | `src/presentation/api/middleware/rate_limit.py`, `src/infrastructure/adapters/security/rate_limiter.py` |
| At-rest encryption | `src/infrastructure/adapters/security/encryption.py` |
| Object-key path-traversal guard | `src/infrastructure/adapters/storage/sanitize.py` |
| AuthN (JWT, password hashing) | `src/infrastructure/auth/jwt_provider.py` |
| Email verification (token lifecycle + delivery) | `src/domain/security/enitities/email_verification.py`; `src/infrastructure/adapters/email/*`; `src/application/services/email_templates.py` |
| AuthZ / ownership | `src/presentation/api/dependencies/auth_dependencies.py`; `src/application/services/*.py` |
| API-key hashing & lifecycle | `src/application/services/api_key_service.py` |
| Stripe webhook verification | `src/presentation/api/routers/v1/webhooks.py` |
| SSE events (owner-enforced) | `src/presentation/api/routers/v1/events.py` |
| Worker conversion/orchestration | `workers/converter_workers/processor.py` |
| Cleanup / retention worker | `workers/cleanup_worker/worker.py` |
| Edge (nginx) controls | `deployment/docker/nginx.conf` |
| Deployment topology | `deployment/docker/compose.yaml` |
| Container hardening | `deployment/docker/worker.Dockerfile` |
| SSE / queue adapters | `src/infrastructure/adapters/queues/*` |
| Session cache | `src/infrastructure/adapters/cache/redis_session_adapter.py` |
| DB migrations | `src/infrastructure/database/migrations/versions/*` |

---

## Evidence-collection checklist for the auditor

Use this to locate each control's proof during a walkthrough:

1. **Open `settings.py`** — point to `validate()` for the production failure-fast
   on weak `SECRET_KEY`, wildcard CORS, and plaintext object storage.
2. **Emit an audit event** — trigger a failed login and show the JSON
   `auth_failure` line with `correlation_id`/`actor`.
3. **Show a `429`** — exceed an auth rate limit and show `rate_limited` + the
   `Retry-After` header.
4. **Inspect headers** — curl the API and show `Content-Security-Policy`,
   `Strict-Transport-Security`, `X-Frame-Options`, `X-Content-Type-Options`.
5. **Test path traversal** — attempt an upload with a `..` file name and show
   `sanitize.py` rejecting it.
6. **Verify ownership** — attempt to read another user's job/file and show a
   404 / `permission_denied` event.
7. **Confirm hashing** — inspect the DB for API-key rows and confirm only
   SHA-256 hashes are stored.
8. **Prove decryption** — with `ENCRYPTION_MASTER_KEY` set, show a stored object
   is ciphertext and the API streams a decrypted download (G1 caveat: unset
   master key = plaintext).
9. **Review retention** — show the cleanup worker config & deletion logic.
10. **Prove email verification** — register an account, show the delivered
    activation link (`EMAIL_BACKEND=console` logs it locally, so no provider is
    needed for the demo), attempt sign-in *before* activating and show the `403
    EMAIL_NOT_VERIFIED` gate, activate, confirm sign-in now succeeds, then show
    the same link replayed is rejected (single-use).
