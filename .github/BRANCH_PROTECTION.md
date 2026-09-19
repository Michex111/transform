# Branch Protection Setup

Branch protection **cannot be configured from the repository** — it is a
GitHub server-side setting.

> **Status: ACTIVE on `main` and `staging`.** Applied 2026-09-17. The commands
> below double as the reference for re-applying or changing it.

## Prerequisites

Authenticate the GitHub CLI (needs a token with `repo` scope, and admin rights
on the repository):

```bash
gh auth login
```

Verify:

```bash
gh auth status
```

## Branch strategy

Four branches. Each promotion is a PR — never a direct push:

```
dev ──▶ staging ──▶ main
(implementation)  (testing)   (FINAL PRODUCTION)
design ──┘  (design-system work, merged into main)
```

| Branch    | Purpose                     | Protected | Deploys to  |
| --------- | --------------------------- | --------- | ----------- |
| `dev`     | Implementation              | No        | —           |
| `staging` | Testing / pre-production    | Yes       | —           |
| `design`  | Design-system work          | No        | —           |
| `main`    | **Final production**        | Yes       | production  |

`main` holds only releases that have passed `staging`. A push to `main`
triggers the `Deploy` workflow, which re-runs the full test suite before
deploying.

## Apply protection

Run the script below (or copy the commands). Replace `OWNER/REPO` if the remote
changes.

```bash
REPO="Michex111/transform"

# ---- main (final production): strictest protection ---------------------------
# Require PRs, passing CI, and no force-pushes/deletions.
#
# NOTE: required_approving_review_count is 0 on purpose. GitHub forbids
# approving your own PR, so a count of 1 would make main permanently
# unmergeable for a solo maintainer. Raise it only if a second reviewer exists.
gh api -X PUT "repos/$REPO/branches/main/protection" \
  --input - <<'JSON'
{
  "required_status_checks": {
    "strict": true,
    "contexts": ["Backend (pytest)", "Frontend (lint, test, build)"]
  },
  "enforce_admins": false,
  "required_pull_request_reviews": {
    "dismiss_stale_reviews": true,
    "require_code_owner_reviews": false,
    "required_approving_review_count": 0
  },
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "required_conversation_resolution": true
}
JSON

# ---- staging: same checks, but allow the maintainer to self-merge faster ----
gh api -X PUT "repos/$REPO/branches/staging/protection" \
  --input - <<'JSON'
{
  "required_status_checks": {
    "strict": true,
    "contexts": ["Backend (pytest)", "Frontend (lint, test, build)"]
  },
  "enforce_admins": false,
  "required_pull_request_reviews": {
    "dismiss_stale_reviews": true,
    "required_approving_review_count": 0
  },
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false
}
JSON
```

## Required GitHub secrets

The `Deploy` workflow reads `RENDER_PROD_DEPLOY_HOOK`. **Without it the deploy
job fails closed** (`::error::Secret RENDER_PROD_DEPLOY_HOOK is not configured`)
rather than silently passing — so a missing secret can never be mistaken for a
successful deploy.

### 1. Get the deploy hook URL

Render Dashboard → **transform-api** → **Settings** → **Deploy Hook** → copy the
URL. It looks like:

```
https://api.render.com/deploy/srv-xxxxxxxxxxxx?key=yyyyyyyyyyyy
```

The deploy workflow also accepts the trimmed `srv-xxxxxxxxxxxx?key=yyyyyyyyyyyy`
fragment, but storing the full URL is still recommended.

### 2. Add it as a repository secret

Interactive (the value is never echoed):

```bash
gh secret set RENDER_PROD_DEPLOY_HOOK --repo Michex111/transform
```

Or via UI: **Settings → Secrets and variables → Actions → New repository
secret**, name it exactly `RENDER_PROD_DEPLOY_HOOK`.

### 3. Verify

```bash
gh api repos/Michex111/transform/actions/secrets --jq '.secrets[].name'
# expected: RENDER_PROD_DEPLOY_HOOK
```

| Secret                    | Used by               | Where to get it                           |
| ------------------------- | --------------------- | ----------------------------------------- |
| `RENDER_PROD_DEPLOY_HOOK` | production deploy job | Render → Service → Settings → Deploy Hook |

Optional repository **variable** (Settings → Variables) used for the
environment link displayed on deployments:

```bash
gh variable set PRODUCTION_APP_URL --repo Michex111/transform \
  --body "https://transform-api-7b3g.onrender.com"
```

| Variable             | Example                                   |
| -------------------- | ----------------------------------------- |
| `PRODUCTION_APP_URL` | `https://transform-api-7b3g.onrender.com` |

## GitHub environments

Both environments exist but currently have **0 protection rules**, so deploys
run without a manual approval gate.

| Environment  | Used by                  |
| ------------ | ------------------------ |
| `production` | `deploy.yml` prod job    |
| `staging`    | reserved                 |

To require a manual approval before each production deploy, add yourself as a
required reviewer in the GitHub UI: **Settings → Environments → production →
Required reviewers**.

Unlike PR reviews, a required environment reviewer **may approve their own**
deployment, so this does not deadlock a solo maintainer.

## Verify protection

```bash
for BRANCH in main staging; do
  echo "== $BRANCH =="
  gh api "repos/$REPO/branches/$BRANCH/protection" \
    --jq '{required_checks: .required_status_checks.contexts, reviews: .required_pull_request_reviews.required_approving_review_count, force_push: .allow_force_pushes.enabled, deletions: .allow_deletions.enabled}'
done
```

## Note on repository visibility

This repository is currently **public**. Branch protection on a public repo
prevents accidental force-pushes and unreviewed changes, but it provides **no
confidentiality** — all branches and history are world-readable. Do not commit
secrets to any branch.
