# Information Security Policy

**ISO 27001:2022 (clause 5.2, A.5.1) · Transform (File Conversion SaaS)**
**Applies to:** all staff, contractors, and automated components operating the Transform platform
**Document owner:** CISO / InfoSec Lead · **Classification:** Internal — Confidential
**Last updated:** 2026-08-22

---

## 1. Purpose

To define the information security objectives, principles, and acceptable-use
rules that protect the confidentiality, integrity, and availability (CIA) of the
Transform platform — customer files, credentials, billing data, and all
supporting systems. It satisfies ISO 27001:2022 clause 5.2 (policy) and Annex A
A.5.1 (policies for information security).

## 2. Scope

This policy applies to:

- All staff and third-party contractors with access to the platform, code,
  credentials, or customer data.
- All platform components: API (`src/presentation/api/`), web SPA (`web/`),
  worker pipeline (`workers/`), domain/application/infrastructure layers,
  PostgreSQL, Redis, object storage, nginx ingress, and configuration (`.env`).
- Managed suppliers (Stripe, Neon, Upstash, Backblaze B2) by contract.

It does **not** cover customer-managed infrastructure or the customers'
own use of produced files (see `isms-overview.md` §3.2).

## 3. Definitions

| Term | Meaning |
|---|---|
| **Access token** | Short-lived JWT (30 min) used to authenticate API calls. |
| **Refresh token** | Long-lived JWT (7 days) used to obtain a new access token. |
| **API key** | Long-lived, per-user secret (`tr_…`) hashed at rest (SHA-256), shown once. |
| **Master key** | `ENCRYPTION_MASTER_KEY` — derives per-file/per-user AES keys for at-rest encryption. |
| **PII** | Personally identifiable information (user records). |
| **File content** | The uploaded/converted binary data processed by the platform. |
| **CIA** | Confidentiality, Integrity, Availability. |

---

## 4. Policy statements

### 4.1 Access control (least privilege)

- **Least privilege:** every actor (JWT user, API key, worker) is granted the
  minimum rights required. Ownership checks are enforced at the service/API
  layer, not merely in the UI — evidence: `file_service.py`,
  `auth_dependencies.py`, `events.py`, `conversions.py`.
- **Authentication:** the platform uses JWT (access + refresh) for interactive
  users and hashed API keys for programmatic access. Passwords are hashed with
  `pwdlib.PasswordHash.recommended()` (argon2) — **never stored or logged in
  plaintext** (`jwt_provider.py`).
- **MFA for privileged users:** *planned* — administrative/staff accounts MUST
  be protected with a second factor before go-live (see G7). Interactive
  customer accounts currently use password + JWT only.
- **API-key lifecycle:** keys are generated with `secrets.token_urlsafe(32)`,
  shown exactly once at creation, stored as a SHA-256 hash, and can be revoked
  or deleted per-owner (`api_key_service.py`).
- **Revocation:** revoking a token or API key takes effect on the next
  authenticated request; long-lived access should be minimised by short
  `ACCESS_TOKEN_EXPIRE_MINUTES`.

### 4.2 Password policy

- **Hashing:** passwords MUST be hashed (never reversible) using the
  `pwdlib` recommended scheme (argon2 by default) — `jwt_provider.py`.
- **Length:** enforce a minimum password length at registration (policy target
  ≥ 12 characters).
- **Failure handling:** authentication endpoints are rate-limited more strictly
  (`RATE_LIMIT_AUTH=10`) and emit an `auth_failure` audit event on failure
  (`audit.py`, `rate_limit.py`).
- **No password reuse across tiers** — a single-credential model applies; there
  is no separate admin password store.

### 4.3 Cryptography & key management

- **At-rest encryption:** objects MAY be encrypted with AES-256-GCM streaming,
  per-file/per-user keys derived via HKDF, with user-context binding in the AAD
  (`encryption.py`). `ENCRYPTION_MASTER_KEY` is a fernet-format master key.
- **Key rotation:** the service accepts `previous_master_keys` and tries the
  current then previous keys on decrypt, enabling rotation without data loss.
- **Encryption is NOT optional in production** — the operator MUST set
  `ENCRYPTION_MASTER_KEY` in the production `.env`; when unset, `get_file_encryption_service()`
  returns `None` and files are stored in plaintext (see G1). Provisioning is a
  policy obligation, not a choice.
- **Secrets management:** secrets (`SECRET_KEY`, `BACKBLAZE_SECRET_KEY`,
  `REDIS_URL`, `STRIPE_*`, `ENCRYPTION_MASTER_KEY`, `DATABASE_URL`) MUST live in
  `.env` (gitignored) or a secrets manager — **never committed to the repo**. All
  are typed `SecretStr` in `settings.py`. Production should use a managed secret
  store (e.g. Docker secrets / cloud secrets) rather than a committed `.env`.
- **Transport encryption:** HTTPS MUST be used for all external traffic; the
  API enables HSTS via middleware/nginx, though TLS termination must be provided
  by the edge terminator (see G2).
- **Key handling:** keys must never be logged. `audit.py` explicitly never logs
  secrets, tokens, passwords, or file contents.

### 4.4 Data classification & handling

