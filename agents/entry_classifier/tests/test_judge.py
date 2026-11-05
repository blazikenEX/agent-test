import json

import pytest
from conftest import FakeClient

from entry_classifier.judge import ClaudeJudge

SPECIFIC = "INVENTED: Northwind's PM promised 3 March before any load test."
GENERAL = "A team promised a delivery date before testing at realistic scale."


def answer_with(obj):
    return lambda payload: json.dumps(obj)


def test_valid_answer():
    client = FakeClient(answer_with({"color": "amber", "reasons": ["single_experience"]}))
    result = ClaudeJudge(client=client).classify(SPECIFIC, GENERAL, [])
    assert result.ok and result.color == "amber" and result.reasons == ["single_experience"]
    assert (result.input_tokens, result.output_tokens) == (100, 20)
    request = client.requests[0]
    assert request["model"] == "claude-sonnet-5-5"
    assert request["output_config"]["format"]["type"] == "json_schema"


def test_only_this_entry_is_sent():
    client = FakeClient()
    ClaudeJudge(client=client).classify(SPECIFIC, GENERAL, ["Northwind"])
    payload = json.loads(client.requests[0]["messages"][0]["content"].removeprefix("<entry>\n").removesuffix("\n</entry>"))
    assert set(payload) == {"specific", "general", "hints"}
    assert payload["hints"] == ["Northwind"]


def test_reasons_can_only_make_it_stricter():
    client = FakeClient(answer_with({"color": "green", "reasons": ["common_practice", "identifies_client"]}))
    result = ClaudeJudge(client=client).classify(SPECIFIC, GENERAL, [])
    assert result.color == "green" and result.strict_color == "red"


@pytest.mark.parametrize("stop_reason", ["refusal", "max_tokens"])
def test_bad_stop_reason_fails_closed(stop_reason):
    result = ClaudeJudge(client=FakeClient(stop_reason=stop_reason)).classify(SPECIFIC, GENERAL, [])
    assert not result.ok and result.strict_color is None
    assert result.error == f"stop_reason:{stop_reason}"


def test_sdk_error_fails_closed():
    result = ClaudeJudge(client=FakeClient(error=RuntimeError("boom"))).classify(SPECIFIC, GENERAL, [])
    assert not result.ok and result.error == "RuntimeError"


@pytest.mark.parametrize(
    "answer, error",
    [
        ("not json", "unparsable"),
        (json.dumps({"color": "green"}), "unparsable"),
        (json.dumps({"color": "blue", "reasons": ["common_practice"]}), "bad_answer"),
        (json.dumps({"color": "green", "reasons": []}), "bad_answer"),
        (json.dumps({"color": "green", "reasons": ["looks_fine"]}), "bad_answer"),
        (json.dumps({"color": "green", "reasons": "common_practice"}), "bad_answer"),
    ],
)
def test_malformed_answers_fail_closed(answer, error):
    result = ClaudeJudge(client=FakeClient(lambda payload: answer)).classify(SPECIFIC, GENERAL, [])
    assert not result.ok and result.error == error


def test_cache_avoids_second_call_and_holds_no_text(tmp_path):
    cache = tmp_path / "classifier.cache.json"
    ClaudeJudge(client=FakeClient(), cache_path=cache).classify(SPECIFIC, GENERAL, [])

    second = FakeClient()
    result = ClaudeJudge(client=second, cache_path=cache).classify(SPECIFIC, GENERAL, [])
    assert second.requests == []
    assert result.cached and result.ok and result.color == "green"
    stored = cache.read_text()
    assert "Northwind" not in stored and "delivery" not in stored
    assert oct(cache.stat().st_mode & 0o777) == "0o600"


def test_failures_are_not_cached(tmp_path):
    cache = tmp_path / "classifier.cache.json"
    ClaudeJudge(client=FakeClient(stop_reason="refusal"), cache_path=cache).classify(SPECIFIC, GENERAL, [])
    retry = FakeClient()
    result = ClaudeJudge(client=retry, cache_path=cache).classify(SPECIFIC, GENERAL, [])
    assert len(retry.requests) == 1 and result.ok


def test_cache_depends_on_model(tmp_path):
    cache = tmp_path / "classifier.cache.json"
    ClaudeJudge(client=FakeClient(), cache_path=cache).classify(SPECIFIC, GENERAL, [])
    other = FakeClient()
    ClaudeJudge(model="claude-opus-5-5", client=other, cache_path=cache).classify(SPECIFIC, GENERAL, [])
    assert len(other.requests) == 1
