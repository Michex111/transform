# Statement of Applicability (SoA)

**ISO/IEC 27001:2022 Annex A · Transform (File Conversion SaaS)**
**Document owner:** CISO / InfoSec Lead · **Classification:** Internal — Confidential
**Last updated:** 2026-09-22

This Statement of Applicability assesses every ISO/IEC 27001:2022 Annex A
control against the Transform platform. The scope and exclusions are defined in
`isms-overview.md`. Status values: **Implemented**, **Partially implemented**,
**Not applicable**, **Planned**.

> **Convention:** "Evidence / Gap" cites a concrete repository path, config key,
> or documented procedure. Where the control is **Partially implemented** or
> **Planned**, the specific gap is named in the same column.

---

## A.5 — Organizational controls

| ID | Control | Status | Evidence / Gap |
|---|---|---|---|
| **A.5.1** | Policies for information security | Implemented | Top-level policy in `docs/security/security-policy.md`; reviewed as part of the ISMS management review. |
| **A.5.2** | Information security roles and responsibilities | Implemented | Roles defined in `isms-overview.md` §5 (CISO, AppSec Eng, DevOps, DPO). Ownership encoded in the DDD module boundaries (`domain/`, `application/`, `infrastructure/`). |
| **A.5.3** | Segregation of duties | Partially implemented | Code-review + `deploy.replicas: 3` workers provide no-single-shipper; migration via `RUN_MIGRATIONS` can be disabled for a separate one-shot step (`settings.py`). **Gap:** no formal JIRA/PR approval gate documented; environment separation relies on `.env` only (see A.8.31). |
| **A.5.4** | Management responsibilities | Implemented | Managers own ISMS resourcing; risk acceptance is delegated to the InfoSec Lead (`isms-overview.md` §5). |
| **A.5.5** | Contact with authorities | Partially implemented | Incident escalation documented in `incident-response-plan.md` (DPO → ICO/market Regulator). **Gap:** no tested/dated regulator contact register. |
| **A.5.6** | Contact with special interest groups | Partially implemented | We consume vendor security advisories (Stripe/Neon/Upstash/B2). **Gap:** not systematically recorded. |
| **A.5.7** | Threat intelligence | Partially implemented | Monitoring feeds from managed providers + Prometheus; **Gap:** no dedicated TIP; relies on manual advisory review. |
| **A.5.8** | Information security in project management | Implemented | Secure-by-design in the DDD layered architecture; security headers/rate-limit/audit are wired at app composition (`src/presentation/api/main.py`). |
| **A.5.9** | Inventory of information and other associated assets | Partially implemented | Asset classes enumerated in `isms-overview.md` §3.1 and `risk-assessment.md` §3. **Gap:** no formal asset register (CMDB) with owners, classification, and retention per asset. |
| **A.5.10** | Acceptable use of information | Implemented | `security-policy.md` §Access Control & Asset Management; access delegated via JWT/API-key per tier. |
| **A.5.11** | Return of assets | Not applicable | SaaS-only: no physical company assets on customer premises. Internal assets handled in the HR policy (not in scope). |
| **A.5.12** | Classification of information | Partially implemented | Secrets clearly grouped as `SecretStr` in `src/infrastructure/config/settings.py`; PII/file contents identified. **Gap:** no explicit four-tier classification label applied across the codebase/data model. |
| **A.5.13** | Labelling of information | Not applicable | No physical media; classification is communicated via documentation and config labels, not printed labels. |
| **A.5.14** | Information transfer | Implemented | File transfer is a first-class service (`file_transfer_service.py`); encrypted at rest via `encryption.py` (AES-256-GCM) and keyed per-owner. Upload TTL `UPLOAD_URL_TTL_MINUTES=15`; object-key sanitization (`sanitize.py`). |
| **A.5.15** | Access control | Implemented | See `access-control-policy.md`. JWT access+refresh, hashed API keys, tier-based rate limits, `CurrentUser` dependency with ownership checks (`auth_dependencies.py`, `file_service.py`, `events.py`). |
| **A.5.16** | Identity management | Implemented | Users table via Alembic migrations (`0002_create_users.py`), `is_active` flag enforced in `auth_dependencies.py`. **Email ownership is verified before an account can authenticate:** `0015_email_verification.py` adds `users.email_verified` (+ hashed single-use token columns), and an unverified address is refused at sign-in (`routers/v1/users.py`). Existing accounts were grandfathered to verified by that migration. See G11 for the deployment caveat (enforced only when an email transport is configured). |
| **A.5.17** | Authentication information | Implemented | Passwords hashed with `pwdlib.PasswordHash.recommended()` (argon2) in `jwt_provider.py`; API keys SHA-256 hashed, never stored plaintext, shown once (`api_key_service.py`). Email-verification tokens are 32-byte CSPRNG values stored **only** as a SHA-256 digest (a DB leak cannot be replayed to verify accounts), single-use, and expiring (`domain/security/enitities/email_verification.py`). |
| **A.5.18** | Access rights | Implemented | Ownership checks on jobs (`conversions.py:236,294`), files/folders (`file_service.py`), upload sessions (`upload.py`), API keys (`api_key_service.py`), webhooks. |
| **A.5.19** | Information security in supplier relationships | Partially implemented | Managed providers (Stripe/Neon/Upstash/B2, plus the transactional-email relay when `RESEND_API_KEY`/`SMTP_HOST` is configured) are identified & out-of-scope. **Gap:** supplier DPAs/SLAs/attestations not archived in-repo. |
| **A.5.20** | Addressing information security within supplier agreements | Planned | No formal supplier security agreement on file. **Gap:** add security clauses/DPAs for Neon, Upstash, Backblaze B2, Stripe. |
| **A.5.21** | Managing information security in the ICT supply chain | Partially implemented | Dependency versions pinned in `pyproject.toml` + `uv.lock`; container built from `python:3.14-slim` with pinned deps. **Gap:** no automated SBOM/CVE gate in CI. |
| **A.5.22** | Monitoring, review and change management of supplier services | Partially implemented | Status/readiness probes (`main.py`) monitor our service; managed providers monitored by their own dashboards. **Gap:** no formal periodic supplier risk review. |
| **A.5.23** | Information security for use of cloud services | Partially implemented | Cloud services managed by providers (Neon/Upstash/B2); secrets in `.env`; at-rest encryption at B2 keyed to the app. **Gap:** no cloud security posture statement / shared-responsibility matrix on file. |
| **A.5.24** | Planning and preparing for information security incident management | Implemented | See `incident-response-plan.md` (phases, severity, roles, escalation). |
| **A.5.25** | Assessing and deciding on information security events | Implemented | Audit logger as event triage source (`audit.py`: `auth_failure`, `rate_limited`, `webhook_failure`, `permission_denied`, `data_access`). |
| **A.5.26** | Responding to information security incidents | Implemented | `incident-response-plan.md` §Response & Containment; service restarts via `restart: unless-stopped`. |
| **A.5.27** | Learning from information security incidents | Implemented | `incident-response-plan.md` §Lessons learned; ISMS management review. |
| **A.5.28** | Collection of evidence | Implemented | Structured JSON audit logs with `correlation_id` + actor (`audit.py`); metrics counters (`main.py`). |
| **A.5.29** | Information security during disruption | Planned | **Gap:** no documented, tested DR runbook; see `backup-recovery.md` §DR runbook (gap note). |
| **A.5.30** | ICT readiness for business continuity | Implemented | Managed-service HA (Neon PITR, B2 versioning) + `restart: unless-stopped` + 3 worker replicas. See `backup-recovery.md`. |
| **A.5.31** | Legislative, regulatory, contractual requirements | Partially implemented | GDPR overlap recognised (retention & deletion in `backup-recovery.md`). **Gap:** no formal legal/regulatory register. |
| **A.5.32** | Intellectual property rights | Partially implemented | License/attribution in `LICENSE`, `README.md`. **Gap:** no consolidated third-party licence/SBOM review. |
| **A.5.33** | Protection of records | Partially implemented | Retention windows for job history (`JOB_ARCHIVE_AFTER_DAYS=30`) & guest data; deletion via cleanup worker. **Gap:** no documented legal-hold or immutable-audit-log retention policy. |
| **A.5.34** | Privacy and protection of personally identifiable information | Implemented | Per-file encryption, PII in users table, GDPR overlap addressed in `security-policy.md` §Data Protection & `backup-recovery.md` §Retention. |
| **A.5.35** | Independent review of information security | Planned | **Gap:** no internal audit or independent penetration test scheduled/evidenced. |
| **A.5.36** | Compliance with policies/social engineering — see A.6.3 | — | Cross-referenced to A.6.3 (awareness). |
| **A.5.37** | Documented operating procedures | Implemented | Orchestration in `workers/converter_workers/processor.py`; deployment in `deployment/docker/compose.yaml`; operational runbooks in `backup-recovery.md` & `incident-response-plan.md`. |

