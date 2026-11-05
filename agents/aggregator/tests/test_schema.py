import pytest

from aggregator.schema import (
    validate_accepted_entry,
    validate_candidate_metadata,
    validate_checker_input,
    validate_lesson,
    validate_lesson_draft,
)
from conftest import make_draft, make_entry


def test_accepts_valid_green_and_amber_entries():
    green = make_entry("client-a", 1, "A general lesson.")
    amber = make_entry("client-a", 2, "A pattern.", route="amber", k=3, cluster_id="c-one")
    assert validate_accepted_entry(green) is green
    assert validate_accepted_entry(amber) is amber


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"k": 2, "route": "amber", "cluster_id": "c-one"}, "k >= 3"),
        ({"k": 3, "route": "amber", "cluster_id": None}, "cluster_id"),
        ({"k": 3}, "green entry must have k"),
        ({"cluster_id": "c-one"}, "green entry must have cluster_id"),
        ({"route": "red"}, "route"),
    ],
)
def test_rejects_entries_that_are_not_accepted(changes, message):
    entry = make_entry("client-a", 1, "A general lesson.") | changes
    with pytest.raises(ValueError, match=message):
        validate_accepted_entry(entry)


def test_validates_real_lesson_schema_and_review_status():
    lesson = {
        "id": "L-0001",
        **make_draft(),
        "evidence_count": 3,
        "created": "2026-06-30",
        "reviewed_by_checker": True,
    }
    assert validate_lesson(lesson, require_reviewed=True) is lesson
    with pytest.raises(ValueError, match="reviewed"):
        validate_lesson(lesson | {"reviewed_by_checker": False}, require_reviewed=True)


def test_draft_requires_taxonomy_and_all_three_sections():
    assert validate_lesson_draft(make_draft())["type"] == "decision-making"
    with pytest.raises(ValueError, match="type"):
        validate_lesson_draft(make_draft(type="unknown"))
    incomplete = make_draft()
    del incomplete["watch_out"]
    with pytest.raises(ValueError, match="wrong fields"):
        validate_lesson_draft(incomplete)


def test_checker_input_has_exact_external_contract():
    record = {"id": "candidate-1", "candidate": "complete markdown"}
    assert validate_checker_input(record) is record
    with pytest.raises(ValueError, match="id and candidate"):
        validate_checker_input(record | {"private": "must not leak"})


def test_update_metadata_requires_existing_lesson():
    metadata = {
        "candidate_id": "candidate-1",
        "action": "update",
        "lesson_id": "L-0001",
        "existing_lesson_id": None,
        "source_unit_id": "c-one",
        "source_entry_ids": ["client-a#001"],
        "route": "amber",
        "cluster_id": "c-one",
        "evidence_count": 3,
        "candidate_sha256": "abc123",
    }
    with pytest.raises(ValueError, match="existing_lesson_id"):
        validate_candidate_metadata(metadata)

