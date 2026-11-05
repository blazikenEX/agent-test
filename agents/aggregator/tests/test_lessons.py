import pytest

from aggregator.lessons import (
    next_lesson_id,
    parse_lesson_markdown,
    read_lesson_library,
    render_lesson_markdown,
)
from conftest import make_draft


def test_parses_data_pack_lesson_style_with_en_dash():
    text = """---
id: L-0004
title: Use a public benchmark carefully
type: launch
applies_when: A launch decision uses public research
confidence: medium
evidence_count: 1
created: 2026-03-10
reviewed_by_checker: true
---

**Lesson** – Retain a named public source when it supports the lesson.

**Why** – Attribution distinguishes a public fact from a client fact.

**Watch out** – Update the lesson when stronger evidence appears.
"""
    lesson = parse_lesson_markdown(text)
    assert lesson["id"] == "L-0004"
    assert lesson["lesson"].startswith("Retain")


def test_rendered_candidate_round_trips_but_is_not_an_approved_library_lesson():
    text = render_lesson_markdown(
        "L-0005",
        make_draft(title="Use a colon: only when useful"),
        evidence_count=3,
        created="2026-06-30",
        reviewed_by_checker=False,
    )
    candidate = parse_lesson_markdown(text, require_reviewed=False)
    assert candidate["title"] == "Use a colon: only when useful"
    assert candidate["reviewed_by_checker"] is False
    with pytest.raises(ValueError, match="reviewed"):
        parse_lesson_markdown(text)


def test_library_reader_ignores_schema_and_requires_unique_ids(tmp_path):
    directory = tmp_path / "lessons"
    directory.mkdir()
    (directory / "SCHEMA.md").write_text("not a lesson", encoding="utf-8")
    lesson = render_lesson_markdown(
        "L-0001",
        make_draft(),
        evidence_count=2,
        created="2026-01-01",
        reviewed_by_checker=True,
    )
    (directory / "L-0001-first.md").write_text(lesson, encoding="utf-8")
    assert [item["id"] for item in read_lesson_library(directory)] == ["L-0001"]
    (directory / "L-0001-copy.md").write_text(lesson, encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate lesson id"):
        read_lesson_library(directory)


def test_allocates_next_lesson_id():
    assert next_lesson_id({"L-0001", "L-0004"}) == "L-0005"

