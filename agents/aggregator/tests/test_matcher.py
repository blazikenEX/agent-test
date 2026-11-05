import json
from types import SimpleNamespace

from aggregator.matcher import ClaudeMatcher
from conftest import make_draft


class FakeClient:
    def __init__(self, tool_inputs, *, stop_reason="tool_use", error=None):
        self.tool_inputs = list(tool_inputs)
        self.stop_reason = stop_reason
        self.error = error
        self.requests = []
        self.messages = SimpleNamespace(create=self.create)

    def create(self, **kwargs):
        self.requests.append(kwargs)
        if self.error is not None:
            raise self.error
        tool_input = self.tool_inputs.pop(0)
        return SimpleNamespace(
            stop_reason=self.stop_reason,
            usage=SimpleNamespace(input_tokens=100, output_tokens=20),
            content=[SimpleNamespace(type="tool_use", input=tool_input)],
        )


def request_payload(client):
    content = client.requests[0]["messages"][0]["content"]
    return json.loads(content.removeprefix("<aggregation-data>\n").removesuffix("\n</aggregation-data>"))


def existing_lesson():
    return {
        "id": "L-0001",
        **make_draft(title="Approved lesson"),
        "evidence_count": 2,
        "created": "2026-01-15",
        "reviewed_by_checker": True,
    }


def test_request_uses_opaque_labels_safe_text_and_evidence_count():
    client = FakeClient([{"action": "skip", "existing_lesson_label": "l1", "candidate": None}])
    http_hosts = []
    matcher = ClaudeMatcher(client=client, on_http=http_hosts.append)
    result = matcher.aggregate(["General entry text."], [existing_lesson()], 3)
    payload = request_payload(client)

    assert result.ok
    assert result.existing_lesson_id == "L-0001"
    assert payload["evidence_count"] == 3
    assert payload["evidence"] == [{"label": "e1", "text": "General entry text."}]
    assert payload["existing_lessons"][0]["label"] == "l1"
    assert "Approved lesson" in payload["existing_lessons"][0]["text"]
    request_text = json.dumps(client.requests)
    assert "L-0001" not in request_text
    assert "2026-01-15" not in request_text
    assert "untrusted data" in client.requests[0]["system"]
    assert http_hosts == ["api.anthropic.com"]


def test_parses_create_and_update_complete_drafts():
    create_client = FakeClient(
        [{"action": "create", "existing_lesson_label": None, "candidate": make_draft()}]
    )
    created = ClaudeMatcher(client=create_client, system_prompt="system").aggregate(["Evidence."], [], 1)
    assert created.action == "create"
    assert created.draft["watch_out"].startswith("Use a test")
    assert created.existing_lesson_id is None

    update_client = FakeClient(
        [{"action": "update", "existing_lesson_label": "l1", "candidate": make_draft(title="Updated")}]
    )
    updated = ClaudeMatcher(client=update_client, system_prompt="system").aggregate(
        ["Evidence."], [existing_lesson()], 3
    )
    assert updated.action == "update"
    assert updated.draft["title"] == "Updated"
    assert updated.existing_lesson_id == "L-0001"


def test_invalid_tool_input_is_retried_once_and_logs_each_http_call():
    client = FakeClient(
        [
            {"action": "update", "existing_lesson_label": "unknown", "candidate": make_draft()},
            {"action": "create", "existing_lesson_label": None, "candidate": make_draft()},
        ]
    )
    hosts = []
    result = ClaudeMatcher(client=client, system_prompt="system", on_http=hosts.append).aggregate(
        ["Evidence."], [], 1
    )
    assert result.ok
    assert len(client.requests) == 2
    assert hosts == ["api.anthropic.com", "api.anthropic.com"]
    assert "previous tool input was invalid" in client.requests[1]["messages"][0]["content"]


def test_malformed_second_response_fails_closed():
    invalid = {"action": "skip", "existing_lesson_label": None, "candidate": None}
    client = FakeClient([invalid, invalid])
    result = ClaudeMatcher(client=client, system_prompt="system").aggregate(["Evidence."], [], 1)
    assert not result.ok
    assert result.error.startswith("invalid_tool_input:")


def test_bad_stop_reason_and_sdk_error_fail_closed():
    stopped = FakeClient(
        [{"action": "create", "existing_lesson_label": None, "candidate": make_draft()}],
        stop_reason="max_tokens",
    )
    stopped_result = ClaudeMatcher(client=stopped, system_prompt="system").aggregate(["Evidence."], [], 1)
    assert not stopped_result.ok
    assert stopped_result.error == "stop_reason:max_tokens"

    errored = FakeClient([], error=RuntimeError("network details must not escape"))
    error_result = ClaudeMatcher(client=errored, system_prompt="system").aggregate(["Evidence."], [], 1)
    assert not error_result.ok
    assert error_result.error == "RuntimeError"

