"""Real smoke test of the Evidence Counter against Claude, with invented entries only.

Runs every entry in entries.jsonl through the counter, in order, and checks each
decision against the expected route and K. Data goes to smoke_test/data/ (private).
Verdicts are cached, so re-running costs nothing unless data/ is deleted.

    cd agents/evidence_counter && uv run python smoke_test/run_smoke.py
"""

import json
from pathlib import Path

from evidence_counter.counter import process_entry
from evidence_counter.matcher import ClaudeMatcher

HERE = Path(__file__).parent
DATA = HERE / "data"

# entry_id -> (expected route, expected K)
EXPECTED = {
    "acme#001": ("quarantined", 1),  # empty quarantine, no API call
    "beta#001": ("quarantined", 1),  # different topic (hiring)
    "gamma#001": ("quarantined", 2),  # same pattern as acme#001
    "delta#001": ("quarantined", 1),  # related (deadlines) but different cause: scope creep
    "zeta#001": ("quarantined", 1),  # prompt injection: must not match anything
    "acme#002": ("quarantined", 2),  # matches gamma, but acme already counted: K stays 2
    "epsilon#001": ("accepted", 3),  # acme + gamma + epsilon: three clients
}


def main() -> int:
    matcher = ClaudeMatcher(cache_path=DATA / "verdicts.cache.json")
    failures = 0
    total_in = total_out = 0
    for line in (HERE / "entries.jsonl").read_text(encoding="utf-8").splitlines():
        entry = json.loads(line)
        d = process_entry(
            entry,
            matcher,
            quarantine_path=DATA / "quarantined_entries.jsonl",
            accepted_path=DATA / "accepted_entries.jsonl",
            log_path=DATA / "evidence_counter_decisions.jsonl",
        )
        total_in += d.input_tokens
        total_out += d.output_tokens
        want = EXPECTED[d.entry_id]
        ok = (d.route, d.k) == want or d.route == "skipped"
        failures += not ok
        print(
            f"{'OK  ' if ok else 'FAIL'} {d.entry_id:12} route={d.route:11} K={d.k} "
            f"matched={d.matched_ids} reason={d.reason} expected={want}"
        )
    print(f"\ntokens: input={total_in} output={total_out}   failures: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
