# Branch Protection Setup

Branch protection **cannot be configured from the repository** — it is a
GitHub server-side setting. The rules below are not applied automatically; use
the commands/checklist in this file to apply them once.

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

Promotion order (each step is a PR, never a direct push):

```
dev-branch ──▶ staging-branch ──▶ main ──▶ master ──▶ deploy
   (dev)          (staging)      (FINAL     (approved    (triggers
                               PRODUCTION)   staging)    deployment)
```

**`main` is the FINAL PRODUCTION branch.** It is not a development trunk: only
releases that have passed staging and owner approval are merged into it.

| Branch           | Purpose                          | Protected | Deploys to  |
| ---------------- | -------------------------------- | --------- | ----------- |
| `dev-branch`     | Active development               | No        | —           |
| `staging-branch` | Pre-production / staging         | Yes       | —           |
| `main`           | **Final production**             | Yes       | —           |
| `master`         | Approved-staging gate            | Yes       | staging     |
| `deploy`         | Deployment trigger               | Yes       | production  |

## Apply protection

Run the script below (or copy the commands). Replace `OWNER/REPO` if the remote
changes.

```bash
REPO="Michex111/transform"

# ---- Strictly protected: main, master, deploy -------------------------------
# Require PRs, passing CI, an approving review, and no force-pushes/deletions.
for BRANCH in main master deploy; do
  echo "Protecting $BRANCH..."
  gh api -X PUT "repos/$REPO/branches/$BRANCH/protection" \
    --input - <<'JSON'
{
  "required_status_checks": {
    "strict": true,
    "contexts": ["Backend (pytest)", "Frontend (lint, test, build)"]
  },
  "enforce_admins": true,
  "required_pull_request_reviews": {
    "dismiss_stale_reviews": true,
    "require_code_owner_reviews": false,
    "required_approving_review_count": 1
  },
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "required_conversation_resolution": true
}
JSON
done

# ---- staging-branch: same, but allow the maintainer to self-merge faster ----
gh api -X PUT "repos/$REPO/branches/staging-branch/protection" \
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

The `Deploy` workflow reads these. Add them under
**Settings → Secrets and variables → Actions**.

| Secret                       | Used by                | Where to get it                          |
| ---------------------------- | ---------------------- | ---------------------------------------- |
| `RENDER_STAGING_DEPLOY_HOOK` | staging deploy job     | Render → Service → Settings → Deploy Hook |
| `RENDER_PROD_DEPLOY_HOOK`    | production deploy job  | Render → Service → Settings → Deploy Hook |

Optional repository **variables** (Settings → Variables) used for the
environment links displayed on deployments:

| Variable             | Example                          |
| -------------------- | -------------------------------- |
| `STAGING_APP_URL`    | `https://transform-staging.onrender.com` |
| `PRODUCTION_APP_URL` | `https://app.example.com`        |

## Verify protection

```bash
for BRANCH in main master deploy staging-branch; do
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
