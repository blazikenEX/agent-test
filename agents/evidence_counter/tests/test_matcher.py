import json

import pytest
from conftest import FakeClient

from evidence_counter.matcher import ClaudeMatcher

NEW = "deadline: a team promised a date before testing"
CANDIDATES = [
    ("acme#001", "deadline: a date was committed with no load test"),
    ("beta#004", "hiring: a key role stayed open for months"),
]


def test_valid_answer_maps_labels_back_to_ids():
    client = FakeClient()
    result = ClaudeMatcher(client=client).match(NEW, CANDIDATES)
    assert result.ok
    assert result.verdicts == {"acme#001": "same", "beta#004": "different"}
    assert result.same_ids == ["acme#001"]
    request = client.requests[0]
    assert request["model"] == "claude-sonnet-5-5"
    assert request["output_config"]["format"]["type"] == "json_schema"


def test_entry_ids_never_sent():
    client = FakeClient()
    ClaudeMatcher(client=client).match(NEW, CANDIDATES)
    sent = repr(client.requests)
    assert "acme" not in sent and "beta" not in sent
    assert '"c1"' in sent and '"c2"' in sent


@pytest.mark.parametrize("stop_reason", ["refusal", "max_tokens"])
def test_bad_stop_reason_fails_closed(stop_reason):
    result = ClaudeMatcher(client=FakeClient(stop_reason=stop_reason)).match(NEW, CANDIDATES)
    assert not result.ok and result.same_ids == []
    assert result.error == f"stop_reason:{stop_reason}"


@pytest.mark.parametrize(
    "answer, error",
    [
        ("not json", "unparsable"),
        (json.dumps({"verdicts": [{"label": "c1", "verdict": "same"}]}), "bad_labels"),  # c2 missing
        (
            json.dumps(
                {"verdicts": [{"label": "c1", "verdict": "same"}, {"label": "c9", "verdict": "same"}]}
            ),
            "bad_labels",
        ),
        (
            json.dumps(
                {"verdicts": [{"label": "c1", "verdict": "same"}, {"label": "c1", "verdict": "same"}]}
            ),
            "bad_labels",
        ),
        (
            json.dumps(
                {"verdicts": [{"label": "c1", "verdict": "maybe"}, {"label": "c2", "verdict": "same"}]}
            ),
            "bad_labels",
        ),
        (json.dumps({"verdicts": ["c1", "c2"]}), "AttributeError"),
    ],
)
def test_malformed_answers_fail_closed(answer, error):
    result = ClaudeMatcher(client=FakeClient(judge=lambda payload: answer)).match(NEW, CANDIDATES)
    assert not result.ok and result.same_ids == []
    assert result.error == error


def test_cache_avoids_second_call(tmp_path):
    cache = tmp_path / "verdicts.cache.json"
    first = FakeClient()
    ClaudeMatcher(client=first, cache_path=cache).match(NEW, CANDIDATES)

    second = FakeClient()
    result = ClaudeMatcher(client=second, cache_path=cache).match(NEW, CANDIDATES)
    assert second.requests == []
    assert result.cached == 2 and result.same_ids == ["acme#001"]


def test_cache_is_per_model(tmp_path):
    cache = tmp_path / "verdicts.cache.json"
    ClaudeMatcher(client=FakeClient(), cache_path=cache).match(NEW, CANDIDATES)
    other = FakeClient()
    ClaudeMatcher(model="claude-opus-5-5", client=other, cache_path=cache).match(NEW, CANDIDATES)
    assert len(other.requests) == 1


def test_only_uncached_candidates_are_sent(tmp_path):
    cache = tmp_path / "verdicts.cache.json"
    ClaudeMatcher(client=FakeClient(), cache_path=cache).match(NEW, CANDIDATES[:1])
    client = FakeClient()
    ClaudeMatcher(client=client, cache_path=cache).match(NEW, CANDIDATES)
    payload = client.requests[0]["messages"][0]["content"]
    assert "hiring" in payload and "load test" not in payload