---

## A.6 — People controls

| ID | Control | Status | Evidence / Gap |
|---|---|---|---|
| **A.6.1** | Personnel screening | Not applicable* | No in-house employee onboarding process is documented/evidenced in this repo. *Assessment is N/A for the codebase, but the information owner must complete this as an HR control.* |
| **A.6.2** | Terms and conditions of employment | Not applicable* | As above — HR control outside the repo. |
| **A.6.3** | Information security awareness, education and training | Partially implemented | Secure-development guidance embedded in `Agents.MD` and skill instructions (`.github/skills/`). **Gap:** no evidence of staff completion records / awareness programme. |
| **A.6.4** | Disciplinary process | Planned | **Gap:** a non-compliance policy statement in `security-policy.md` §Enforcement, but no disciplinary process artifact. |
| **A.6.5** | Responsibilities after termination or change of employment | Planned | SaaS-only, no facility access; **Gap:** access-revocation on staff departure not documented. |
| **A.6.6** | Confidentiality or non-disclosure agreements | Partially implemented | `security-policy.md` states confidentiality; **Gap:** no signed NDA inventory. |
| **A.6.7** | Remote working | Not applicable | Platform is fully SaaS; no customer-facing remote-working surface we control. |
| **A.6.8** | Information security event reporting | Implemented | `incident-response-plan.md` §Reporting; audit-log events (`audit.py`) capture security-relevant activity. |

