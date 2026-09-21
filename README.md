# Transform - File Converter Backend

A production-ready file conversion SaaS platform built with FastAPI, featuring real-time progress tracking, subscription-based billing, secure file storage, and a distributed worker architecture.

## Architecture

```
┌─────────┐     ┌──────────┐     ┌────────────┐     ┌───────────────┐
│ Client  │────▶│  Nginx   │────▶│  FastAPI   │────▶│  PostgreSQL   │
│ (Web)   │     │  (Proxy) │     │  (API)     │     │  (Jobs/Users) │
└─────────┘     └──────────┘     └─────┬──────┘     └───────────────┘
                                       │
                                ┌──────▼──────┐     ┌───────────────┐
                                │    Redis    │◀────│   Workers     │
                                │  (Streams)  │     │  (Converter)  │
                                └──────┬──────┘     └───────┬───────┘
                                       │                    │
                                ┌──────▼──────┐     ┌───────▼───────┐
                                │     SSE     │     │  S3 / Minio   │
                                │  (Events)   │     │  (Storage)    │
                                └─────────────┘     └───────────────┘
```

### Service Topology

The API and the web UI are **separate deployables** that communicate
cross-origin:

| Service | What it is | Serves |
|---------|------------|--------|
| `transform-api` | FastAPI backend (Docker, `deployment/docker/worker.Dockerfile`) | `/api/*`, `/health`, `/ready`, `/metrics`, `/docs`, `/redoc`, `/openapi.json` |
| `transform-web` | React SPA (static site, `web/`) | the UI, with a client-side-routing fallback to `index.html` |

The API deliberately does **not** serve the SPA — `/` and any unknown path
return a JSON `404`, never an HTML application shell. Two consequences:

- The API must allow-list the SPA origin in `ALLOWED_ORIGINS` (CORS) and in
  `S3_CORS_ALLOWED_ORIGINS` (the browser uploads directly to object storage
  with a presigned `PUT`).
- The SPA learns the API origin at **build** time from `VITE_API_BASE_URL`
  (Vite inlines `VITE_*` variables, so it must be set when `npm run build` runs).

Locally the two run same-origin through the Vite dev-server proxy, so no CORS
setup is needed for development.

### Tech Stack
- **API Framework**: FastAPI (async Python)
- **Frontend**: React 18 + TypeScript + Vite SPA (`web/`), deployed as a separate static site
- **Database**: PostgreSQL 15 with SQLAlchemy 2.0 (async)
- **Queue/Events**: Redis Streams with consumer groups
- **Storage**: S3-compatible (Minio local, Backblaze B2/AWS S3 production)
- **Auth**: JWT (access + refresh tokens), API keys, bcrypt passwords
- **Payments**: Stripe (subscriptions, credit purchases)
- **Workers**: Async Python workers with retry and backoff

## Features

### File Conversion
- **Documents**: PDF ↔ DOCX, DOCX ↔ HTML, PDF ↔ HTML, XLSX ↔ CSV
- **Audio**: MP3 ↔ WAV, WAV ↔ FLAC, MP3 ↔ OGG, MP3 ↔ M4A
- **Video**: MP4 ↔ AVI, MP4 ↔ MOV, AVI ↔ MKV, Video → GIF
- **Images**: JPEG ↔ PNG, PNG ↔ WEBP, SVG → PNG
- **PDF to image**: PDF → PNG, JPG, JPEG, WEBP, BMP (one image per page; a
  multi-page PDF is bundled into a ZIP), PDF → TIFF and PDF → GIF (all pages in
  the single multi-page file)
- **Image to PDF**: JPG, JPEG, PNG, WEBP, GIF, BMP, TIFF, ICO and AVIF → PDF,
  plus SVG → PDF (rendered as vectors, not rasterised). Each page keeps the
  image's pixel size, scaled by the file's own DPI when it has one (otherwise
  1 pixel = 1 point); an animated GIF or multi-page TIFF becomes one PDF page
  per frame.
- **Ebooks**: EPUB ↔ PDF, EPUB ↔ MOBI, EPUB → TXT
- **Archives**: ZIP ↔ TAR

