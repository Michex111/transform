"""What kind of work an execution represents.

The distinction is a security and reliability boundary, not a label: a
``SCRIPT`` is source code written to a file and run with ``uv run``, a
``COMMAND`` is an argv list executed directly, and a ``PROJECT`` is a bounded
``uv`` project operation (``uv sync``, ``uv add``, ``uv lock``). The API
validates each mode differently — a project operation is checked against a
closed allow-list — so the mode must be explicit in the request rather than
inferred from the payload.
"""

from enum import StrEnum


class ExecutionMode(StrEnum):
    SCRIPT = "SCRIPT"
    COMMAND = "COMMAND"
    PROJECT = "PROJECT"
