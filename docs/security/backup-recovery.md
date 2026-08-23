# Backup & Recovery Policy & Procedures

**ISO 27001:2022 A.8.13, A.5.29–A.5.30 · Transform (File Conversion SaaS)**
**Document owner:** DevOps Engineer · **Classification:** Internal — Confidential
**Last updated:** 2026-08-22

This document defines backup, retention, RPO/RTO targets, and the disaster
recovery procedure for the Transform platform. It relies on the managed
providers' durability features and the application's own retention/cleanup
logic.

---

## 1. Data inventory & backup strategy

| Data store | Purpose | Backup mechanism | RPO target | RTO target |
|---|---|---|---|---|
| **PostgreSQL** (Neon) | Users, conversion jobs, subscriptions, credits, API-key hashes, user files/folders | **Neon Point-In-Time Recovery (PITR)** + automated continuous backups | ≤ 5 minutes (Neon default PITR) | ≤ 1 hour (restore + run migrations) |
| **Object storage** (Backblaze B2) | Uploaded input objects + converted output objects (encrypted when master key set) | **B2 versioning** + lifecycle rules (retain N days, then expire) | ≤ 24 h (version retention window) | ≤ 2 h (restore from version) |
| **Redis** (Upstash) | Job queue (Streams), SSE status stream, rate-limit counters, upload-session cache | **Transient** — treated as non-durable; Upstash persistence optional | Not guaranteed | Not applicable (rebuild) |
| **Docker volumes** (local dev) | postgres_data, redis_data, minio_data | Local named volumes (dev only) | n/a | n/a |

> **Key principle:** PostgreSQL and object storage hold the authoritative data
> and are backed up. Redis is a **transient** message/state bus — losing it
> requires re-queued jobs and in-flight progress events to be replayed from the
> Postgres job table + re-emitted, but **no customer data is lost** because the
> durable copy lives in Postgres + B2.

---

## 2. Point-in-time recovery (PostgreSQL / Neon)

- Neon provides **continuous PITR** and automated backups for the Postgres
  cluster. The application schema is managed by Alembic migrations
  (`alembic.ini`, `src/infrastructure/database/migrations/versions/`).
- **Restore procedure (Neon):**
  1. From the Neon console, create a **branch / point-in-time restore** at the
     desired timestamp (or most recent).
  2. Point `DATABASE_URL` in `.env` to the restored branch and restart the API.
  3. If the application version changed, apply pending Alembic migrations
     (`RUN_MIGRATIONS=true` or a one-shot `alembic upgrade head`).
  4. Verify `/ready` returns `{"status":"ready","database":"ok"}`.
- **Migration strategy:** `RUN_MIGRATIONS` can be set to `false` so migrations
  run as a separated one-shot step, avoiding concurrent upgrades during a
  restore. Back up the DB before any destructive migration.

---

## 3. Object storage backup (Backblaze B2)

- **Versioning:** enable bucket versioning so overwrites/deletes are recoverable
  for a defined window.
- **Lifecycle:** set a lifecycle rule to expire old non-current versions after a
  retention window (e.g. 30 days), balancing recovery vs cost.
- **Encrypted at rest:** objects stored by the worker are AES-256-GCM encrypted
  **when** `ENCRYPTION_MASTER_KEY` is set (`encryption.py`, `processor.py`).
  Backups are therefore ciphertext at the provider; keep the master key safe and
  off the backup path (see G1, R9).
- **Cross-region/object-lock (recommended):** for higher assurance, use an
  object-lock (compliance) mode and/or a second B2 bucket in a different region
  for disaster protection. *Not currently configured — gap.*

---

## 4. Redis (queue/SSE) handling — transient

- Redis holds the **conversion job queue (Streams)**, the **SSE status stream**,
  the **rate-limit counters**, and **upload-session caches**.
- Because it is transient:
  - **Job state is durable in Postgres** (`ConversionJobModel`); the worker
    persists status via `job_repository.update_conversion_job` (`processor.py`).
  - **SSE replay:** `redis_stream_status_queue.py` replays previously emitted
    events on reconnect; a Redis restart loses the stream, so a client reconnecting
    after a Redis loss may not receive historical progress. This is a recognised
    limitation (G5).
  - **Rate-limit state:** the in-memory fallback in `rate_limit.py` is per-process,
    so multi-worker environments lose cluster-wide limits when Redis is down
    (per-process limits still apply).
- **Recovery:** restart workers to re-consume the queue; optionally enable
  Upstash persistence/durability (`Redis` with AOF) to reduce event loss. Consider
  a **dead-letter queue** for messages that fail retries (`retry_on_exception` in
  `worker.py`).

---

