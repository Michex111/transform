from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / ".github" / "scripts" / "render_deploy_hook.py"


def run_script(value: str | None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    if value is None:
        env.pop("DEPLOY_HOOK", None)
    else:
        env["DEPLOY_HOOK"] = value

    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def test_accepts_full_render_url() -> None:
    result = run_script("https://api.render.com/deploy/srv-prod123?key=prod456")

    assert result.returncode == 0
    assert result.stdout.strip() == "https://api.render.com/deploy/srv-prod123?key=prod456"
    assert result.stderr == ""


def test_expands_service_fragment_to_full_url() -> None:
    result = run_script("  srv-prod123?key=prod456  \n")

    assert result.returncode == 0
    assert result.stdout.strip() == "https://api.render.com/deploy/srv-prod123?key=prod456"
    assert result.stderr == ""


def test_rejects_query_parameter_without_service_id() -> None:
    result = run_script("key=prod456")

    assert result.returncode == 1
    assert "missing the Render service id" in result.stderr
