"""Read, validate, render, and allocate ids for lesson Markdown files."""

import json
import re
from collections.abc import Callable
from pathlib import Path

from .schema import Lesson, LessonDraft, validate_lesson

BODY_PATTERN = re.compile(
    r"\*\*Lesson\*\*\s*[–-]\s*(.*?)\n\s*\n"
    r"\*\*Why\*\*\s*[–-]\s*(.*?)\n\s*\n"
    r"\*\*Watch out\*\*\s*[–-]\s*(.*?)\s*$",
    re.DOTALL,
)


def read_lesson_library(
    directory: Path,
    on_read: Callable[[Path], None] | None = None,
) -> list[Lesson]:
    if not directory.exists():
        return []
    if not directory.is_dir():
        raise ValueError("lessons path must be a directory")
    lessons = []
    for path in sorted(directory.glob("L-*.md")):
        if on_read is not None:
            on_read(path)
        lessons.append(parse_lesson_markdown(path.read_text(encoding="utf-8")))
    ids = [lesson["id"] for lesson in lessons]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate lesson id")
    return lessons


def parse_lesson_markdown(text: str, *, require_reviewed: bool = True) -> Lesson:
    if not text.startswith("---\n"):
        raise ValueError("lesson is missing YAML front matter")
    try:
        front, body = text[4:].split("\n---\n", 1)
    except ValueError as error:
        raise ValueError("lesson has unterminated YAML front matter") from error
    fields: dict[str, object] = {}
    for line in front.splitlines():
        if not line.strip():
            continue
        if ":" not in line:
            raise ValueError("invalid lesson front matter line")
        key, raw = line.split(":", 1)
        fields[key.strip()] = _parse_scalar(raw.strip())
    match = BODY_PATTERN.fullmatch(body.strip())
    if match is None:
        raise ValueError("lesson body must contain Lesson, Why, and Watch out sections")
    fields.update(
        {
            "lesson": match.group(1).strip(),
            "why": match.group(2).strip(),
            "watch_out": match.group(3).strip(),
        }
    )
    return validate_lesson(fields, require_reviewed=require_reviewed)


def render_lesson_markdown(
    lesson_id: str,
    draft: LessonDraft,
    *,
    evidence_count: int,
    created: str,
    reviewed_by_checker: bool,
) -> str:
    lesson = validate_lesson(
        {
            "id": lesson_id,
            **draft,
            "evidence_count": evidence_count,
            "created": created,
            "reviewed_by_checker": reviewed_by_checker,
        }
    )
    return (
        "---\n"
        f"id: {_format_scalar(lesson['id'])}\n"
        f"title: {_format_scalar(lesson['title'])}\n"
        f"type: {_format_scalar(lesson['type'])}\n"
        f"applies_when: {_format_scalar(lesson['applies_when'])}\n"
        f"confidence: {_format_scalar(lesson['confidence'])}\n"
        f"evidence_count: {lesson['evidence_count']}\n"
        f"created: {lesson['created']}\n"
        f"reviewed_by_checker: {'true' if reviewed_by_checker else 'false'}\n"
        "---\n\n"
        f"**Lesson** - {lesson['lesson']}\n\n"
        f"**Why** - {lesson['why']}\n\n"
        f"**Watch out** - {lesson['watch_out']}\n"
    )


def lesson_text_for_model(lesson: Lesson) -> str:
    """Approved, de-identified content only; excludes ids, dates, and review metadata."""
    return (
        f"Title: {lesson['title']}\n"
        f"Type: {lesson['type']}\n"
        f"Applies when: {lesson['applies_when']}\n"
        f"Confidence: {lesson['confidence']}\n"
        f"Evidence count: {lesson['evidence_count']}\n\n"
        f"Lesson: {lesson['lesson']}\n"
        f"Why: {lesson['why']}\n"
        f"Watch out: {lesson['watch_out']}"
    )


def next_lesson_id(existing_ids: set[str]) -> str:
    highest = max((int(value.removeprefix("L-")) for value in existing_ids), default=0)
    return f"L-{highest + 1:04d}"


def _parse_scalar(raw: str) -> object:
    if raw == "true":
        return True
    if raw == "false":
        return False
    if re.fullmatch(r"\d+", raw):
        return int(raw)
    if raw.startswith("'") and raw.endswith("'"):
        return raw[1:-1].replace("''", "'")
    if raw.startswith('"'):
        try:
            return json.loads(raw)
        except json.JSONDecodeError as error:
            raise ValueError("invalid quoted front matter value") from error
    return raw


def _format_scalar(value: str) -> str:
    if (
        value
        and "\n" not in value
        and ": " not in value
        and " #" not in value
        and value not in ("true", "false")
        and not value[0].isspace()
        and not value[-1].isspace()
    ):
        return value
    return json.dumps(value, ensure_ascii=False)

