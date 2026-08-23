# ISMS Security Pack — Index

**Transform (File Conversion SaaS)** · ISO/IEC 27001:2022 · TÜV Süd Stage 1/2 assessment pack
**Generated:** 2026-08-22 · **Owner:** CISO / InfoSec Lead

This directory is a documentation-only evidence pack. **No application code was
modified.** It maps genuinely implemented controls to the actual repository
artifacts (file paths / config / middleware / Dockerfile / service) and is honest
about what is missing or planned.

---

## 1. Document index

| # | Document | What it answers |
|---|---|---|
| 1 | [`isms-overview.md`](./isms-overview.md) | What's in scope, ISMS ownership/roles, context & interested parties, scope boundary diagram. |
| 2 | [`statement-of-applicability.md`](./statement-of-applicability.md) | The core audit artifact — Annex A control-by-control status (Implemented / Partially / Not applicable / Planned) with evidence. |
| 3 | [`risk-assessment.md`](./risk-assessment.md) | Risk methodology, 8–12 realistic risks with L×I scoring, mitigations, residual risk, owners, verification evidence, acceptance sign-off. |
| 4 | [`security-policy.md`](./security-policy.md) | Top-level information security policy (purpose/scope, statements, roles, enforcement). |
| 5 | [`access-control-policy.md`](./access-control-policy.md) | AuthN/AuthZ detail: JWT access/refresh, API-key hashing, tier authz, ownership, least privilege, token lifetimes, revocation, audit. |
| 6 | [`backup-recovery.md`](./backup-recovery.md) | PostgreSQL (Neon PITR), object storage (B2 versioning/lifecycle), Redis (transient), RPO/RTO, cleanup/retention worker, DR runbook. |
| 7 | [`incident-response-plan.md`](./incident-response-plan.md) | IR phases, severity matrix, roles, escalation, audit-log & signal use. |
| 8 | [`control-implementation-matrix.md`](./control-implementation-matrix.md) | "How do we prove it" — ISO control → concrete repo artifact mapping. |

---

## 2. How to use this pack for a TÜV Süd audit

1. **Start with `isms-overview.md`** — confirm scope and boundary with the
   auditor (Stage 1). It defines in-scope services and explicit out-of-scope
   managed suppliers. The SoA preamble states that controls are assessed
   honestly, not over-claimed.
2. **Drill into `statement-of-applicability.md`** — the auditor will walk Annex A
   control by control. Each "Implemented" row cites an exact file path or config
   you can open during the audit. Each "Partially implemented" / "Planned" row
   is a live gap — go in prepared to discuss the remediation plan.
3. **Provide `risk-assessment.md`** as evidence of a living risk process: show
   the methodology, the register, and the signed acceptance of residual risk.
4. **Use `control-implementation-matrix.md`** to answer "where is X implemented?"
   quickly during an evidence trace.
5. **Be ready to demonstrate during Stage 2**, at minimum:
   - The app **refusing to boot** with a weak `SECRET_KEY` / wildcard CORS /
     plaintext object storage in production (`settings.validate()`).
   - **Structured audit events** on a failed login, a rate-limit hit, a webhook
     signature failure, and a permission denial.
   - The **rate-limiter** returning `429` with `Retry-After` and emitting a
     `rate_limited` audit event.
   - The **security headers** present on a response (CSP, HSTS, X-Frame-Options…).
   - A **downloaded object** decrypting through the API when
     `ENCRYPTION_MASTER_KEY` is set.

---

## 3. Gap / action list (what an auditor will flag)

These are the concrete items to close before or just after Stage 2. Ordered by
materiality.

| # | Gap | Where it shows | Action | Priority |
|---|---|---|---|---|
| G1 | **At-rest encryption is opt-in** — `get_file_encryption_service()` returns `None` when `ENCRYPTION_MASTER_KEY` is unset, so files are stored **plaintext** in object storage. Prod validation only guards SSL, not encryption. | `encryption.py`, `settings.py`, `.env.example` | Set `ENCRYPTION_MASTER_KEY` in production, and add a production validation that rejects a missing key. | High |
| G2 | **No TLS terminator in the stack** — compose exposes nginx on `:80` only (HTTP). HSTS is added by `security_headers.py`/`nginx.conf` but is inert over plain HTTP. | `deployment/docker/compose.yaml` (nginx `80:80`), `nginx.conf` | Terminate TLS (e.g. Caddy / Traefik / managed LB) and redirect `:80 → :443`; enable HSTS only behind HTTPS. | High |
| G3 | **`ALLOWED_ORIGINS` default is `[]` but `.env.example` sets `["*"]`** — wildcard CORS with credentials is rejected only in *production*; dev/CI could run with it. | `settings.py`, `.env.example`, `main.py` (CORSMiddleware) | Set explicit origins in `.env.example`, and add a non-production guard or warn. | Medium |
| G4 | **No malware / archive-bomb scanning of uploads** — converters process untrusted files (LibreOffice, ffmpeg, pandoc, calibre). No AV hook or archive-expansion limit. | `workers/converter_workers/processor.py`, `converters/functions/*` | Add file-type magic sniffing, an AV/ClamAV-in-container scan, and enforce archive expansion depth/size limits. | Medium |
| G5 | **Redis/queue is transient with no DR** — no replica/durability for the queue; SSE replay is in-memory from Redis Streams. A Redis loss drops in-flight job events. | `redis_stream_job_queue.py`, `redis_stream_status_queue.py`, `backup-recovery.md` | Confirm Upstash persistence/durability, document SSE replay limits, add dead-letter handling. | Medium |
| G6 | **No documented, executable DR runbook in-repo** — backup relies on Neon PITR and B2 versioning/lifecycle but there is no tested restore script or runbook to hand the auditor. | `backup-recovery.md` (gap noted) | Write and test a restore procedure; record restore RTO/RPO proof. | Medium |
| G7 | **No MFA for privileged/admin users** — JWT + password only; no second factor for staff/administrative access. | `access-control-policy.md` (gap) | Enable MFA for admin/staff accounts; document the control. | Medium |
| G8 | **Rotate secrets on a schedule** — `SECRET_KEY`, Stripe secrets, B2 keys, master key have no automated rotation policy/review. | `security-policy.md`, `incident-response-plan.md` | Define rotation period and a monthly secrets review; track in the SoA. | Low/Medium |
| G9 | **Security awareness & supplier assurance artifacts not in-repo** — no evidence of staff training completion or Stripe/Neon/Upstash/B2 attestations & DPAs on file. | `security-policy.md`, `statement-of-applicability.md` (A.6.3, A.5.19–A.5.23) | Collect & archive training records and supplier attestations/DPAs. | Low |
| G10 | **Prometheus `/metrics` is unauthenticated** — `GET /metrics` exposes operational metadata with no access control, and is served on the same origin as the public API. | `main.py` (`metrics_endpoint`), `nginx.conf` | Restrict `/metrics` to a monitoring network / internal-only route, or add auth. | Low |

---

## 4. Document hygiene

- All documents are Markdown, human-readable, and greppable — suitable for
  auditor note-taking and evidence extraction.
- Every "Implemented" control references at least one concrete repository path.
- Where a control you expect to exist is **not** present, it is explicitly flagged
  as a gap rather than glossed over.
- Review cadence: the ISMS management review should re-run
  `risk-assessment.md` and `statement-of-applicability.md` at least annually.
