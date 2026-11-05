"""Validated data contracts at the Aggregator boundary.

Validation errors name fields only and never include entry or lesson text.
"""

import re
from datetime import date
from typing import Literal, TypedDict

LESSON_TYPES = (
    "negotiation",
    "decision-making",
    "launch",
    "pricing",
    "operations",
    "client-relationship",
)
CONFIDENCE_LEVELS = ("low", "medium", "high")


class AcceptedEntry(TypedDict):
    entry_id: str
    workspace: str
    sources: list[str]
    specific: str
    general: str
    route: Literal["green", "amber"]
    k: int | None
    cluster_id: str | None


class LessonDraft(TypedDict):
    title: str
    type: str
    applies_when: str
    confidence: str
    lesson: str
    why: str
    watch_out: str


class Lesson(LessonDraft):
    id: str
    evidence_count: int
    created: str
    reviewed_by_checker: bool


class CheckerInput(TypedDict):
    id: str
    candidate: str


class CandidateMetadata(TypedDict):
    candidate_id: str
    action: Literal["create", "update"]
    lesson_id: str
    existing_lesson_id: str | None
    source_unit_id: str
    source_entry_ids: list[str]
    route: Literal["green", "amber"]
    cluster_id: str | None
    evidence_count: int
    candidate_sha256: str


BASE_ENTRY_FIELDS = {
    "entry_id": str,
    "workspace": str,
    "sources": list,
    "specific": str,
    "general": str,
}


def validate_accepted_entry(value: object) -> AcceptedEntry:
    if not isinstance(value, dict):
        raise ValueError("accepted entry must be a JSON object")
    for field, kind in BASE_ENTRY_FIELDS.items():
        if field not in value:
            raise ValueError(f"missing field: {field}")
        if not isinstance(value[field], kind):
            raise ValueError(f"wrong type for field: {field}")
    for field in ("route", "k", "cluster_id"):
        if field not in value:
            raise ValueError(f"missing field: {field}")

    if not value["entry_id"].strip():
        raise ValueError("empty field: entry_id")
    if not value["workspace"].strip():
        raise ValueError("empty field: workspace")
    if not value["entry_id"].startswith(value["workspace"] + "#"):
        raise ValueError("entry_id must start with '<workspace>#'")
    if not value["general"].strip():
        raise ValueError("empty field: general")
    if not all(isinstance(source, str) for source in value["sources"]):
        raise ValueError("wrong type for field: sources")

    route = value["route"]
    if route == "green":
        if value["k"] is not None:
            raise ValueError("green entry must have k: null")
        if value["cluster_id"] is not None:
            raise ValueError("green entry must have cluster_id: null")
    elif route == "amber":
        k = value["k"]
        if not isinstance(k, int) or isinstance(k, bool):
            raise ValueError("amber entry must have integer k")
        if k < 3:
            raise ValueError("amber entry must have k >= 3")
        if not isinstance(value["cluster_id"], str) or not value["cluster_id"].strip():
            raise ValueError("amber entry must have non-empty cluster_id")
    else:
        raise ValueError("route must be green or amber")
    return value  # type: ignore[return-value]


def validate_lesson(value: object, *, require_reviewed: bool = False) -> Lesson:
    if not isinstance(value, dict):
        raise ValueError("lesson must be an object")
    required_strings = (
        "id",
        "title",
        "type",
        "applies_when",
        "confidence",
        "created",
        "lesson",
        "why",
        "watch_out",
    )
    for field in required_strings:
        if not isinstance(value.get(field), str) or not value[field].strip():
            raise ValueError(f"invalid lesson field: {field}")
    if not re.fullmatch(r"L-\d{4,}", value["id"]):
        raise ValueError("invalid lesson field: id")
    if value["type"] not in LESSON_TYPES:
        raise ValueError("invalid lesson field: type")
    if value["confidence"] not in CONFIDENCE_LEVELS:
        raise ValueError("invalid lesson field: confidence")
    count = value.get("evidence_count")
    if not isinstance(count, int) or isinstance(count, bool) or count < 1:
        raise ValueError("invalid lesson field: evidence_count")
    reviewed = value.get("reviewed_by_checker")
    if not isinstance(reviewed, bool):
        raise ValueError("invalid lesson field: reviewed_by_checker")
    if require_reviewed and not reviewed:
        raise ValueError("existing lesson must be reviewed by checker")
    try:
        date.fromisoformat(value["created"])
    except ValueError as error:
        raise ValueError("invalid lesson field: created") from error
    return value  # type: ignore[return-value]


def validate_lesson_draft(value: object) -> LessonDraft:
    if not isinstance(value, dict):
        raise ValueError("lesson draft must be an object")
    required = ("title", "type", "applies_when", "confidence", "lesson", "why", "watch_out")
    if set(value) != set(required):
        raise ValueError("lesson draft has wrong fields")
    for field in required:
        if not isinstance(value[field], str) or not value[field].strip():
            raise ValueError(f"invalid lesson draft field: {field}")
    if value["type"] not in LESSON_TYPES:
        raise ValueError("invalid lesson draft field: type")
    if value["confidence"] not in CONFIDENCE_LEVELS:
        raise ValueError("invalid lesson draft field: confidence")
    return {field: value[field].strip() for field in required}  # type: ignore[return-value]


def validate_checker_input(value: object) -> CheckerInput:
    if not isinstance(value, dict) or set(value) != {"id", "candidate"}:
        raise ValueError("checker input must contain id and candidate")
    for field in ("id", "candidate"):
        if not isinstance(value[field], str) or not value[field].strip():
            raise ValueError(f"invalid checker input field: {field}")
    return value  # type: ignore[return-value]


def validate_candidate_metadata(value: object) -> CandidateMetadata:
    if not isinstance(value, dict):
        raise ValueError("candidate metadata must be an object")
    string_fields = ("candidate_id", "action", "lesson_id", "source_unit_id", "route", "candidate_sha256")
    for field in string_fields:
        if not isinstance(value.get(field), str) or not value[field].strip():
            raise ValueError(f"invalid candidate metadata field: {field}")
    if value["action"] not in ("create", "update"):
        raise ValueError("candidate action must be create or update")
    if value["route"] not in ("green", "amber"):
        raise ValueError("candidate route must be green or amber")
    if not re.fullmatch(r"L-\d{4,}", value["lesson_id"]):
        raise ValueError("invalid candidate metadata field: lesson_id")
    ids = value.get("source_entry_ids")
    if not isinstance(ids, list) or not ids or not all(isinstance(item, str) for item in ids):
        raise ValueError("invalid candidate metadata field: source_entry_ids")
    count = value.get("evidence_count")
    if not isinstance(count, int) or isinstance(count, bool) or count < 1:
        raise ValueError("invalid candidate metadata field: evidence_count")
    existing = value.get("existing_lesson_id")
    if value["action"] == "create" and existing is not None:
        raise ValueError("create candidate must have existing_lesson_id: null")
    if value["action"] == "update" and (not isinstance(existing, str) or not existing.strip()):
        raise ValueError("update candidate must have existing_lesson_id")
    cluster = value.get("cluster_id")
    if value["route"] == "green" and cluster is not None:
        raise ValueError("green candidate must have cluster_id: null")
    if value["route"] == "amber" and (not isinstance(cluster, str) or not cluster.strip()):
        raise ValueError("amber candidate must have cluster_id")
    return value  # type: ignore[return-value]