### User Management
- Guest access (rate-limited, 50MB limit)
- Free tier (5GB storage, 50 conversions/month)
- Pro tier (50GB, 500 conversions, Stripe subscription)
- Pro Plus tier (100GB, 2000 conversions)
- Enterprise tier (custom limits)
- API key authentication

### File Library
- **Folders**: signed-in users can create nested folders, rename them, move
  files between folders, and delete folders recursively (files and objects).
- Files are listed per folder (`?folder_id=`), and uploads can target a folder
  directly via `folder_id` on the upload-session request.

### Real-time Progress
- Server-Sent Events (SSE) for conversion progress
- Redis Streams for event distribution
- Progress stages: downloading → converting → uploading → complete

### Security
- **Encryption at rest** (opt-in via `ENCRYPTION_MASTER_KEY`): files in object
  storage are always ciphertext (chunked AES-256-GCM, per-user keys derived
  via HKDF). The worker decrypts inputs before converting and re-encrypts
  outputs; downloads are streamed decrypted through the API. Without the key,
  everything runs in plaintext for minimal overhead.
- JWT authentication with refresh tokens
- API key hashing with SHA-256
- **Rate limiting**: Redis-backed sliding window per IP (in-memory fallback),
  stricter limits on auth endpoints, and per-API-key limits for
  `X-API-Key` traffic
- **Security headers** (X-Content-Type-Options, X-Frame-Options, …)
- **Boot-time validation**: in `ENVIRONMENT=production` the app refuses to
  start with a weak `SECRET_KEY`, wildcard CORS, or plaintext object storage
- **Tier-based upload size limits** enforced at upload verification (413)
- CORS configuration
- Input validation and sanitization

## Quick Start

### Prerequisites
- Python 3.14+
- Docker & Docker Compose (for local development)

### Local Development (with Docker)

```bash
# Clone the repository
git clone <repo-url>
cd "File Converter"

# Copy environment config
cp .env.example .env

# Start all services
docker compose -f deployment/docker/compose.yaml up -d

# The API is available at http://localhost:8000
# Swagger docs at http://localhost:8000/docs
```

### Local Development (without Docker)

```bash
# Create virtual environment
python -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install uv
uv sync

# Install system dependencies (Ubuntu/Debian)
sudo apt-get install -y libreoffice ffmpeg calibre libcairo2

# Start PostgreSQL and Redis (via Docker)
docker compose -f deployment/docker/compose.yaml up -d postgres redis minio

# Create the Alembic config (point it at your local database)
cp alembic.ini.example alembic.ini

# Run database migrations
alembic upgrade head

# Start the API
uv run uvicorn src.presentation.api.main:app --reload

# In another terminal, start the worker
uv run python -m workers.converter_workers.main

# Optionally, start the cleanup worker (guest data retention)
uv run python -m workers.cleanup_worker.main
```

### Local Development (frontend)

The SPA lives in `web/`. In development it runs on the Vite dev server and
proxies `/api` to the backend, so everything stays same-origin:

```bash
cd web
npm install
npm run dev        # http://localhost:5173  (proxies /api -> http://localhost:8000)
```

Other useful scripts: `npm run lint`, `npm test` (Vitest), `npm run build`
(production bundle in `web/dist`). The backend keeps its default localhost
origins in `ALLOWED_ORIGINS` so this works with no extra CORS configuration.

## Cleanup Worker

The cleanup worker runs as an **independent process** from the converter worker
and performs scheduled maintenance on guest data:

| Task | What it removes | Default |
|------|-----------------|---------|
| Guest conversion jobs | Ownerless jobs (`user_id IS NULL`) older than the window, plus their input/output objects | 24 h |
| Expired guest files | `user_files` rows whose `expires_at` has passed, plus their objects | 24 h |
| Temp objects | Objects under the `temp/` prefix older than the window | 1 h |
| Job history | Conversion records older than the archive window, releasing their objects | 30 d |

Retention windows are configurable via `CLEANUP_INTERVAL_SECONDS`,
`GUEST_JOB_RETENTION_HOURS`, `GUEST_FILE_RETENTION_HOURS`,
`TEMP_FILE_RETENTION_HOURS` and `JOB_ARCHIVE_AFTER_DAYS`.

