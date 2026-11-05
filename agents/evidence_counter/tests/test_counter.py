import os
import stat

import anthropic
import httpx2
import pytest
from conftest import FakeClient, TopicMatcher, make_entry

from evidence_counter.counter import process_entry
from evidence_counter.matcher import ClaudeMatcher
from evidence_counter.store import read_jsonl

DEADLINE_A = "deadline: a team promised a date before testing at scale"
DEADLINE_B = "deadline: a delivery date was committed with no load test"
DEADLINE_C = "deadline: dates were promised before the system was tested"
HIRING = "hiring: a key role stayed open for months and slowed delivery"


def run(entry, matcher, paths):
    return process_entry(entry, matcher, paths.quarantine, paths.accepted, paths.log)


def ids(records):
    return sorted(r["entry_id"] for r in records)


def test_empty_quarantine_stays_quarantined(paths):
    matcher = TopicMatcher()
    d = run(make_entry("acme", 1, DEADLINE_A), matcher, paths)
    assert (d.route, d.k, d.reason) == ("quarantined", 1, "no_candidates")
    assert matcher.calls == []
    assert ids(read_jsonl(paths.quarantine)) == ["acme#001"]
    assert read_jsonl(paths.accepted) == []


def test_partial_match_k2(paths):
    matcher = TopicMatcher()
    run(make_entry("acme", 1, DEADLINE_A), matcher, paths)
    d = run(make_entry("beta", 1, DEADLINE_B), matcher, paths)
    assert (d.route, d.k, d.matched_ids) == ("quarantined", 2, ["acme#001"])
    q = {r["entry_id"]: r for r in read_jsonl(paths.quarantine)}
    assert q["acme#001"]["k"] == 2 and q["acme#001"]["matched_ids"] == ["beta#001"]
    assert q["beta#001"]["k"] == 2


def test_full_match_k3_moves_cluster_to_accepted(paths):
    matcher = TopicMatcher()
    run(make_entry("acme", 1, DEADLINE_A), matcher, paths)
    run(make_entry("beta", 1, DEADLINE_B), matcher, paths)
    run(make_entry("gamma", 1, HIRING), matcher, paths)
    d = run(make_entry("gamma", 2, DEADLINE_C), matcher, paths)

    assert (d.route, d.k, d.reason) == ("accepted", 3, "k_reached")
    accepted = read_jsonl(paths.accepted)
    assert ids(accepted) == ["acme#001", "beta#001", "gamma#002"]
    assert {r["cluster_id"] for r in accepted} == {d.cluster_id}
    assert all(r["route"] == "amber" and r["k"] == 3 for r in accepted)
    assert all("matched_ids" not in r for r in accepted)
    # The unrelated entry stays waiting.
    assert ids(read_jsonl(paths.quarantine)) == ["gamma#001"]


def test_same_workspace_three_times_stays_k1(paths):
    matcher = TopicMatcher()
    for n, text in enumerate([DEADLINE_A, DEADLINE_B, DEADLINE_C], start=1):
        d = run(make_entry("acme", n, text), matcher, paths)
        assert (d.route, d.k) == ("quarantined", 1)
    assert matcher.calls == []  # same-workspace entries are never compared
    assert read_jsonl(paths.accepted) == []


def test_two_entries_from_one_workspace_count_once(paths):
    matcher = TopicMatcher()
    run(make_entry("acme", 1, DEADLINE_A), matcher, paths)
    run(make_entry("acme", 2, DEADLINE_B), matcher, paths)
    d = run(make_entry("beta", 1, DEADLINE_C), matcher, paths)
    assert d.k == 2 and d.matched_ids == ["acme#001", "acme#002"]
    assert d.route == "quarantined"


def test_strict_rule_no_chaining(paths):
    # acme~beta and beta~gamma, but acme is NOT the same as gamma: K must stay 2.
    matcher = TopicMatcher(overrides={(DEADLINE_C, DEADLINE_A): "related"})
    run(make_entry("acme", 1, DEADLINE_A), matcher, paths)
    run(make_entry("beta", 1, DEADLINE_B), matcher, paths)
    d = run(make_entry("gamma", 1, DEADLINE_C), matcher, paths)
    assert (d.route, d.k, d.matched_ids) == ("quarantined", 2, ["beta#001"])