- **Tiers of data:** (1) Public/operational metadata, (2) Internal —
  application config & source, (3) Confidential — user records, PII, and file
  contents, (4) Secret — credentials/keys.
- **Confidential (PII + file content):** MUST be encrypted at rest, namespaced
  per owner, and subject to the retention windows in `backup-recovery.md`.
- **Retention:** guest jobs 24h, guest files 24h, temp 1h, job archive 30d
  (`settings.py`, `cleanup_worker/worker.py`).
- **Logging:** audit logs must never contain secrets, tokens, passwords, or file
  contents (`audit.py`); they are structured JSON with correlation/actor context.

### 4.5 Asset management

- All platform assets are inventoried in the ISMS overview & risk register.
- Assets MUST be classified per §4.4 and have a named owner (see
  `isms-overview.md` §5).
- Third-party dependencies are pinned via `uv.lock` and frozen in the build
  (`worker.Dockerfile`); an automated SBOM/CVE review should be added (G8).

### 4.6 Change management

- All code changes MUST undergo code review before merge.
- **Tests must pass** before deployment — the `test backend` task runs the
  `pytest -q` suite; the `build frontend` task runs the web build.
- Database changes are managed via Alembic migrations; `RUN_MIGRATIONS` can be
  set to `false` so migrations are applied as a separate one-shot step in
  deployment (avoiding concurrent upgrades) — `settings.py`.
- **Boot-time validation** fails fast on an insecure production config
  (weak `SECRET_KEY`, wildcard CORS + credentials, plaintext object storage) —
  `settings.validate()`.

### 4.7 Incident response

- Incidents are classified by severity and handled per
  `incident-response-plan.md` (preparation, detection, containment, eradication,
  recovery, lessons learned).
- Security-relevant events are captured as audit events: `auth_failure`,
  `auth_success`, `rate_limited`, `webhook_failure`, `permission_denied`,
  `data_access` (`audit.py`).
- All suspected security incidents MUST be reported to the InfoSec Lead
  immediately; see escalation SLA in the IR plan.

### 4.8 Business continuity & backups

- Backups and recovery targets are defined in `backup-recovery.md`
  (RPO/RTO, Neon PITR, B2 versioning/lifecycle).
- The platform uses managed HA for PostgreSQL and object storage and 3 worker
  replicas — see `compose.yaml` & `worker.Dockerfile`.
- A tested DR runbook is a live gap (G6) and must be produced.

### 4.9 Supplier security

- Managed suppliers (Stripe, Neon, Upstash, Backblaze B2) MUST provide
  attestations (e.g. SOC 2) and, where processing personal data, a DPA.
- Secret handling MUST NOT require sharing our `SECRET_KEY` or master key with a
  supplier.
- Supplier risk and changes MUST be reviewed periodically (A.5.22). See the gap
  under A.5.19–A.5.23.

### 4.10 Awareness & training

- All staff with access MUST complete annual information-security awareness
  training (see A.6.3 gap for evidence).
- Secure-development expectations are documented in `Agents.MD` and the repository
  skill instructions under `.github/skills/`.

### 4.11 Physical security

- The platform is SaaS-only with no owned premises; physical controls are
  inherited from the managed providers (A.7.x — largely not applicable). Operators
  must not introduce on-premises hosting without a full ISMS scope review.

### 4.12 Monitoring & audit logging

- Prometheus `/metrics` and health/readiness probes monitor availability.
- Security audit logging MUST be enabled and retained for review; events carry
  `correlation_id` and actor context (`audit.py`).
- `/metrics` should be access-restricted (see G10).

---

## 5. Roles & responsibilities

| Role | Responsibility |
|---|---|
| CISO / InfoSec Lead | Accountable for this policy, risk acceptance, ISMS management review, exceptions. |
| AppSec Engineer | Enforces secure coding, reviews audit-log schema, owns key rotation, security testing. |
| DevOps Engineer | Operates prod, enforces boot-time validation, rotates secrets, owns backups/DR. |
| DPO | Data protection & GDPR overlap, retention, data-subject requests, supplier DPAs. |
| All staff/contractors | Comply with this policy, report security events, complete awareness training. |

Exception to any statement requires written approval from the InfoSec Lead and
must be recorded in the risk register with a compensating control.

---

## 6. Enforcement & non-compliance

- **Monitoring:** compliance is reviewed via the ISMS management review and
  periodic control verification (e.g. confirming `settings.validate()` blocks an
  insecure config).
- **Violations:** a security violation or policy breach is handled as a security
  incident (see `incident-response-plan.md`) and may escalate to disciplinary
  action for staff/contractors. Administrative access must be revoked
  immediately on suspicion of misuse (see A.8.2).
- **Review:** this policy is reviewed at least annually and after any material
  change in architecture, threat landscape, or legal requirement.

---

## 7. Related documents

- `access-control-policy.md` — detailed AuthN/AuthZ & token lifecycle
- `risk-assessment.md` — risk register & treatment
- `backup-recovery.md` — backup/retention/RPO/RTO & DR
- `incident-response-plan.md` — IR phases & escalation
- `control-implementation-matrix.md` — control → artifact mapping
