from aggregator.aggregator import build_units, process_files
from aggregator.audit import ActionLogger
from aggregator.lessons import parse_lesson_markdown
from aggregator.matcher import MatchResult
from conftest import FakeMatcher, make_draft, make_entry, read_jsonl, write_jsonl, write_lesson


def run(paths, matcher, *, actions=None):
    return process_files(
        matcher=matcher,
        accepted_path=paths.accepted,
        lessons_dir=paths.lessons,
        checker_input_path=paths.checker,
        metadata_path=paths.metadata,
        state_path=paths.state,
        decision_log_path=paths.decision,
        created_date="2026-06-30",
        actions=actions,
    )


def amber_cluster():
    return [
        make_entry("client-a", 1, "Teams committed before testing.", route="amber", k=3, cluster_id="c-one", specific="SECRET ALPHA"),
        make_entry("client-b", 2, "A commitment preceded a production trial.", route="amber", k=3, cluster_id="c-one", specific="SECRET BETA"),
        make_entry("client-c", 3, "Pressure caused a commitment before testing.", route="amber", k=3, cluster_id="c-one", specific="SECRET GAMMA"),
    ]


def test_groups_amber_cluster_and_keeps_green_entries_separate():
    values = amber_cluster() + [make_entry("client-d", 4, "Another pattern")]
    units = build_units(values)
    assert [(unit.key, unit.entry_ids, unit.evidence_count) for unit in units] == [
        ("amber:c-one", ["client-a#001", "client-b#002", "client-c#003"], 3),
        ("green:client-d#004", ["client-d#004"], 1),
    ]


def test_rejects_duplicate_and_incomplete_amber_input():
    entry = make_entry("client-a", 1, "Pattern")
    try:
        build_units([entry, dict(entry)])
    except ValueError as error:
        assert "duplicate entry_id" in str(error)
    else:
        raise AssertionError("duplicate entry id was accepted")

    try:
        build_units(amber_cluster()[:2])
    except ValueError as error:
        assert "workspace count does not match k" in str(error)
    else:
        raise AssertionError("incomplete amber cluster was accepted")


def test_creates_complete_checker_candidate_and_keeps_provenance_private(paths):
    write_jsonl(paths.accepted, amber_cluster())
    matcher = FakeMatcher([MatchResult(action="create", draft=make_draft(), model="fake")])

    summary = run(paths, matcher)

    assert summary.created == 1
    safe_call = matcher.calls[0]
    assert safe_call[0] == [
        "Teams committed before testing.",
        "A commitment preceded a production trial.",
        "Pressure caused a commitment before testing.",
    ]
    assert safe_call[2] == 3
    assert "SECRET" not in repr(matcher.calls)
    assert "client-a" not in repr(matcher.calls)

    checker = read_jsonl(paths.checker)[0]
    assert set(checker) == {"id", "candidate"}
    candidate = parse_lesson_markdown(checker["candidate"], require_reviewed=False)
    assert candidate["id"] == "L-0001"
    assert candidate["evidence_count"] == 3
    assert candidate["created"] == "2026-06-30"
    assert candidate["reviewed_by_checker"] is False
    assert "client-a" not in checker["candidate"]

    metadata = read_jsonl(paths.metadata)[0]
    assert metadata["source_entry_ids"] == ["client-a#001", "client-b#002", "client-c#003"]
    assert metadata["cluster_id"] == "c-one"
    assert "specific" not in metadata


def test_green_entry_gets_one_evidence_count_and_next_library_id(paths):
    write_lesson(paths.lessons, "L-0004")
    write_jsonl(paths.accepted, [make_entry("client-a", 1, "A general observation.")])
    matcher = FakeMatcher([MatchResult(action="create", draft=make_draft(), model="fake")])
    summary = run(paths, matcher)
    candidate = parse_lesson_markdown(read_jsonl(paths.checker)[0]["candidate"], require_reviewed=False)
    assert summary.created == 1
    assert candidate["id"] == "L-0005"
    assert candidate["evidence_count"] == 1
    assert read_jsonl(paths.metadata)[0]["cluster_id"] is None


def test_existing_library_is_passed_to_matcher_and_skip_emits_no_checker_input(paths):
    write_lesson(paths.lessons, "L-0001")
    write_jsonl(paths.accepted, [make_entry("client-a", 1, "Commit only after validation.")])
    matcher = FakeMatcher([MatchResult(action="skip", existing_lesson_id="L-0001", model="fake")])

    summary = run(paths, matcher)

    assert summary.skipped == 1
    assert matcher.calls[0][1][0]["id"] == "L-0001"
    assert read_jsonl(paths.checker) == []
    assert read_jsonl(paths.metadata) == []
    assert read_jsonl(paths.state)[0]["action"] == "skip"