## API Documentation

Full OpenAPI documentation is available at `/docs` when the server is running.

### Key Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/users/register` | Register new user |
| POST | `/api/users/token` | Login (get JWT + refresh token) |
| POST | `/api/users/refresh` | Exchange refresh token for a new access token |
| GET | `/api/users/me` | Get current user |
| GET | `/api/conversions/supported` | List supported conversions |
| POST | `/api/conversions/jobs` | Submit conversion job (returns upload URL) |
| GET | `/api/conversions/jobs/{id}` | Get job status |
| GET | `/api/conversions/jobs/{id}/download` | Stream decrypted output (encryption enabled) |
| POST | `/api/uploads/sessions` | Create upload session |
| POST | `/api/uploads/sessions/{id}/verify?job_id=` | Verify upload and enqueue job |
| GET | `/api/v1/files` | List files (filter by `folder_id` query param) |
| POST | `/api/v1/files/folders` | Create a folder (nest with `parent_id`) |
| GET | `/api/v1/files/folders` | List root-level folders |
| GET | `/api/v1/files/folders/{id}` | Folder contents (subfolders + files) |
| PATCH | `/api/v1/files/folders/{id}` | Rename a folder |
| DELETE | `/api/v1/files/folders/{id}` | Delete folder recursively (files + objects) |
| POST | `/api/v1/files/{id}/move` | Move a file into a folder (or root) |
| GET | `/api/v1/files/{id}/download` | Get download URL (or stream endpoint when encrypted) |
| GET | `/api/v1/files/{id}/stream` | Stream decrypted file content (encryption enabled) |
| GET | `/api/v1/events/jobs/{id}` | SSE stream of real-time job progress |
| GET | `/api/v1/subscription/plans` | List plans |
| POST | `/api/v1/subscription/checkout` | Create Stripe checkout session |
| GET | `/api/v1/subscription/status` | Current subscription status |
| GET | `/api/v1/credits/balance` | Credit balance |
| GET | `/api/v1/credits/history` | Credit transaction history |
| POST | `/api/v1/credits/purchase` | Purchase credits (Stripe) |
| POST | `/api/v1/api-keys` | Generate API key (hashed in DB) |
| GET | `/api/v1/user/dashboard` | Real usage dashboard |
| POST | `/api/v1/webhooks/stripe` | Stripe webhook (subscriptions) |
| GET | `/metrics` | Prometheus metrics |
| GET | `/health`, `/ready` | Liveness / readiness probes |

### Authentication

- **JWT**: send `Authorization: Bearer <token>` (obtained from `/api/users/token`).
- **API keys**: send `X-API-Key: tr_<key>` (obtained from `POST /api/v1/api-keys`).

## Configuration

All configuration is via environment variables (see `.env.example`):

| Variable | Required | Description |
|----------|----------|-------------|
| `ENVIRONMENT` | No | `development` (default) or `production` — production enforces safety checks at boot |
| `SECRET_KEY` | Yes | JWT signing secret (>=32 chars, non-default in production) |
| `DATABASE_URL` | Yes | PostgreSQL connection string |
| `REDIS_URL` | Yes | Redis connection string |
| `BACKBLAZE_*` | Yes | S3-compatible storage credentials |
| `BACKBLAZE_USE_SSL` | No | `true` for HTTPS endpoints (AWS S3/B2), `false` for local Minio (required `true` in production) |
| `RUN_MIGRATIONS` | No | Run migrations at startup (default `true`); disable for one-shot migration deployments |
| `STRIPE_SECRET_KEY` | No | Stripe API key for payments |
| `STRIPE_WEBHOOK_SECRET` | No | Stripe webhook signing secret |
| `STRIPE_PRICE_*` | No | Stripe price IDs for subscription checkout |
| `STRIPE_SUCCESS_URL` / `STRIPE_CANCEL_URL` / `STRIPE_CREDIT_*` / `STRIPE_PORTAL_RETURN_URL` | No | Where Stripe sends the user back after Checkout / Customer Portal. **Must point at the SPA origin** in production |
| `ALLOWED_ORIGINS` | Yes in production | JSON list of browser origins allowed to call the API. Must include the SPA origin. Wildcard `"*"` is refused at boot in production |
| `S3_CORS_ALLOWED_ORIGINS` | Yes when uploading from a browser | JSON list of origins allowed to `PUT`/`GET` directly against object storage. Must include the SPA origin |
| `FRONTEND_DIST_DIR` | No | **Deprecated / no-op.** The API no longer serves the SPA; the value is never read. Retained only so an environment that still sets it does not fail boot |
| `ENCRYPTION_MASTER_KEY` | No | Fernet key for file encryption |

