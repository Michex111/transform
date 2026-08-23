# Risk Assessment & Treatment Plan

**ISO 27001:2022 (clause 6.1) · Transform (File Conversion SaaS)**
**Document owner:** CISO / InfoSec Lead · **Classification:** Internal — Confidential
**Last updated:** 2026-08-22

> This register is a living assessment, not a fixed claim. It is grounded in the
> actual architecture and controls. "Current mitigation" always cites a real
> repository artifact. "Verification evidence" states what an auditor can
> demonstrate to confirm the mitigation.

---

## 1. Methodology

The assessment follows an **asset → threat → vulnerability → risk score →
control/treatment** model, adapted from ISO 27005.

1. **Identify assets** in scope (see `isms-overview.md` §3.1).
2. **Identify threats** (actors & motives) and **vulnerabilities** (weaknesses
   that a threat could exploit).
3. **Score** using a qualitative Likelihood × Impact matrix:

   | Likelihood | Impact | | |
   |---|---|---|---|
   | L=Low / M=Medium / H=High | C/I/A | Low = minor, Medium = material, High = severe |
   | **Score** | L×I | Score = product of qualitative labels | |

4. **Determine treatment** — we select from A.8 controls (mitigate), accept,
   transfer (to managed providers), or avoid.
5. **Record residual risk** and assign an **owner** for ongoing management.
6. **Verify** via the specified evidence.

**Risk scoring grid:**

| | Impact Low | Impact Medium | Impact High |
|---|---|---|---|
| **Likelihood Low** | Low | Medium | Medium |
| **Likelihood Medium** | Medium | Medium | High |
| **Likelihood High** | Medium | High | High |

**Treatment priority:** High = actively remediate & report to management;
Medium = plan & track; Low = accept/retain with review.

---

## 2. Scope & assets in the register

| Asset ID | Asset | Class |
|---|---|---|
| A1 | JWT signing `SECRET_KEY` | Credential (confidential) |
| A2 | Object-storage access/secret keys, API keys | Credential |
| A3 | PostgreSQL database (users, jobs, subscriptions, API-key hashes) | Data (confidential/integrity) |
| A4 | Redis queue + SSE stream + session cache | Data (availability/integrity) |
| A5 | Object storage bucket (input/output encrypted objects) | Data (confidentiality/integrity) |
| A6 | Converter worker process & converter binaries | System (integrity/availability) |
| A7 | Stripe webhook / billing plane | Data (integrity) |
| A8 | Encryption master key (`ENCRYPTION_MASTER_KEY`) | Cryptographic key |
| A9 | Dependencies / OSS supply chain | System (integrity) |
| A10 | PII + customer file content (GDPR overlap) | Data (confidentiality) |
| A11 | API-key usage plane (customer trust/abuse) | System/Data |

---

## 3. Risk register