def test_update_reuses_lesson_id_and_conservative_evidence_count(paths):
    write_lesson(paths.lessons, "L-0001")
    write_jsonl(paths.accepted, amber_cluster())
    matcher = FakeMatcher(
        [MatchResult(action="update", draft=make_draft(title="Improved title"), existing_lesson_id="L-0001", model="fake")]
    )
    summary = run(paths, matcher)
    candidate = parse_lesson_markdown(read_jsonl(paths.checker)[0]["candidate"], require_reviewed=False)
    metadata = read_jsonl(paths.metadata)[0]
    assert summary.updated == 1
    assert candidate["id"] == "L-0001"
    assert candidate["evidence_count"] == 3
    assert candidate["created"] == "2026-01-15"
    assert metadata["action"] == "update"
    assert metadata["existing_lesson_id"] == "L-0001"


def test_multiple_creates_allocate_distinct_ids(paths):
    write_lesson(paths.lessons, "L-0002")
    write_jsonl(
        paths.accepted,
        [make_entry("client-a", 1, "First pattern"), make_entry("client-b", 2, "Second pattern")],
    )
    matcher = FakeMatcher(
        [
            MatchResult(action="create", draft=make_draft(title="First"), model="fake"),
            MatchResult(action="create", draft=make_draft(title="Second"), model="fake"),
        ]
    )
    run(paths, matcher)
    ids = {
        parse_lesson_markdown(item["candidate"], require_reviewed=False)["id"]
        for item in read_jsonl(paths.checker)
    }
    assert ids == {"L-0003", "L-0004"}


def test_rerun_is_idempotent_and_does_not_call_matcher(paths):
    write_jsonl(paths.accepted, [make_entry("client-a", 1, "A general observation.")])
    run(paths, FakeMatcher([MatchResult(action="create", draft=make_draft(), model="fake")]))
    before = (paths.checker.read_bytes(), paths.metadata.read_bytes(), paths.state.read_bytes())

    second = FakeMatcher([MatchResult(action="create", draft=make_draft(title="Unused"), model="fake")])
    summary = run(paths, second)

    assert summary.already_processed == 1
    assert second.calls == []
    assert (paths.checker.read_bytes(), paths.metadata.read_bytes(), paths.state.read_bytes()) == before


def test_library_change_rechecks_unit_and_removes_duplicate_candidate(paths):
    write_jsonl(paths.accepted, [make_entry("client-a", 1, "A general observation.")])
    run(paths, FakeMatcher([MatchResult(action="create", draft=make_draft(), model="fake")]))
    assert len(read_jsonl(paths.checker)) == 1

    write_lesson(paths.lessons, "L-0001")
    second = FakeMatcher([MatchResult(action="skip", existing_lesson_id="L-0001", model="fake")])
    summary = run(paths, second)

    assert summary.skipped == 1
    assert len(second.calls) == 1
    assert read_jsonl(paths.checker) == []
    assert read_jsonl(paths.metadata) == []


def test_failure_creates_no_candidate_or_state_and_can_retry(paths):
    write_jsonl(paths.accepted, [make_entry("client-a", 1, "A general observation.")])
    summary = run(paths, FakeMatcher([MatchResult(ok=False, error="APIError", model="fake")]))
    assert summary.failed == 1
    assert read_jsonl(paths.checker) == []
    assert read_jsonl(paths.metadata) == []
    assert read_jsonl(paths.state) == []

    retry = FakeMatcher([MatchResult(action="create", draft=make_draft(), model="fake")])
    assert run(paths, retry).created == 1
    assert len(retry.calls) == 1


def test_decision_and_action_logs_have_no_entry_or_candidate_text(paths):
    entry = make_entry("client-a", 1, "GENERAL SECRET SENTENCE", specific="SPECIFIC SECRET SENTENCE")
    write_jsonl(paths.accepted, [entry])
    actions = ActionLogger(paths.actions, root=paths.root)
    run(paths, FakeMatcher([MatchResult(action="create", draft=make_draft(), model="fake")]), actions=actions)

    decision = paths.decision.read_text(encoding="utf-8")
    action_records = read_jsonl(paths.actions)
    assert "GENERAL SECRET SENTENCE" not in decision
    assert "SPECIFIC SECRET SENTENCE" not in decision
    assert "Validate before committing" not in decision
    assert {item["action"] for item in action_records} == {"read", "write"}
    assert all(item["actor"] == "aggregator" and item["scope"] == "library" for item in action_records)
    assert any(item["target"].endswith("accepted_entries.jsonl") for item in action_records)
    assert all("GENERAL SECRET SENTENCE" not in repr(item) for item in action_records)