## 5. Retention & cleanup worker behaviour

Configured via `settings.py` and executed by
`workers/cleanup_worker/worker.py`:

| Setting | Default | Meaning |
|---|---|---|
| `GUEST_JOB_RETENTION_HOURS` | 24 | Remove ownerless (`user_id IS NULL`) conversion jobs + their objects. |
| `GUEST_FILE_RETENTION_HOURS` | 24 | Remove guest files whose `expires_at` passed + objects. |
| `TEMP_FILE_RETENTION_HOURS` | 1 | Remove `temp/` prefix processing objects older than 1 h. |
| `JOB_ARCHIVE_AFTER_DAYS` | 30 | Delete job history (and objects) older than 30 days. |
| `CLEANUP_INTERVAL_SECONDS` | 21600 | Cleanup cycle every 6 h. |

The cleanup worker runs as a separate process (`worker-cleanup` in
`compose.yaml`) so it never competes with active conversions. Deletion is
**object-first, then row**, tolerant of missing keys (`_delete_objects`).

> **Retention & privacy (GDPR overlap):** these windows define how long PII/file
> content is retained. Guest data is auto-purged within 24 h; authenticated job
> history is purged after 30 days. **Gap:** no legal-hold mechanism or immutable
> deletion log — see `risk-assessment.md` R11 and `security-policy.md` §4.4.

---

## 6. RPO / RTO summary

| Component | RPO | RTO | Notes |
|---|---|---|---|
| Postgres | ≤ 5 min | ≤ 1 h | Neon PITR; run migrations on restore. |
| Object storage | ≤ 24 h (version window) | ≤ 2 h | B2 versioning/lifecycle; object-lock recommended. |
| Redis | Not guaranteed | Rebuild | Transient; re-queue from Postgres, re-emit events. |
| Full platform | — | ≤ 4 h (target) | Sum of restore steps + migration + verification. |

**Availability:** 3 converter replicas + `restart: unless-stopped`
(`compose.yaml`); `/health` and `/ready` probes for orchestration.

---

## 7. Disaster recovery runbook

> **Status:** this runbook is a template and must be **tested and signed off**
> before it can be presented as evidence (G6). Each step names the person/role
> and the concrete command/config.

### Phase 1 — Declare the disaster

- **Trigger:** data-store outage, corruption, compromise, or region loss.
- **Owner:** DevOps Engineer, **Notify:** CISO / InfoSec Lead + DPO (if PII/data
  events involved) + on-call.
- Record the incident per `incident-response-plan.md`.

### Phase 2 — Restore PostgreSQL (Neon)

1. Create a Neon branch/PITR restore at the last good point.
2. Update `DATABASE_URL` in `.env` (or managed secret store).
3. Restart the API stack; run `alembic upgrade head` (or `RUN_MIGRATIONS=true`).
4. Verify `/ready` returns `database: ok`.

### Phase 3 — Restore object storage (B2)

1. Use B2 versioning to restore deleted/overwritten objects (or failover bucket).
2. Confirm object keys are intact (respect `sanitize.py` naming).
3. Verify a sample download decrypts via the API when the master key is set.

### Phase 4 — Rebuild Redis

1. Restore Upstash Redis (or create a new instance and update `REDIS_URL`).
2. Restart workers; re-consume the job Stream from Postgres.
3. Re-emit SSE status for any in-flight jobs; accept that historical events are
   lost (G5).

### Phase 5 — Verify & return to service

- `/health` OK, `/ready` OK, `/metrics` scraper receiving data.
- A representative conversion succeeds end-to-end.
- Confirm audit logs are flowing (a `data_access` event on a test download).

### Phase 6 — Post-incident

- Export evidence, run lessons-learned, update the ISMS risk register.

---

## 8. Backup verification & testing

- **Restore test:** at least annually, perform a full restore to a **non-production**
  environment and prove RPO/RTO (record the actual timings).
- **Monitoring:** alert on backup/restore job status and on `/ready` failing.
- **Evidence:** keep the restore test results and DR runbook sign-off with this
  pack (the auditor will ask for a tested restore, not just a written plan).

---

## 9. Gaps / recommendations (auditor flags)

| Gap | Recommendation |
|---|---|
| G6 — no tested DR runbook | Execute & record a restore test; sign off RPO/RTO. |
| G5 — Redis transient, SSE replay lost | Enable Upstash persistence; add dead-letter queue; document replay limits. |
| No object-lock / cross-region backup | Enable B2 object-lock compliance mode & a second-region bucket. |
| No legal-hold / immutable deletion log | Add retention/legal-hold controls for evidence preservation. |
| `/metrics` unauthenticated | Restrict to internal/monitoring network (A.8.16). |