### Frontend Configuration (`web/`)

The SPA is configured at **build** time (see `web/.env.example`):

| Variable | Description |
|----------|-------------|
| `VITE_API_BASE_URL` | Base URL of the API **including the `/api` prefix**. Defaults to `/api` (local dev via the Vite proxy). For production use the API origin, e.g. `https://transform-api-7b3g.onrender.com/api` |

`web/.env.development` sets `/api` (proxied to `localhost:8000`).
`web/.env.production` carries the deployed API origin as a fallback; the static
host's build environment variable always wins over it.

### Production Checklist

- Set `ENVIRONMENT=production`, a strong `SECRET_KEY` (>=32 random chars), and
  explicit `ALLOWED_ORIGINS` (no `*`) — the app refuses to boot otherwise.
- **Add the SPA origin** (`https://transform-web.onrender.com`) to both
  `ALLOWED_ORIGINS` and `S3_CORS_ALLOWED_ORIGINS`, otherwise every browser call
  from the deployed UI fails the CORS preflight (and direct uploads are blocked).
- Point all `STRIPE_*_URL` settings at the SPA origin, e.g.
  `https://transform-web.onrender.com/app/billing?checkout=success`.
- Set `BACKBLAZE_USE_SSL=true` with an HTTPS storage endpoint.
- Run migrations as a one-shot step (`RUN_MIGRATIONS=false` on replicas), or
  rely on the startup migration with a single API replica.
- Put the API behind Nginx/TLS; failed conversion jobs are copied to the
  `conversion_jobs:dead` Redis stream for replay/inspection.

## Project Structure

```
src/
├── domain/              # Business logic & entities
│   ├── conversions/     # Conversion job entities
│   ├── security/        # API key entities
│   └── subscriptions/   # Subscription & credit entities
├── application/         # Use cases & ports
│   ├── dtos/           # Data transfer objects
│   ├── ports/          # Interface definitions
│   └── services/       # Application services
├── infrastructure/      # External integrations
│   ├── adapters/       # Queue, storage, payment, security
│   ├── auth/           # JWT & password hashing
│   ├── config/         # Settings
│   ├── converters/     # Converter registry & functions
│   └── database/       # ORM models & migrations
├── presentation/        # API layer
│   ├── api/            # FastAPI routers & middleware
│   └── schemas/        # Pydantic request/response models
workers/
├── converter_workers/   # Async worker, processor, dependencies
└── cleanup_worker/      # Guest-data cleanup worker (separate process)
web/                     # React SPA (React 18 + TypeScript + Vite + Tailwind v4)
├── src/                 # App source (api client, pages, components)
├── .env.example         # VITE_API_BASE_URL documentation
└── vite.config.ts       # Dev proxy: /api -> http://localhost:8000
deployment/
├── docker/              # Dockerfile (API + workers) and compose stack
└── ...
render.yaml              # Render Blueprint: transform-api + transform-web
.github/workflows/       # ci.yml (quality gates) + deploy.yml (both services)
```

## Testing

```bash
# Run all tests
uv run pytest

# Run with coverage
uv run pytest --cov=src --cov-report=term-missing

# Run specific test file
uv run pytest tests/unit/domain/test_subscription.py -v
```

### Frontend tests

```bash
cd web
npm run lint          # ESLint
npm test              # Vitest
npm run build         # tsc -b && vite build
```

## Deployment

