"""Invented Aggregator fixtures. Tests never make API calls."""

import json
from types import SimpleNamespace

import pytest

from aggregator.lessons import render_lesson_markdown
from aggregator.matcher import MatchResult


def make_entry(
    workspace: str,
    number: int,
    general: str,
    *,
    route: str = "green",
    k: int | None = None,
    cluster_id: str | None = None,
    specific: str | None = None,
) -> dict:
    return {
        "entry_id": f"{workspace}#{number:03d}",
        "workspace": workspace,
        "sources": ["01-notes.md"],
        "specific": specific or f"SECRET-{workspace}-{number}",
        "general": general,
        "route": route,
        "k": k,
        "cluster_id": cluster_id,
    }


def make_draft(**overrides) -> dict:
    return {
        "title": "Validate before committing",
        "type": "decision-making",
        "applies_when": "A commitment depends on an untested operational assumption",
        "confidence": "medium",
        "lesson": "Validate the assumption before making the commitment.",
        "why": "Testing first exposes constraints while plans can still change.",
        "watch_out": "Use a test that reflects the real operating conditions.",
    } | overrides


def write_lesson(directory, lesson_id="L-0001", **draft_overrides):
    directory.mkdir(parents=True, exist_ok=True)
    text = render_lesson_markdown(
        lesson_id,
        make_draft(**draft_overrides),
        evidence_count=2,
        created="2026-01-15",
        reviewed_by_checker=True,
    )
    path = directory / f"{lesson_id}-fixture.md"
    path.write_text(text, encoding="utf-8")
    return path


class FakeMatcher:
    def __init__(self, results: list[MatchResult] | None = None):
        self.results = list(
            results
            or [MatchResult(action="create", draft=make_draft(), model="fake")]
        )
        self.calls: list[tuple[list[str], list[dict], int]] = []

    def aggregate(self, general_entries, existing_lessons, evidence_count):
        self.calls.append(
            (
                list(general_entries),
                [dict(item) for item in existing_lessons],
                evidence_count,
            )
        )
        if len(self.results) == 1:
            return self.results[0]
        return self.results.pop(0)


def write_jsonl(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(item) + "\n" for item in records), encoding="utf-8")


def read_jsonl(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


@pytest.fixture
def paths(tmp_path):
    data = tmp_path / "data"
    return SimpleNamespace(
        root=tmp_path,
        accepted=data / "accepted_entries.jsonl",
        lessons=data / "lessons",
        checker=data / "checker-input.jsonl",
        metadata=data / "candidate-metadata.jsonl",
        state=data / "aggregator-state.jsonl",
        decision=data / "aggregator-decisions.jsonl",
        actions=data / "actions.log.jsonl",
    )