---

## A.7 — Physical controls

| ID | Control | Status | Evidence / Gap |
|---|---|---|---|
| **A.7.1** | Physical security perimeter | Not applicable | No owned physical premises; multi-tenant SaaS, perimeter delegated to the colocation/cloud provider and managed suppliers. |
| **A.7.2** | Physical entry | Not applicable | As above — inherited from provider. |
| **A.7.3** | Securing offices, rooms and facilities | Not applicable | As above. |
| **A.7.4** | Physical security monitoring | Not applicable | As above. |
| **A.7.5** | Protecting against physical and environmental threats | Not applicable | Provider-managed DC controls (power/cooling/fire/flood) inherited. |
| **A.7.6** | Working in secure areas | Not applicable | SaaS-only. |
| **A.7.7** | Clear desk and clear screen | Not applicable | SaaS-only; no customer premises or company desks applicable to the platform. |
| **A.7.8** | Equipment siting and protection | Not applicable | No physical equipment. |
| **A.7.9** | Security of assets off-premises | Not applicable | No physical assets off-premises. |
| **A.7.10** | Storage media | Partially implemented | Objects stored encrypted at rest in B2 **when** `ENCRYPTION_MASTER_KEY` is set (see G1). **Gap:** media lifecycle/decommission not documented. |
| **A.7.11** | Supporting utilities | Not applicable | Provider-managed. |
| **A.7.12** | Cabling security | Not applicable | Provider-managed. |
| **A.7.13** | Equipment maintenance | Not applicable | Provider-managed. |
| **A.7.14** | Secure disposal or reuse of equipment | Not applicable | Provider-managed. |

