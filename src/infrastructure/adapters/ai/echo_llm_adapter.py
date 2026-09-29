"""Offline, deterministic LM backend ("echo") for the AI assistant.

WHY this exists: the assistant must be usable — and *testable* — with no API
key, no network and no cost. This adapter answers from a small rule engine
instead of a model, so:

* a developer can click through the whole flow (list files → pick a format →
  start a conversion → see the result) on a fresh clone;
* the test suite can assert exact behaviour, because the same conversation and
  the same input always produce byte-identical output;
* no request ever leaves the process.

It is honest about what it is: when it cannot match a rule it says so and lists
what it *can* do, rather than inventing an answer. It never invents a file name
either — every file it mentions came back from a tool result.

Termination is structural: at most ``_MAX_STEPS`` tool requests are emitted for
one turn, and any tool result without a matching follow-up rule produces a final
answer immediately.
"""

import json
import re
from collections.abc import AsyncIterator, Sequence
from typing import Any

from src.application.ports.llm_port import (
    LlmMessage,
    LlmResponse,
    LlmStreamChunk,
    LlmToolCall,
    LlmToolSpec,
)
from src.infrastructure.converters.conversion_map import build_conversion_map

#: Tool requests this backend will emit within a single turn.
#:
#: Two is enough for the demo path (``list_files`` to resolve a name, then the
#: action) and small enough that a bug in the rule engine cannot loop.
_MAX_STEPS = 2

#: Fixed ids: deterministic output is the whole point, so no uuid4 here.
_CALL_ID = "call_echo"

_CONVERSION_VERBS = re.compile(
    r"\b(convert|converting|turn|change|export|transform|make)\b", re.IGNORECASE
)
_SUMMARY_WORDS = re.compile(
    r"\b(summar\w*|tldr|tl;dr|key points?|what(?:'s| is) (?:this|it|that) about|explain)\b",
    re.IGNORECASE,
)
_FORMAT_WORDS = re.compile(
    r"\b(formats?|convert(?:ing)? (?:it |this |that )?to what|what can i (?:convert|turn))\b",
    re.IGNORECASE,
)
_LIST_WORDS = re.compile(
    r"\b(my files?|my documents?|what files|which files|list (?:my |the )?files|show (?:my )?files|"
    r"do i have)\b",
    re.IGNORECASE,
)
_FOLDER_WORDS = re.compile(r"\b(folders?|director\w+)\b", re.IGNORECASE)
#: ``to pdf`` / ``into .docx`` — the trailing format of a conversion request.
_TARGET_FORMAT = re.compile(r"\b(?:to|into)\s+\.?([a-z][a-z0-9]{1,5})\b", re.IGNORECASE)
#: A file the user named explicitly: a quoted name, ``report.pdf``, or
#: ``file named report``.
_QUOTED = re.compile(r"[\"'\u201c\u2018]([^\"'\u201d\u2019]{2,120})[\"'\u201d\u2019]")
_WITH_EXTENSION = re.compile(r"\b([\w][\w .\-]{0,80}?\.[a-z0-9]{1,6})\b", re.IGNORECASE)
_NAMED = re.compile(
    r"\b(?:file|document|folder)\s+(?:called|named)\s+([\w][\w .\-]{0,60})", re.IGNORECASE
)

_OFFLINE_NOTICE = (
    "I'm running in offline demo mode, so I can't reason about that freely — "
    "but I can still help with the basics: ask me what files you have, what a "
    "file can be converted to, to summarise a document, or to start a conversion "
    '(for example "convert report.pdf to docx").'
)


def _last(messages: Sequence[LlmMessage], role: str) -> LlmMessage | None:
    for message in reversed(messages):
        if message.role == role:
            return message
    return None


def _loads(content: str) -> dict[str, Any]:
    try:
        parsed: Any = json.loads(content)
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _already_called(messages: Sequence[LlmMessage], name: str) -> bool:
    return any(
        call.name == name
        for message in messages
        if message.role == "assistant"
        for call in message.tool_calls
    )


def _named_file(text: str) -> str | None:
    """A file name the user explicitly mentioned, if any."""
    for pattern in (_QUOTED, _NAMED, _WITH_EXTENSION):
        match = pattern.search(text)
        if match:
            candidate = match.group(1).strip()
            if candidate:
                return candidate
    return None


