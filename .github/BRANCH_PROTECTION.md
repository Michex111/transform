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

Three branches, three stages. Each promotion is a PR — never a direct push:

```
dev ──▶ staging ──▶ main
(implementation)  (testing)   (FINAL PRODUCTION)
```

| Branch    | Purpose                  | Protected | Deploys to  |
| --------- | ------------------------ | --------- | ----------- |
| `dev`     | Implementation           | No        | —           |
| `staging` | Testing / pre-production | Yes       | —           |
| `main`    | **Final production**     | Yes       | production  |

`main` holds only releases that have passed `staging`. A push to `main`
triggers the `Deploy` workflow, which re-runs the full test suite before
deploying.

## Apply protection

Run the script below (or copy the commands). Replace `OWNER/REPO` if the remote
changes.

```bash
REPO="Michex111/transform"

# ---- main (final production): strictest protection ---------------------------
# Require PRs, passing CI, an approving review, and no force-pushes/deletions.
gh api -X PUT "repos/$REPO/branches/main/protection" \
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

The `Deploy` workflow reads this. Add it under
**Settings → Secrets and variables → Actions**.

| Secret                    | Used by               | Where to get it                           |
| ------------------------- | --------------------- | ----------------------------------------- |
| `RENDER_PROD_DEPLOY_HOOK` | production deploy job | Render → Service → Settings → Deploy Hook |

Optional repository **variable** (Settings → Variables) used for the
environment link displayed on deployments:

| Variable             | Example                   |
| -------------------- | ------------------------- |
| `PRODUCTION_APP_URL` | `https://app.example.com` |

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