| # | Risk / threat scenario | Assets | Likelihood | Impact | Score | Current mitigation (real control) | Residual risk | Treatment | Owner | Verification evidence |
|---|---|---|---|---|---|---|---|---|---|---|
| R1 | **Object-storage credentials leak** (B2/Minio key exposed via env, logs, or repo) allowing read/delete of bucket objects. | A2, A5 | M | H | **High** | Keys stored as `SecretStr` only in `.env` (never in repo — `.env` gitignored); `settings.validate()` requires `BACKBLAZE_USE_SSL=true` in prod; objects encrypted at-rest **when** `ENCRYPTION_MASTER_KEY` set (`encryption.py`). | M (encryption opt-in leaves plaintext risk) | Mitigate | DevOps | Open `.env.example` + `settings.py`; confirm `ENCRYPTION_MASTER_KEY` is set; confirm object keys are not in audit logs (`audit.py` never logs secrets). |
| R2 | **JWT secret compromise** (weak/shared `SECRET_KEY`) enabling token forgery & account takeover. | A1, A3, A10 | M | H | **High** | `settings.validate()` rejects `SECRET_KEY` <32 chars or known-insecure in production (`settings.py`); token has `exp` + `type` claim enforced (`jwt_provider.py`); access 30 min / refresh 7 days. | M | Mitigate | DevOps | Trigger `get_settings()` validation in prod env; show a token with a wrong `type` is rejected. |
| R3 | **Redis/queue loss** — Redis down/unavailable loses in-flight jobs, SSE replay, rate-limit state; in-memory rate-limiter fallback is per-process only, so multi-worker limits degrade. | A4, A6 | M | M | **Medium** | Redis-backed sliding window with graceful in-memory fallback (`rate_limit.py`); `restart: unless-stopped`; `build_rate_limit_middleware` uses short connect timeout. | M | Mitigate / transfer | DevOps | Stop Redis and confirm workers degrade to the in-memory fallback; document Upstash persistence. |
| R4 | **Malicious / malformed upload** — zip-bomb or oversized decompression, virus, or format-specific exploit (LibreOffice/ffmpeg/pandoc/calibre) consuming worker CPU/memory or breaking out. | A5, A6 | M | M-H | **Median-High** | Containerised non-root `appuser` (`worker.Dockerfile`); `WORKER_CONVERSION_TIMEOUT=600` timeout; `GUEST_MAX_FILE_SIZE`/tier caps; arg-list `subprocess.run` (no shell). | M-H | Mitigate | AppSec | **Gap G4:** no archive-expansion limit or AV scan. Add ClamAV scan + zip-bomb guard; enforce type sniffing. |
| R5 | **SQL injection** via user-controlled query parameters. | A3, A10 | L | H | **Medium** | All DB access via SQLAlchemy 2.0 ORM / async session (parameterized); `get_engine()` + `text("SELECT 1")` only for readiness. | L | Mitigate | AppSec | Grep for raw string-interpolated SQL; confirm all queries use ORM. |
| R6 | **SSRF / command injection via converters / object keys** — user-supplied file name or object key escaping bucket/namespace or reaching internal services. | A5, A6 | M | H | **High** | `sanitize_object_key`/`sanitize_filename` reject traversal & absolute paths (`sanitize.py`); `resolve_path` uses only `Path(file_location).name` (`processor.py`); output key namespaced `user/{id}/job/{job_id}`. | M | Mitigate | AppSec | Show `sanitize.py` unit tests for `..`, `\\`, absolute, and multi-slash keys; confirm converters take literal path args. |
| R7 | **Stripe webhook replay / forgery** — attacker replays or forges a webhook to grant credits/upgrade a subscription. | A7, A3 | M | M | **Medium** | `stripe.Webhook.construct_event(...)` verifies signature with `STRIPE_WEBHOOK_SECRET`; failure → `log_webhook_failure` + HTTP 400 (`webhooks.py`); secrets typed `SecretStr`. | M | Mitigate | Backend/DevOps | Send a request with a bad `stripe-signature` and observe 400 + `webhook_failure` audit event; handle idempotency (see G-gap: no event-id dedupe). |
| R8 | **DDoS / rate-limit bypass** — flooding auth or conversion endpoints; per-IP and per-key limits ineffective when attackers rotate IPs or use the in-memory fallback. | A1, A11, A4 | H | M | **High** | Tiered sliding-window limits (`settings.py` rates); auth endpoints stricter (`RATE_LIMIT_AUTH=10`); per-API-key hashing, per-JWT-token hashing (`rate_limit.py`); nginx edge zones (`nginx.conf`: guest 10r/m, api 30r/m, `client_max_body_size 1024M`). | M-H | Mitigate | DevOps | Hit an auth endpoint >10×/min → 429 + `rate_limited` event; confirm nginx zone applies. **Gap:** no per-token rate limit at edge; revisit for G5. |
| R9 | **Encryption-master-key loss or rotation failure** — losing `ENCRYPTION_MASTER_KEY` makes stored files undecryptable; rotation without previous-keys breaks decrypt. | A8, A5 | L | H | **Medium** | `FileEncryptionService` supports `previous_master_keys` and tries current then previous keys on decrypt (`encryption.py`); master key from `ENCRYPTION_MASTER_KEY`. | L | Mitigate | DevOps | Show key-rotation path: create file with key K1, rotate to K2 keeping K1 in `previous_master_keys`, verify decrypt succeeds; add KMS/HSM recommendation. |
| R10 | **Supply-chain / dependency CVE** — a vulnerable OSS package (cryptography, pyjwt, pillow, boto3…) exploited. | A9 | M | M-H | **Median-High** | Pinned deps via `uv.lock` + `uv sync --frozen`; `pyproject.toml` exact versions; minimal container (`python:3.14-slim`, `--no-dev`). | M | Mitigate | AppSec | **Gap:** no automated SBOM/CVE gate (A.8.8). Add `pip-audit`/`osv-scanner` in CI. |
| R11 | **Data retention / privacy (GDPR overlap)** — customer PII/file content held beyond stated retention, or guest data outliving policy. | A10, A5 | M | M | **Medium** | Retention windows: guest jobs 24h, guest files 24h, temp 1h, job archive 30d (`settings.py` + `cleanup_worker/worker.py`); files deleted object-then-row. | M | Mitigate | DPO | Demonstrate cleanup worker deletes expired guest objects; document deletion proof. **Gap:** no legal-hold/DELETED record or immutable audit log. |
| R12 | **Internal misuse of API keys / credentials** — a compromised or insider secret used to exceed quota, access others' files, or exfiltrate. | A11, A2, A1 | M | M-H | **Median-High** | API keys hashed (SHA-256), shown once, revocable per-owner (`api_key_service.py`); per-key rate limits; ownership checks on all resources; `data_access`/`permission_denied` audit events (`audit.py`); per-user encryption keys (`encryption.py`). | M | Mitigate | AppSec | List a user's keys (hashes only), revoke one, attempt an authenticated call → 401; grep `log_data_access` usage; confirm per-user key derivation isolates ciphertext. |