def _target_format(text: str) -> str | None:
    match = _TARGET_FORMAT.search(text)
    return match.group(1).lower() if match else None


class EchoLlmAdapter:
    """Deterministic, network-free stand-in for a chat model."""

    @property
    def model(self) -> str:
        return "echo"

    async def stream(
        self,
        *,
        messages: Sequence[LlmMessage],
        tools: Sequence[LlmToolSpec],
    ) -> AsyncIterator[LlmStreamChunk]:
        """Answer from the rule engine, or ask for one more tool call."""
        available = {tool.name for tool in tools}
        call = self._decide(messages, available)
        if call is not None:
            yield LlmStreamChunk(
                kind="done",
                response=LlmResponse(
                    content="", tool_calls=(call,), finish_reason="tool_calls"
                ),
            )
            return
        text = self._compose_answer(messages)
        yield LlmStreamChunk(kind="text", text=text)
        yield LlmStreamChunk(kind="done", response=LlmResponse(content=text))

    # ------------------------------------------------------------------
    # Decision
    # ------------------------------------------------------------------

    def _decide(
        self, messages: Sequence[LlmMessage], available: set[str]
    ) -> LlmToolCall | None:
        if "list_files" not in available:
            # Tools were not offered (the summarise/recommend prompts): never
            # ask for one, or the caller would have nothing to execute.
            return None
        steps = sum(
            1
            for message in messages
            if message.role == "assistant" and message.tool_calls
        )
        if steps >= _MAX_STEPS:
            return None

        latest = messages[-1] if messages else None
        if latest is None:
            return None

        if latest.role == "tool":
            return self._follow_up(messages, latest, available)

        if latest.role != "user":
            return None
        return self._first_step(latest.content, available)

    def _first_step(self, text: str, available: set[str]) -> LlmToolCall | None:
        """First tool to call for a fresh user message (or ``None`` to answer)."""
        wants_action = bool(_CONVERSION_VERBS.search(text) or _SUMMARY_WORDS.search(text))
        source_format = self._named_source_format(text)
        if _FORMAT_WORDS.search(text) and source_format is not None:
            return LlmToolCall(
                id=_CALL_ID,
                name="list_supported_targets",
                arguments={"source_format": source_format},
            )
        if wants_action or _LIST_WORDS.search(text):
            query = _named_file(text)
            arguments: dict[str, Any] = {"limit": 20}
            if query is not None:
                arguments["query"] = query
            return LlmToolCall(id=_CALL_ID, name="list_files", arguments=arguments)
        if _FOLDER_WORDS.search(text):
            return LlmToolCall(id=_CALL_ID, name="list_folders", arguments={})
        return None

    def _follow_up(
        self, messages: Sequence[LlmMessage], latest: LlmMessage, available: set[str]
    ) -> LlmToolCall | None:
        """Second hop: act on a single unambiguous file from ``list_files``."""
        if latest.name != "list_files":
            return None
        file_id = self._single_file_id(latest.content)
        if file_id is None:
            # Zero or several matches: asking again would loop, so the answer
            # lists what came back and asks the user to pick.
            return None

        request = _last(messages, "user")
        text = request.content if request is not None else ""
        if _SUMMARY_WORDS.search(text):
            if "summarize_file" in available and not _already_called(messages, "summarize_file"):
                return LlmToolCall(
                    id=_CALL_ID, name="summarize_file", arguments={"file_id": file_id}
                )
            return None
        if _CONVERSION_VERBS.search(text):
            target = _target_format(text)
            if (
                target is not None
                and "start_conversion" in available
                and not _already_called(messages, "start_conversion")
            ):
                return LlmToolCall(
                    id=_CALL_ID,
                    name="start_conversion",
                    arguments={"file_id": file_id, "target_format": target},
                )
        return None

    @staticmethod
    def _single_file_id(content: str) -> str | None:
        payload = _loads(content)
        files = payload.get("files")
        if not isinstance(files, list) or len(files) != 1:
            return None
        entry = files[0]
        if not isinstance(entry, dict):
            return None
        file_id = entry.get("file_id")
        return file_id if isinstance(file_id, str) and file_id else None

    @staticmethod
    def _named_source_format(text: str) -> str | None:
        """A source format the user named that this service actually supports."""
        conversion_map = build_conversion_map()
        lowered = text.casefold()
        for fmt in conversion_map:
            if re.search(rf"\b{re.escape(fmt)}\b", lowered):
                return fmt
        return None

    # ------------------------------------------------------------------
    # Answer composition
    # ------------------------------------------------------------------

    def _compose_answer(self, messages: Sequence[LlmMessage]) -> str:
        latest = messages[-1] if messages else None
        if latest is not None and latest.role == "tool":
            return self._answer_from_tool(latest)
        return _OFFLINE_NOTICE

    def _answer_from_tool(self, message: LlmMessage) -> str:
        """A readable sentence about what the tool just returned.

        This is the only place the offline backend states a fact, and every
        fact here comes from the tool payload — never from the user's wording.
        """
        payload = _loads(message.content)
        error = payload.get("error")
        if isinstance(error, str) and error:
            return f"I couldn't do that: {error}"

        name = message.name or ""
        if name == "list_files":
            return self._answer_list_files(payload)
        if name == "list_folders":
            folders = payload.get("folders")
            names = [
                str(entry.get("name"))
                for entry in folders
                if isinstance(entry, dict) and entry.get("name")
            ] if isinstance(folders, list) else []
            if not names:
                return "You don't have any folders there yet."
            return "Folders: " + ", ".join(f"**{n}**" for n in names) + "."
        if name == "list_supported_targets":
            source = payload.get("source_format", "that")
            targets = payload.get("targets")
            listed = [str(t) for t in targets] if isinstance(targets, list) else []
            return f"A {source} file can be converted to: " + ", ".join(f"`{t}`" for t in listed) + "."
        if name == "summarize_file":
            summary = str(payload.get("summary", "")).strip()
            points = payload.get("key_points")
            lines = [f"- {p}" for p in points if isinstance(p, str)] if isinstance(points, list) else []
            body = summary or "I couldn't find much to summarise."
            return body + ("\n\n**Key points**\n" + "\n".join(lines) if lines else "")
        if name == "read_file_text":
            text = str(payload.get("text", "")).strip()
            if not text:
                return "I read the file but it appears to be empty."
            excerpt = text[:600]
            suffix = "…" if len(text) > len(excerpt) else ""
            return f"Here is what it says:\n\n{excerpt}{suffix}"
        if name == "start_conversion":
            return (
                f"Started converting **{payload.get('file_name', 'your file')}** from "
                f"`{payload.get('source_format', '?')}` to `{payload.get('target_format', '?')}`. "
                "I'll let you know as soon as it's done."
            )
        if name == "get_conversion_status":
            return (
                f"That conversion is **{payload.get('status', 'unknown')}**"
                + (f" ({payload['error_message']})" if payload.get("error_message") else ".")
            )
        if name == "list_recent_conversions":
            jobs = payload.get("jobs")
            if not isinstance(jobs, list) or not jobs:
                return "You have no conversions yet."
            lines = [
                f"- `{job.get('input_file', '?')}` → `{job.get('target_format', '?')}` — {job.get('status', '?')}"
                for job in jobs
                if isinstance(job, dict)
            ]
            return "Your recent conversions:\n" + "\n".join(lines)
        if name == "create_folder":
            return f"Created the folder **{payload.get('name', '')}**."
        if name == "move_file":
            return f"Moved **{payload.get('file_name', 'the file')}**."
        if name == "get_file_info":
            return (
                f"**{payload.get('file_name', 'That file')}** is a "
                f"`{payload.get('extension', '?')}` file of "
                f"{payload.get('size_bytes', 0)} bytes."
            )
        return "Done."

    def _answer_list_files(self, payload: dict[str, Any]) -> str:
        files = payload.get("files")
        entries = [entry for entry in files if isinstance(entry, dict)] if isinstance(files, list) else []
        if not entries:
            return "I couldn't find any files matching that."
        lines = [
            f"- **{entry.get('file_name', '?')}** (`{entry.get('extension', '?')}`)"
            for entry in entries[:10]
        ]
        header = "I found 1 file:" if len(entries) == 1 else f"I found {len(entries)} files:"
        return header + "\n" + "\n".join(lines)
