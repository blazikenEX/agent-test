"""Invented entries and fake judges/clients. No real data, no API calls."""

import json
from types import SimpleNamespace

import pytest

from entry_classifier.judge import JudgeResult


def make_entry(workspace: str, number: int, general: str, specific: str | None = None) -> dict:
    return {
        "entry_id": f"{workspace}#{number:03d}",
        "workspace": workspace,
        "sources": ["01-notes.md"],
        "specific": specific or f"SECRET-{workspace}-{number} client wording",
        "general": general,
    }


class FakeJudge:
    """Answers from a dict general -> (color, reasons). Unknown text: amber/single_experience."""

    def __init__(self, answers: dict | None = None, fail: str | None = None):
        self.answers = answers or {}
        self.fail = fail
        self.calls: list[tuple[str, str, list[str]]] = []

    def classify(self, specific, general, hints):
        self.calls.append((specific, general, hints))
        if self.fail:
            return JudgeResult(ok=False, error=self.fail, model="fake")
        color, reasons = self.answers.get(general, ("amber", ["single_experience"]))
        return JudgeResult(color=color, reasons=reasons, model="fake", input_tokens=10, output_tokens=5)


class FakeClient:
    """Stands in for anthropic.Anthropic. `answer(payload)` returns the response text."""

    def __init__(self, answer=None, stop_reason="end_turn", error: Exception | None = None):
        self.answer = answer or (lambda payload: json.dumps({"color": "green", "reasons": ["common_practice"]}))
        self.stop_reason = stop_reason
        self.error = error
        self.requests: list[dict] = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        if self.error is not None:
            raise self.error
        content = kwargs["messages"][0]["content"]
        payload = json.loads(content.removeprefix("<entry>\n").removesuffix("\n</entry>"))
        return SimpleNamespace(
            stop_reason=self.stop_reason,
            usage=SimpleNamespace(input_tokens=100, output_tokens=20),
            content=[SimpleNamespace(type="text", text=self.answer(payload))],
        )


class FakeCounter:
    """Stands in for the Evidence Counter: records amber entries, always quarantines (K = 1)."""

    def __init__(self, route: str = "quarantined", k: int = 1):
        self.route, self.k = route, k
        self.entries: list[dict] = []

    def __call__(self, entry):
        self.entries.append(entry)
        return SimpleNamespace(route=self.route, reason="no_candidates", k=self.k, input_tokens=0, output_tokens=0)


@pytest.fixture
def paths(tmp_path):
    data = tmp_path / "data"
    return SimpleNamespace(
        accepted=data / "accepted_entries.jsonl",
        quarantine=data / "quarantined_entries.jsonl",
        log=data / "decisions.jsonl",
        counter_log=data / "counter_decisions.jsonl",
        cache=data / "classifier.cache.json",
    )
