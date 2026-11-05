"""Real smoke test of the Entry Classifier against Claude, with invented entries only.

Runs every entry in entries.jsonl through the classifier (amber entries go on to the
Evidence Counter) and checks each color against the allowed colors. Data goes to smoke_test/data/ (private). Answers are cached, so
re-running costs nothing unless data/ is deleted.

    cd agents/entry_classifier && uv run python smoke_test/run_smoke.py
"""

import json
from pathlib import Path

from entry_classifier.chain import counter_handler
from entry_classifier.classifier import process_entry
from entry_classifier.judge import ClaudeJudge

HERE = Path(__file__).parent
DATA = HERE / "data"

# entry_id -> (allowed colors, classifier API call expected, (counter route, K) or None)
EXPECTED = {
    "acme#001": ({"green"}, True, None),  # widely known practice
    "beta#001": ({"amber"}, True, ("quarantined", 1)),  # empty quarantine, no counter call
    "falconbay#001": ({"red"}, False, None),  # workspace name in general: local check
    "gamma#001": ({"red"}, False, None),  # obvious injection: local check
    "delta#001": ({"red"}, True, None),  # no names, but a unique combination + year
    "epsilon#001": ({"red"}, True, None),  # a bare fact, not a lesson
    "zeta#001": ({"red", "amber"}, True, None),  # product name from specific: never green
    "eta#001": ({"red"}, True, None),  # injection the regex does not catch
    "theta#001": ({"amber"}, True, ("quarantined", 2)),  # same pattern as beta#001
    "beta#002": ({"amber"}, True, ("quarantined", 2)),  # same pattern, but beta already counted
    "kappa#001": ({"amber"}, True, ("quarantined", 1)),  # event from one engagement, even if textbook;
    # deadline slip with a different cause (scope creep): related, not same
    "iota#001": ({"amber"}, True, ("accepted", 3)),  # beta + theta + iota: three clients
}


def main() -> int:
    judge = ClaudeJudge(cache_path=DATA / "classifier.cache.json")
    on_amber = counter_handler(
        accepted_path=DATA / "accepted_entries.jsonl",
        quarantine_path=DATA / "quarantined_entries.jsonl",
        log_path=DATA / "evidence_counter_decisions.jsonl",
        cache_path=DATA / "verdicts.cache.json",
    )
    failures = 0
    total_in = total_out = 0
    for line in (HERE / "entries.jsonl").read_text(encoding="utf-8").splitlines():
        d = process_entry(
            json.loads(line),
            judge,
            on_amber,
            accepted_path=DATA / "accepted_entries.jsonl",
            log_path=DATA / "entry_classifier_decisions.jsonl",
        )
        total_in += d.input_tokens + d.counter_input_tokens
        total_out += d.output_tokens + d.counter_output_tokens
        allowed, call_expected, counter_expected = EXPECTED[d.entry_id]
        called = d.model is not None
        counter_ok = counter_expected is None or (d.route, d.k) == counter_expected
        ok = d.route == "skipped" or (d.color in allowed and called == call_expected and counter_ok)
        failures += not ok
        print(
            f"{'OK  ' if ok else 'FAIL'} {d.entry_id:14} color={d.color!s:6} route={d.route:11} K={d.k!s:4} "
            f"reasons={d.reasons} hard={d.local_hard} soft={d.local_soft} reason={d.reason} "
            f"cached={d.cached} expected={sorted(allowed)} {counter_expected or ''}"
        )
    print(f"\ntokens: input={total_in} output={total_out}   failures: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
