"""Command line: process one amber entry.

    python -m evidence_counter --entry new.json --data-dir data/

Prints the decision (ids and counts only) as JSON.
"""

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from .counter import K_THRESHOLD, process_entry
from .matcher import DEFAULT_MODEL, ClaudeMatcher


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="evidence_counter", description=__doc__)
    parser.add_argument("--entry", type=Path, required=True, help="JSON file with one entry")
    parser.add_argument("--data-dir", type=Path, default=Path("data"), help="default for the paths below")
    parser.add_argument("--quarantine", type=Path, help="default: <data-dir>/quarantined_entries.jsonl")
    parser.add_argument("--accepted", type=Path, help="default: <data-dir>/accepted_entries.jsonl")
    parser.add_argument("--log", type=Path, help="default: <data-dir>/evidence_counter_decisions.jsonl")
    parser.add_argument("--cache", type=Path, help="default: <data-dir>/verdicts.cache.json")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--threshold", type=int, default=K_THRESHOLD)
    args = parser.parse_args(argv)

    data = args.data_dir
    try:
        entry = json.loads(args.entry.read_text(encoding="utf-8"))
        decision = process_entry(
            entry,
            matcher=ClaudeMatcher(model=args.model, cache_path=args.cache or data / "verdicts.cache.json"),
            quarantine_path=args.quarantine or data / "quarantined_entries.jsonl",
            accepted_path=args.accepted or data / "accepted_entries.jsonl",
            log_path=args.log or data / "evidence_counter_decisions.jsonl",
            k_threshold=args.threshold,
        )
    except (OSError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(json.dumps(asdict(decision), sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
