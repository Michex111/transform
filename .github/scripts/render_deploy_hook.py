from __future__ import annotations

import os
import re
import sys
from urllib.parse import parse_qs, urlparse

_RENDER_DEPLOY_PREFIX = "https://api.render.com/deploy/"

# Name used in operator-facing error messages. Overridable via
# DEPLOY_HOOK_NAME so the same validator can be reused for the SPA's deploy
# hook (RENDER_WEB_DEPLOY_HOOK) without confusing messages.
_SECRET_NAME = os.environ.get("DEPLOY_HOOK_NAME", "RENDER_PROD_DEPLOY_HOOK")


def _fail(*lines: str) -> int:
    for line in lines:
        print(line, file=sys.stderr)
    return 1


def normalize_deploy_hook(raw_value: str | None) -> str:
    if raw_value is None or not raw_value.strip():
        raise ValueError(
            f"::error::Secret {_SECRET_NAME} is not configured.\n"
            "Create a deploy hook in Render (Service -> Settings -> Deploy Hook) "
            "and add it as a repository secret."
        )

    hook = raw_value.strip()

    if re.search(r"\s", hook):
        raise ValueError(
            f"::error::{_SECRET_NAME} contains whitespace inside the value.\n"
            "Expected either the full Render URL or the fragment starting with 'srv-'."
        )

    if hook.startswith("https://"):
        normalized = hook
    elif hook.startswith("api.render.com/deploy/"):
        normalized = f"https://{hook}"
    elif hook.startswith("deploy/srv-"):
        normalized = f"https://api.render.com/{hook}"
    elif hook.startswith("srv-"):
        normalized = f"{_RENDER_DEPLOY_PREFIX}{hook}"
    elif hook.startswith("key=") or hook.startswith("?key="):
        raise ValueError(
            f"::error::{_SECRET_NAME} is missing the Render service id.\n"
            "Store either the full deploy hook URL or the fragment beginning with 'srv-'."
        )
    else:
        raise ValueError(
            f"::error::{_SECRET_NAME} must be a Render deploy hook URL or service fragment.\n"
            f"The stored value is {len(hook)} characters after trimming whitespace.\n"
            "Expected either 'https://api.render.com/deploy/srv-...?...' or 'srv-...?...'."
        )

    parsed = urlparse(normalized)
    if parsed.scheme != "https":
        raise ValueError(
            f"::error::{_SECRET_NAME} must use https://.\n"
            "Expected the complete URL from Render -> Service -> Settings -> Deploy Hook."
        )
    if parsed.netloc != "api.render.com" or not parsed.path.startswith("/deploy/srv-"):
        raise ValueError(
            f"::error::{_SECRET_NAME} does not look like a Render deploy hook.\n"
            "Expected it to target https://api.render.com/deploy/srv-..."
        )

    key_values = parse_qs(parsed.query, keep_blank_values=True).get("key", [])
    if not key_values or not key_values[0]:
        raise ValueError(
            f"::error::{_SECRET_NAME} is missing the required 'key' query parameter.\n"
            "Copy the deploy hook directly from Render -> Service -> Settings -> Deploy Hook."
        )

    return normalized


def main() -> int:
    try:
        print(normalize_deploy_hook(os.environ.get("DEPLOY_HOOK")))
    except ValueError as exc:
        return _fail(*str(exc).splitlines())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
