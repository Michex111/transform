from pathlib import Path

import pytest


@pytest.fixture
def temp_dir(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def sample_input_file(tmp_path: Path) -> Path:
    path = tmp_path / "input.txt"
    path.write_text("hello world", encoding="utf-8")
    return path


@pytest.fixture
def sample_output_file(tmp_path: Path) -> Path:
    return tmp_path / "output.md"


@pytest.fixture
def sample_urls() -> dict[str, str]:
    return {
        "input": "s3-file_store/input.txt",
        "output": "s3-file_store/input.md",
    }