def test_rerun_gives_identical_files(paths):
    entries = [
        make_entry("acme", 1, DEADLINE_A),
        make_entry("beta", 1, DEADLINE_B),
        make_entry("gamma", 1, HIRING),
        make_entry("gamma", 2, DEADLINE_C),
    ]
    for e in entries:
        run(e, TopicMatcher(), paths)
    before = (paths.quarantine.read_bytes(), paths.accepted.read_bytes())

    matcher = TopicMatcher()
    decisions = [run(e, matcher, paths) for e in entries]
    assert all(d.route == "skipped" for d in decisions)
    assert matcher.calls == []
    assert (paths.quarantine.read_bytes(), paths.accepted.read_bytes()) == before


def test_same_id_with_different_content_is_flagged(paths):
    run(make_entry("acme", 1, DEADLINE_A), TopicMatcher(), paths)
    d = run(make_entry("acme", 1, HIRING), TopicMatcher(), paths)
    assert (d.route, d.reason) == ("skipped", "duplicate_id_conflict_in_quarantine")


def test_api_failure_fails_closed(paths):
    run(make_entry("acme", 1, DEADLINE_A), TopicMatcher(), paths)
    run(make_entry("beta", 1, DEADLINE_B), TopicMatcher(), paths)

    error = anthropic.APIConnectionError(request=httpx2.Request("POST", "https://example.invalid"))
    matcher = ClaudeMatcher(client=FakeClient(error=error), cache_path=paths.cache)
    d = run(make_entry("gamma", 1, DEADLINE_C), matcher, paths)

    assert (d.route, d.k, d.matched_ids) == ("quarantined", 1, [])
    assert d.reason == "match_failed:APIConnectionError"
    assert read_jsonl(paths.accepted) == []
    assert not paths.cache.exists()  # failures are never cached


def test_full_flow_with_claude_matcher(paths):
    client = FakeClient()
    matcher = ClaudeMatcher(client=client, cache_path=paths.cache)
    run(make_entry("acme", 1, DEADLINE_A), matcher, paths)
    run(make_entry("beta", 1, DEADLINE_B), matcher, paths)
    d = run(make_entry("gamma", 1, DEADLINE_C), matcher, paths)
    assert (d.route, d.k) == ("accepted", 3)
    assert d.input_tokens == 100 and d.output_tokens == 20


def test_no_client_text_in_log_or_prompt(paths):
    client = FakeClient()
    matcher = ClaudeMatcher(client=client, cache_path=paths.cache)
    entries = [
        make_entry("acme", 1, DEADLINE_A),
        make_entry("beta", 1, DEADLINE_B),
        make_entry("gamma", 1, DEADLINE_C),
    ]
    for e in entries:
        run(e, matcher, paths)

    sent = repr(client.requests)
    log = paths.log.read_text()
    cache = paths.cache.read_text()
    for e in entries:
        assert e["specific"] not in sent  # specific never leaves its workspace
        assert e["workspace"] not in sent  # the model never learns which client
        assert e["specific"] not in log and e["general"] not in log
        assert e["general"] not in cache and e["workspace"] not in cache


def test_invalid_entry_touches_nothing(paths):
    bad = make_entry("acme", 1, DEADLINE_A)
    del bad["general"]
    with pytest.raises(ValueError, match="missing field: general"):
        run(bad, TopicMatcher(), paths)
    assert not paths.quarantine.exists() and not paths.accepted.exists()


def test_files_are_private(paths):
    for n, ws in enumerate(["acme", "beta", "gamma"], start=1):
        run(make_entry(ws, n, DEADLINE_A), TopicMatcher(), paths)
    for path in (paths.quarantine, paths.accepted, paths.log):
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(paths.quarantine.parent).st_mode) == 0o700


def test_refuses_submission_folder(tmp_path):
    sub = tmp_path / "submission"
    with pytest.raises(ValueError, match="submission"):
        process_entry(
            make_entry("acme", 1, DEADLINE_A),
            TopicMatcher(),
            sub / "q.jsonl",
            sub / "a.jsonl",
            sub / "log.jsonl",
        )
