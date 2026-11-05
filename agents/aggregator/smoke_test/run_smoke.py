"""Real-model smoke test using invented entries only.

Run from agents/aggregator with:
    uv run python smoke_test/run_smoke.py

The first run makes two model calls. Later unchanged runs use Aggregator state and make none.
"""

import json
import sys
from dataclasses import asdict
from pathlib import Path

from aggregator.aggregator import process_files
from aggregator.matcher import ClaudeMatcher


def main() -> int:
    here = Path(__file__).resolve().parent
    output = here / "data"
    summary = process_files(
        matcher=ClaudeMatcher(),
        accepted_path=here / "accepted_entries.jsonl",
        lessons_dir=here / "lessons",
        checker_input_path=output / "checker-input.jsonl",
        metadata_path=output / "candidate-metadata.jsonl",
        state_path=output / "aggregator-state.jsonl",
        decision_log_path=output / "aggregator-decisions.jsonl",
        created_date="2026-06-30",
    )
    print(json.dumps(asdict(summary), sort_keys=True))
    return 0 if summary.failed == 0 else 2


if __name__ == "__main__":
    sys.exit(main())

