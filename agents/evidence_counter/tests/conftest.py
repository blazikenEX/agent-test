"""Invented entries and fake matchers/clients. No real data, no API calls."""

import json
from types import SimpleNamespace

import pytest

from evidence_counter.matcher import MatchResult


def make_entry(workspace: str, number: int, general: str, specific: str | None = None) -> dict:
    return {
        "entry_id": f"{workspace}#{number:03d}",
        "workspace": workspace,
        "sources": ["01-notes.md"],
        "specific": specific or f"SECRET-{workspace}-{number} client wording",
        "general": general,
    }


class TopicMatcher:
    """Fake matcher: `same` when the text before ':' is equal, else `different`.

    `overrides` maps (new_general, candidate_general) to a verdict for special cases.
    """

    def __init__(self, overrides: dict | None = None):
        self.overrides = overrides or {}
        self.calls: list[list[str]] = []

    def match(self, new_general, candidates):
        self.calls.append([cid for cid, _ in candidates])
        verdicts = {}
        for cid, general in candidates:
            verdict = self.overrides.get((new_general, general))
            if verdict is None:
                verdict = "same" if general.split(":")[0] == new_general.split(":")[0] else "different"
            verdicts[cid] = verdict
        return MatchResult(verdicts=verdicts, model="fake", input_tokens=10, output_tokens=5)


class FakeClient:
    """Stands in for anthropic.Anthropic. `judge(payload)` returns the response text."""

    def __init__(self, judge=None, stop_reason="end_turn", error: Exception | None = None):
        self.judge = judge or same_by_topic
        self.stop_reason = stop_reason
        self.error = error
        self.requests: list[dict] = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        if self.error is not None:
            raise self.error
        content = kwargs["messages"][0]["content"]
        payload = json.loads(content.removeprefix("<lessons>\n").removesuffix("\n</lessons>"))
        return SimpleNamespace(
            stop_reason=self.stop_reason,
            usage=SimpleNamespace(input_tokens=100, output_tokens=20),
            content=[SimpleNamespace(type="text", text=self.judge(payload))],
        )


def same_by_topic(payload) -> str:
    topic = payload["new"].split(":")[0]
    return json.dumps(
        {
            "verdicts": [
                {"label": c["label"], "verdict": "same" if c["lesson"].split(":")[0] == topic else "different"}
                for c in payload["candidates"]
            ]
        }
    )


@pytest.fixture
def paths(tmp_path):
    data = tmp_path / "data"
    return SimpleNamespace(
        quarantine=data / "quarantined_entries.jsonl",
        accepted=data / "accepted_entries.jsonl",
        log=data / "decisions.jsonl",
        cache=data / "verdicts.cache.json",
    )
