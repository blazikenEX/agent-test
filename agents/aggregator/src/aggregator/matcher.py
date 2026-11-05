"""Claude-backed lesson aggregation.

Only generalized entry text and approved lesson text enter this module. Identifiers are replaced
with opaque labels before a request is made.
"""

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse

import anthropic

from .lessons import lesson_text_for_model
from .schema import LESSON_TYPES, Lesson, LessonDraft, validate_lesson_draft

DEFAULT_MODEL = "claude-sonnet-5-5"
PROMPT_VERSION = "v2"
ACTIONS = ("create", "update", "skip")

DRAFT_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "type": {"type": "string", "enum": list(LESSON_TYPES)},
        "applies_when": {"type": "string"},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        "lesson": {"type": "string"},
        "why": {"type": "string"},
        "watch_out": {"type": "string"},
    },
    "required": ["title", "type", "applies_when", "confidence", "lesson", "why", "watch_out"],
    "additionalProperties": False,
}

TOOL = {
    "name": "record_aggregation_decision",
    "description": "Record whether to create, update, or skip a complete lesson candidate.",
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": list(ACTIONS)},
            "existing_lesson_label": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            "candidate": {"anyOf": [DRAFT_SCHEMA, {"type": "null"}]},
        },
        "required": ["action", "existing_lesson_label", "candidate"],
        "additionalProperties": False,
    },
}


@dataclass
class MatchResult:
    action: str | None = None
    draft: LessonDraft | None = None
    existing_lesson_id: str | None = None
    ok: bool = True
    error: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    model: str | None = None


class Matcher(Protocol):
    def aggregate(
        self,
        general_entries: list[str],
        existing_lessons: list[Lesson],
        evidence_count: int,
    ) -> MatchResult:
        ...


class ClaudeMatcher:
    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        client: anthropic.Anthropic | None = None,
        system_prompt: str | None = None,
        on_http: Callable[[str], None] | None = None,
    ):
        self.model = model
        self._client = client
        self.system_prompt = system_prompt or _read_system_prompt()
        self.on_http = on_http
        self.api_host = urlparse(os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com")).hostname or "api.anthropic.com"

    @property
    def client(self) -> anthropic.Anthropic:
        if self._client is None:
            self._client = anthropic.Anthropic()
        return self._client

    def aggregate(
        self,
        general_entries: list[str],
        existing_lessons: list[Lesson],
        evidence_count: int,
    ) -> MatchResult:
        lesson_labels = {f"l{index}": item["id"] for index, item in enumerate(existing_lessons, start=1)}
        payload = {
            "evidence_count": evidence_count,
            "evidence": [
                {"label": f"e{index}", "text": text}
                for index, text in enumerate(general_entries, start=1)
            ],
            "existing_lessons": [
                {"label": label, "text": lesson_text_for_model(item)}
                for label, item in zip(lesson_labels, existing_lessons, strict=True)
            ],
        }
        user = "<aggregation-data>\n" + json.dumps(payload, ensure_ascii=False) + "\n</aggregation-data>"
        last_error = ""
        total_input = 0
        total_output = 0

        for attempt in range(2):
            content = user
            if attempt:
                content += (
                    "\n\nYour previous tool input was invalid: "
                    + last_error
                    + "\nCall record_aggregation_decision again with valid input."
                )
            if self.on_http is not None:
                self.on_http(self.api_host)
            try:
                response = self.client.messages.create(
                    model=self.model,
                    max_tokens=3000,
                    temperature=0,
                    system=self.system_prompt,
                    tools=[TOOL],
                    tool_choice={"type": "tool", "name": TOOL["name"]},
                    messages=[{"role": "user", "content": content}],
                )
            except Exception as error:  # Fail closed on SDK, network, and API errors.
                return MatchResult(ok=False, error=type(error).__name__, model=self.model)

            total_input += response.usage.input_tokens
            total_output += response.usage.output_tokens
            if response.stop_reason != "tool_use":
                return MatchResult(
                    ok=False,
                    error=f"stop_reason:{response.stop_reason}",
                    input_tokens=total_input,
                    output_tokens=total_output,
                    model=self.model,
                )
            block = next((item for item in response.content if item.type == "tool_use"), None)
            parsed, last_error = _parse_tool_input(block.input if block is not None else None, lesson_labels)
            if parsed is not None:
                return MatchResult(
                    **parsed,
                    input_tokens=total_input,
                    output_tokens=total_output,
                    model=self.model,
                )

        return MatchResult(
            ok=False,
            error=f"invalid_tool_input:{last_error}",
            input_tokens=total_input,
            output_tokens=total_output,
            model=self.model,
        )


def _parse_tool_input(value: object, lesson_labels: dict[str, str]) -> tuple[dict | None, str]:
    if not isinstance(value, dict):
        return None, "tool input must be an object"
    if set(value) != {"action", "existing_lesson_label", "candidate"}:
        return None, "tool input has wrong fields"
    action = value["action"]
    label = value["existing_lesson_label"]
    candidate = value["candidate"]
    if action not in ACTIONS:
        return None, "invalid action"
    if action == "create":
        if label is not None:
            return None, "create must not select an existing lesson"
        try:
            draft = validate_lesson_draft(candidate)
        except ValueError as error:
            return None, str(error)
        return {"action": action, "draft": draft, "existing_lesson_id": None}, ""
    if not isinstance(label, str) or label not in lesson_labels:
        return None, f"{action} must select a known existing lesson label"
    if action == "update":
        try:
            draft = validate_lesson_draft(candidate)
        except ValueError as error:
            return None, str(error)
        return {
            "action": action,
            "draft": draft,
            "existing_lesson_id": lesson_labels[label],
        }, ""
    if candidate is not None:
        return None, "skip must have candidate: null"
    return {"action": action, "draft": None, "existing_lesson_id": lesson_labels[label]}, ""


def _read_system_prompt() -> str:
    path = Path(__file__).with_name("prompts") / "aggregate.md"
    return path.read_text(encoding="utf-8")