---

## A.8 — Technological controls

| ID | Control | Status | Evidence / Gap |
|---|---|---|---|
| **A.8.1** | User end point devices | Not applicable | No corporate end-point fleet; customers use their own devices. |
| **A.8.2** | Privileged access rights | Implemented | `CurrentUser` dependency enforces per-request identity; ownership checks across resources; API-key per-tier limits. Admin/privileged-grant paths are inventory-limited. |
| **A.8.3** | Information access restriction | Implemented | Ownership checks (`file_service.py`, `conversions.py`, `events.py`, `upload.py`, `api_keys.py`); tier-based size/rate limits (`settings.py`). |
| **A.8.4** | Access to source code | Partially implemented | Repo gates via branch protections presumed; **Gap:** no explicit source-code access/approval policy documented. |
| **A.8.5** | Secure authentication | Implemented | JWT (HS256) access (30 min) + refresh (7 days) with `type` claim enforced (`jwt_provider.py`); API keys SHA-256 hashed, shown once, `tr_` prefix (`api_key_service.py`); argon2 via `pwdlib` (`jwt_provider.py`). Sign-up additionally requires a **verified email address** before credentials are accepted, and the verification endpoints share the strict auth rate-limit bucket (`routers/v1/users.py`) — see G11 for the fail-open caveat. |
| **A.8.6** | Capacity management | Partially implemented | Tier-based file size caps (`settings.py`), worker consumer group + `WORKER_CONVERSION_TIMEOUT=600`, Nginx `client_max_body_size 1024M` (`nginx.conf`). **Gap:** `WORKER_BATCH_SIZE` is defined and documented but **not wired** into the stream consumer (the read batch is fixed at 1 per stream), so it cannot yet be relied on as a capacity control. |
| **A.8.7** | Protection against malware | Partially implemented | Containers run non-root (`worker.Dockerfile`), pin deps, `--no-dev`. **Gap:** no AV/malware scan of uploaded files or downloaded conversion artifacts (see G4). |
| **A.8.8** | Management of technical vulnerabilities | Partially implemented | Pinned deps (`pyproject.toml`+`uv.lock`), boot-time config validation (`settings.validate()`). **Gap:** no automated SAST/DAST/CVE scan in CI. |
| **A.8.9** | Configuration management | Implemented | `settings.py` central config, `.env.example`, boot-time production validation; `RUN_MIGRATIONS` toggle for separate migration steps. |
| **A.8.10** | Information deletion | Implemented | Cleanup worker deletes guest jobs 24h, guest files 24h, temp 1h, archives jobs 30d (`workers/cleanup_worker/worker.py`, settings). |
| **A.8.11** | Data masking | Partially implemented | Audit logs never log secrets/tokens/contents (`audit.py`); API keys hashed. **Gap:** no explicit masking of PII in operational/analyses logs. |
| **A.8.12** | Data leakage prevention | Partially implemented | At-rest encryption, object-key sanitization, ownership isolation. **Gap:** no outbound DLP control on file downloads beyond authz. |
| **A.8.13** | Information backup | Implemented | See `backup-recovery.md` — Neon PITR, B2 versioning/lifecycle, Redis transient. |
| **A.8.14** | Redundancy of information processing facilities | Implemented | 3 worker replicas + `restart: unless-stopped`; managed HA providers. |
| **A.8.15** | Logging | Implemented | `audit.py` (JSON events, correlation_id, actor); operational structured logging (`loggers.py`); Prometheus metrics (`main.py`). |
| **A.8.16** | Monitoring activities | Implemented | `/health`, `/ready` probes; `/metrics` Prometheus; `rate_limited` audit event; webhook failure events. |
| **A.8.17** | Clock synchronisation | Partially implemented | Config uses `datetime.now(UTC)` throughout. **Gap:** no explicit NTP configuration documented for the containers. |
| **A.8.18** | Use of privileged utility programs | Partially implemented | `linux` containers run as `appuser` uid 10001 (non-root); **Gap:** sudo/privileged-usage policy not documented. |
| **A.8.19** | Installation of software on operational systems | Partially implemented | Files pinned and built via `uv sync --frozen`; **Gap:** no change-approval / software-install policy artifact. |
| **A.8.20** | Networks security | Implemented | Docker bridge network `converter-net`; nginx edge rate-limiting & headers; per-service networks. See `nginx.conf`, `compose.yaml`. |
| **A.8.21** | Security of network services | Partially implemented | `converter-net` isolates services; public ingress is nginx only. **Gap:** no documented firewall rule set / no explicit egress restriction on workers. |
| **A.8.22** | Segregation of networks | Implemented | Separate services (api/worker/worker-cleanup/postgres/redis/minio/nginx) on dedicated network; workers connect directly to cloud services via `.env`. |
| **A.8.23** | Web filtering | Not applicable | No outbound web proxy/filter is required for the documented conversion workflow. |
| **A.8.24** | Use of cryptography | Implemented | AES-256-GCM streaming, per-user HKDF keys, AAD user-context binding, key-rotation, Fernet in-memory (`encryption.py`); JWT; SHA-256 API keys. |
| **A.8.25** | Secure development life cycle | Implemented | Hexagonal domain/application/infrastructure split; converter registry; `settings.validate()` fail-fast; `Agents.MD` SDL guidance; `sanitize.py` path-traversal guard. |
| **A.8.26** | Application security requirements | Implemented | Security headers middleware (`security_headers.py`), boot-time production validation (rejects weak secret / wildcard CORS / plaintext storage — `settings.py`). See G2 for TLS gap. |
| **A.8.27** | Secure system architecture | Implemented | Clean architecture, tiered rate limits, ownership checks, sealed DTOs/ports; encryption keyed per-owner. |
| **A.8.28** | Secure coding | Implemented | `sanitize.py` prevents path traversal; converters use arg-list `subprocess.run` (no `shell=True`); SQL via SQLAlchemy ORM (parameterized). See G-* for SSRF/zip-bomb gaps. |
| **A.8.29** | Security testing in development | Partially implemented | `tests/` (unit/integration) incl. fake storage/queue; web build runs. **Gap:** no SAST/DAST/pen-test in pipeline (see A.8.8). |
| **A.8.30** | Outsourced development | Not applicable | No outsourced dev for the platform. |
| **A.8.31** | Separation of development, test and production environments | Partially implemented | `ENVIRONMENT` flag drives `settings.validate()` (prod-only guards). **Gap:** dev/test/prod environments share the same `.env` shape; no separate environment/secret management formally documented. |
| **A.8.32** | Change management | Implemented | `RUN_MIGRATIONS` toggle; `uv.lock` frozen deps; worker/converter code isolated. **Gap:** no formal change-request ticket workflow documented. |
| **A.8.33** | Test information | Implemented | Tests use fakes (`tests/fakes/`) and fixtures, never real data. |
| **A.8.34** | Protection of information systems during audit testing | Implemented | Audit logging is read-only; `/metrics` is non-mutating; test data is isolated in fakes/fixtures. |

---

## Appendix — Controls assessed as "Not applicable"

A.6.1, A.6.2, A.6.5 (in-repo), A.6.6 (in-repo), A.7.1–A.7.9, A.7.11–A.7.14,
A.8.1, A.8.23, A.8.30. These are excluded because the platform is a multi-tenant
SaaS with managed providers and no owned physical infrastructure. The
information owner must confirm the N/A rationale for A.6.x (people) with HR.

> **Note on honest assessment:** The highest-value gaps are G1 (encryption
> opt-in), G2 (no TLS), G4 (no malware scan), G7 (no MFA for admins), and G5/G6
> (Redis DR + untested DR runbook). See `READM​E.md` §3 for the full action list.
