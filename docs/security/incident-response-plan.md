# Incident Response Plan

**ISO 27001:2022 A.5.24–A.5.28 · Transform (File Conversion SaaS)**
**Document owner:** CISO / InfoSec Lead · **Classification:** Internal — Confidential
**Last updated:** 2026-08-22

This plan describes how the organisation prepares for, detects, contains,
eradicates, recovers from, and learns from information security incidents
affecting the Transform platform. It is grounded in the actual security
telemetry the application emits.

---

## 1. Phases

| Phase | Objective | Key activities |
|---|---|---|
| **1. Preparation** | Be ready before an incident | Roles defined (§3); contacts & escalation (§5); audit logging enabled; runbooks in `backup-recovery.md`; backup of key config. |
| **2. Detection** | Identify & triage a possible event | Watch security audit events & probes (§6); `/metrics`, `/health`, `/ready`; alert on anomalies. |
| **3. Containment** | Limit blast radius | Isolate affected service/replica; revoke credentials; rate-limit / drain queue; block attacker (nginx / firewall). |
| **4. Eradication** | Remove the root cause | Patch CVE, rotate secrets, remove malware, restore from clean backup (`backup-recovery.md`). |
| **5. Recovery** | Return to normal service | Restore DB/storage/Redis; run migrations; verify `/ready`; re-queue jobs. |
| **6. Lessons learned** | Prevent recurrence | Post-incident review; update risk register & SoA; corrective action. |

---

## 2. Severity matrix

| Severity | Description | Impact | Response SLA |
|---|---|---|---|
| **SEV-1 (Critical)** | Active exploitation, data breach, credential compromise, or platform-wide loss of service/availability. | Confidentiality/Integrity/Availability lost; regulatory/legal exposure. | Contain within 30 min; escalate to CISO immediately; 24/7. |
| **SEV-2 (High)** | Targeted attack, isolated account compromise, sustained abuse (DDoS), webhook or billing integrity issue. | Significant but bounded impact; potential future escalation. | Respond within 4 business hours; report to InfoSec Lead. |
| **SEV-3 (Medium)** | Suspicious activity, failed signature/detection spike, rate-limit anomalies, single-job failures. | No confirmed breach; may indicate weakness. | Triage within 24 h; monitor. |
| **SEV-4 (Low)** | Noise, minor misconfiguration, low-risk audit events. | No impact. | Logged & reviewed at regular cadence. |

---

## 3. Incident response roles

| Role | Responsibility |
|---|---|
| **Incident Commander (IC)** | Owns the incident end-to-end; makes containment/recovery decisions; communicates status. (Default: CISO/InfoSec Lead or on-call DevOps.) |
| **AppSec Engineer** | Technical security analysis; exploit root-cause; evidence preservation; patch. |
| **DevOps Engineer** | Infrastructure containment/restore; queue/database handling; service restart. |
| **DPO** | Regulator/individual notification (GDPR breach), legal-hold, record-keeping. |
| **Communications** | Internal + customer comms as required; avoid speculative disclosure. |

---

## 4. Report & escalation

- **Internal report:** any staff/contractor who suspects a security event MUST
  report it to the InfoSec Lead / IC immediately (do not delete evidence).
- **Escalation path:** AppSec/DevOps → Incident Commander → CISO → (if PII/data
  breach) DPO → regulator (ICO / market Regulator within GDPR 72 h for reportable
  breaches).
- **Customer notification:** SEV-1/SEV-2 incidents affecting customer files or
  billing may require customer notification; coordinate via Communications with
  the IC.

---

## 5. Detection signals & audit-log use

The app emits structured JSON events via `src/infrastructure/logging/audit.py`.
These are the primary detection feed:

| Signal | Event | What it indicates | Action |
|---|---|---|---|
| Repeated failed logins | `auth_failure` (reason=invalid_or_expired_token / invalid_or_expired_api_key) | Credential stuffing, brute-force, stolen token/key | Revoke & rotate; block source IP; enable MFA (G7). |
| Auth success spike from a new location | `auth_success` | Possible account takeover | Verify user; revoke refresh tokens; force re-auth. |
| 429 bursts | `rate_limited` | Abuse/DoS or a misbehaving client | Apply edge limit (`nginx.conf`); investigate source; possibly blacklist. |
| Webhook signature failures | `webhook_failure` (provider=stripe) | Forged/replayed webhook, or misconfig | Check `STRIPE_WEBHOOK_SECRET`; inspect Stripe logs; add idempotency. |
| Ownership denial | `permission_denied` | Possible IDOR / horizontal privilege attempt | Review the resource path; confirm ownership guard; log the actor. |
| Data access events | `data_access` | Normal data read/write; use for forensics & evidence | Correlate with `correlation_id`/`actor`; export for audit. |
| Availability anomalies | `/health`, `/ready`, `/metrics` | Outage, dependency (DB/Redis) failure | Trigger the DR runbook (`backup-recovery.md`). |

**Correlation:** every audit event carries a `correlation_id` and `actor`
(`set_audit_context`/`clear_audit_context`), so a single request or job can be
reconstructed across events.

---

## 6. Containment & eradication playbooks (by scenario)

### 6.1 Credential / API-key compromise
1. **Revoke** the affected API key(s) via `DELETE /api/v1/api-keys/{id}` or the
   service method (`api_key_service.py`). Keys are hashed, so the plaintext is
   irrecoverable — regenerate. **Rotate** the JWT `SECRET_KEY` and all affected
   tokens (shorten `ACCESS_TOKEN_EXPIRE_MINUTES` if necessary).
2. **Contain:** block the source IP at nginx/firewall; issue a global session
   invalidation if a `SECRET_KEY` change is needed.

### 6.2 Object-storage or encryption-key compromise
1. **Rotate** `BACKBLAZE_*` keys and `ENCRYPTION_MASTER_KEY`.
2. For at-rest files, re-encrypt if the master key is suspected compromised
   (the service supports `previous_master_keys` for rotation —
   `encryption.py`).
3. **Contain:** restrict bucket policies; audit object access via B2 logs.

### 6.3 Malicious / malformed upload (zip-bomb, AV, converter exploit)
1. **Contain:** isolate the worker container (non-root `appuser` bounds the
   blast radius); stop consuming the affected queue.
2. **Eradicate:** quarantine the object; scan with AV (G4 gap); patch the
   converter or add archive-expansion limits.
3. **Recover:** re-queue legitimate jobs; verify no lateral movement via egress
   restrictions.

### 6.4 Supply-chain / dependency CVE
1. Determine exposure; patch to a fixed version; bump `uv.lock` and rebuild from
   `worker.Dockerfile`.
2. Scan for the vulnerable version; re-deploy. Add CI CVE gate (G8 gap).

### 6.5 DDoS / rate-limit bypass
1. **Contain:** tighten nginx `limit_req` zones (`nginx.conf`), enable
   WAF/edge protection, add IP blacklists.
2. Validate app-level limits (per-key/per-token) — the in-memory fallback is
   per-process, so the 3-worker cluster loses global limits if Redis is down
   (G5). Keep Redis healthy.

---

## 7. Recovery

- **Restore** PostgreSQL (Neon PITR) and object storage (B2 versioning) per
  `backup-recovery.md`; **rebuild** Redis and re-consume the job queue.
- **Verify** `/health`, `/ready`, `/metrics`, and a representative end-to-end
  conversion.
- **Confirm** audit logging is flowing and security headers are present.

## 8. Evidence collection & preservation

- Preserve **audit logs**, **Nginx access logs**, **Prometheus metrics**, and
  **B2/Neon/Upstash provider logs** as evidence.
- Never destroy evidence during containment (copy before deleting).
- Record timestamps, correlation IDs, affected assets/accounts, and actions taken
  in the incident record. **Do not log secrets or file contents** (`audit.py`).

## 9. Lessons learned

- Conduct a post-incident review within 5 business days for SEV-1/SEV-2.
- Update the **risk register** (`risk-assessment.md`) and **SoA**
  (`statement-of-applicability.md`) with new controls or changed likelihood/impact.
- Add corrective actions + owners + due dates; track to closure in the ISMS
  management review.

## 10. Testing & maintenance

- **Tabletop exercise:** at least annually, run a SEV-1 exercise (e.g. simulated
  credential compromise) with the IC, AppSec, DevOps, and DPO; record outcomes.
- **Review:** the plan is reviewed at least annually and after any significant
  architectural change (new converter, storage backend, managed supplier).