### Production Considerations
- Use AWS S3 or Backblaze B2 instead of Minio
- Configure a real PostgreSQL instance (RDS, Cloud SQL, etc.)
- Set up Redis with persistence (AOF)
- Use a reverse proxy (Nginx, Caddy) with HTTPS
- Set strong `SECRET_KEY` and `ENCRYPTION_MASTER_KEY`
- Configure proper CORS origins
- Set up monitoring (Prometheus + Grafana, Sentry)

### Render — two services

Production runs two Render services in the `oregon` region, both on the `main`
branch with **Auto-Deploy OFF** (deploys go through CI/CD, never on push):

| Service | Type | Config |
|---------|------|--------|
| `transform-api` | Web service (Docker, free) | Dockerfile `deployment/docker/worker.Dockerfile`, context `.`, command `uv run uvicorn src.presentation.api.main:app --host 0.0.0.0 --port $PORT` |
| `transform-web` | Static site (free) | root dir `web`, build `npm ci && npm run build`, publish `dist`, rewrite `/*` → `/index.html` |

Both are declared in `render.yaml` (Render Blueprint) so the infrastructure is
reproducible. **Review the Blueprint sync diff in the dashboard before
applying**: the API service already exists and is adopted by name.

Ambient URLs:

- API: `https://transform-api-7b3g.onrender.com`
- SPA: `https://transform-web.onrender.com`

#### Environment variables

`transform-api` (dashboard-managed secrets; see `.env.example`):

| Variable | Value for production |
|----------|----------------------|
| `ENVIRONMENT` | `production` |
| `ALLOWED_ORIGINS` | `["https://transform-web.onrender.com"]` (plus any localhost origin you still need) |
| `S3_CORS_ALLOWED_ORIGINS` | `["https://transform-web.onrender.com"]` |
| `STRIPE_SUCCESS_URL` | `https://transform-web.onrender.com/app/billing?checkout=success` |
| `STRIPE_CANCEL_URL` | `https://transform-web.onrender.com/app/billing?checkout=cancelled` |
| `STRIPE_CREDIT_SUCCESS_URL` | `https://transform-web.onrender.com/app/billing?credits=success` |
| `STRIPE_CREDIT_CANCEL_URL` | `https://transform-web.onrender.com/app/billing?credits=cancelled` |
| `STRIPE_PORTAL_RETURN_URL` | `https://transform-web.onrender.com/app/billing` |

> Do **not** set `FRONTEND_DIST_DIR`; it is a deprecated no-op. Also note that
> `Settings` uses `extra="forbid"`, so a mistyped env var crashes the boot.

`transform-web` (build-time):

| Variable | Value |
|----------|-------|
| `VITE_API_BASE_URL` | `https://transform-api-7b3g.onrender.com/api` |

#### Deploy & rollback

Deploys are driven by `.github/workflows/deploy.yml`:

1. `verify` re-runs `ci.yml` (`Backend (pytest)` + `Frontend (lint, test, build)`)
   on the exact commit — a deploy cannot proceed if tests fail.
2. `deploy-api` POSTs the `transform-api` deploy hook.
3. `deploy-web` POSTs the `transform-web` deploy hook.

Each deploy job requires its own repository/environment secret:

| Secret | Service |
|--------|---------|
| `RENDER_PROD_DEPLOY_HOOK` | `transform-api` |
| `RENDER_WEB_DEPLOY_HOOK` | `transform-web` |

Create each hook in Render → service → **Settings → Deploy Hook**, and store the
full URL. A missing or malformed hook fails the job loudly (it is never silently
skipped).

Rollback: in the Render dashboard open the service → **Events** → pick the last
known-good deploy → **Rollback**. (Free static sites and web services both
retain deploy history.) Reverting the commit on `main` and letting CI/CD run is
equally valid, and keeps the repository the source of truth.

### Kubernetes Deployment
```bash
# The docker images can be deployed to any Kubernetes cluster
# Use ConfigMaps for configuration and Secrets for sensitive data
# Horizontal Pod Autoscaling is recommended for workers
```

## License

BSD 3-Clause License - see LICENSE file.