### Risk hot spots (H or M-H at a glance)

| Risk | Score | Primary gap to close |
|---|---|---|
| R1 Credential leak (plaintext storage) | High | G1 — enforce `ENCRYPTION_MASTER_KEY` |
| R2 JWT compromise | High | G1/G9 — strong secret + rotation |
| R6 SSRF / command injection | High | G6 — harden converter inputs, restrict egress |
| R8 DDoS / rate-limit bypass | High | G5 — edge + Redis durability, per-token edge limits |
| R4 Malicious upload | M-H | G4 — AV + zip-bomb limit |
| R10 Dependency CVE | M-H | G8 — CI CVE/SBOM gate |
| R12 Internal API-key misuse | M-H | G7 — MFA for admins, DLP |

---

## 4. Treatment summary

| Treatment | Count | Detail |
|---|---|---|
| **Mitigate** | 12 | All active risks have an applied or planned control (see register). |
| **Transfer** | 1 | R3 partially transferred to Upstash (Redis managed HA/persistence). |
| **Accept** | 0 | No risk accepted without residual-risk approval below. |
| **Avoid** | 0 | No asset avoided; the business need is present. |

---

## 5. Risk acceptance / approval sign-off

The following residual risks, marked **M**, are accepted as manageable with the
listed compensating controls and tracked for the next review:

| Risk | Residual | Rationale for acceptance | Compensating control |
|---|---|---|---|
| R1 | M (when master key set) | Plaintext opt-out is a deployment choice; default assets are encrypted when key present. | Enforce master key in prod (G1) |
| R5 | L | ORM parameterisation is used throughout; no raw SQL paths identified. | Code review + ORM |
| R9 | L | Key rotation supported; risk limited to operator discipline. | KMS/HSM recommendation |
| R11 | M | Retention enforced; GDPR overlap managed. | Legal-hold + deletion proof |

**Approval:**

| Role | Name | Signature | Date |
|---|---|---|---|
| CISO / InfoSec Lead | ___(print)___ | ____ | ____ |
| DPO | ___(print)___ | ____ | ____ |
| DevOps Lead | ___(print)___ | ____ | ____ |

> **Note:** This register must be revisited at least annually and after any
> significant architectural change (new converter, storage backend change, new
> managed supplier). Unaccepted residual risk above the threshold requires the
> InfoSec Lead's written approval before go-live.
