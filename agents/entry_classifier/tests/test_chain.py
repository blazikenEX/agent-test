"""Classifier + real Evidence Counter code, with fake judge and fake matcher (no API)."""

from conftest import FakeJudge, make_entry

from entry_classifier.chain import counter_handler
from entry_classifier.classifier import process_entry
from entry_classifier.store import read_jsonl
from evidence_counter.matcher import MatchResult

GREEN = "Agree on a written definition of done before the first sprint."


class TopicMatcher:
    """`same` when the text before ':' is equal."""

    def match(self, new_general, candidates):
        topic = new_general.split(":")[0]
        verdicts = {cid: "same" if g.split(":")[0] == topic else "different" for cid, g in candidates}
        return MatchResult(verdicts=verdicts, model="fake")


def setup(paths, monkeypatch):
    monkeypatch.setattr("entry_classifier.chain.ClaudeMatcher", lambda **kw: TopicMatcher())
    on_amber = counter_handler(paths.accepted, paths.quarantine, paths.counter_log, paths.accepted.parent / "v.json")
    j = FakeJudge({GREEN: ("green", ["common_practice"])})  # anything else: amber
    return lambda entry: process_entry(entry, j, on_amber, paths.accepted, paths.log)


def test_three_workspaces_leave_quarantine(paths, monkeypatch):
    run = setup(paths, monkeypatch)
    green = run(make_entry("acme", 1, GREEN))
    first = run(make_entry("acme", 2, "deadline: a date was promised before load tests"))
    same_ws = run(make_entry("acme", 3, "deadline: the date came before testing"))
    second = run(make_entry("beta", 1, "deadline: launch date set with no scale test"))
    other = run(make_entry("gamma", 1, "hiring: a key role stayed open for months"))
    third = run(make_entry("delta", 1, "deadline: stakeholders got a date before testing"))

    assert (green.route, green.k) == ("accepted", None)
    assert [(d.route, d.k) for d in (first, same_ws, second, other)] == [
        ("quarantined", 1), ("quarantined", 1), ("quarantined", 2), ("quarantined", 1),
    ]
    assert (third.color, third.route, third.k) == ("amber", "accepted", 3)

    accepted = read_jsonl(paths.accepted)
    assert [r["route"] for r in accepted][0] == "green"
    amber_ids = sorted(r["entry_id"] for r in accepted if r["route"] == "amber")
    assert amber_ids == ["acme#002", "acme#003", "beta#001", "delta#001"]
    assert [q["entry_id"] for q in read_jsonl(paths.quarantine)] == ["gamma#001"]


def test_rerun_changes_nothing(paths, monkeypatch):
    run = setup(paths, monkeypatch)
    entries = [
        make_entry("acme", 1, GREEN),
        make_entry("beta", 1, "deadline: a date was promised before load tests"),
        make_entry("gamma", 1, "deadline: launch date set with no scale test"),
    ]
    for e in entries:
        run(e)
    snapshot = (paths.accepted.read_bytes(), paths.quarantine.read_bytes())

    decisions = [run(e) for e in entries]
    assert [d.route for d in decisions] == ["skipped", "skipped", "skipped"]
    assert decisions[1].counter_reason == "duplicate_in_quarantine"
    assert (paths.accepted.read_bytes(), paths.quarantine.read_bytes()) == snapshot
