"""Command line: classify one entry. Amber entries go straight to the Evidence Counter.

    python -m entry_classifier --entry new.json --data-dir data/

Prints the decision (ids, colors and codes only) as JSON.
"""

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from .chain import COUNTER_MODEL, counter_handler
from .classifier import process_entry
from .judge import DEFAULT_MODEL, ClaudeJudge


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="entry_classifier", description=__doc__)
    parser.add_argument("--entry", type=Path, required=True, help="JSON file with one entry")
    parser.add_argument("--data-dir", type=Path, default=Path("data"), help="default for the paths below")
    parser.add_argument("--accepted", type=Path, help="default: <data-dir>/accepted_entries.jsonl")
    parser.add_argument("--quarantine", type=Path, help="default: <data-dir>/quarantined_entries.jsonl")
    parser.add_argument("--log", type=Path, help="default: <data-dir>/entry_classifier_decisions.jsonl")
    parser.add_argument("--counter-log", type=Path, help="default: <data-dir>/evidence_counter_decisions.jsonl")
    parser.add_argument("--cache", type=Path, help="default: <data-dir>/classifier.cache.json")
    parser.add_argument("--counter-cache", type=Path, help="default: <data-dir>/verdicts.cache.json")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--counter-model", default=COUNTER_MODEL)
    args = parser.parse_args(argv)

    data = args.data_dir
    accepted = args.accepted or data / "accepted_entries.jsonl"
    try:
        entry = json.loads(args.entry.read_text(encoding="utf-8"))
        decision = process_entry(
            entry,
            judge=ClaudeJudge(model=args.model, cache_path=args.cache or data / "classifier.cache.json"),
            on_amber=counter_handler(
                accepted_path=accepted,
                quarantine_path=args.quarantine or data / "quarantined_entries.jsonl",
                log_path=args.counter_log or data / "evidence_counter_decisions.jsonl",
                cache_path=args.counter_cache or data / "verdicts.cache.json",
                model=args.counter_model,
            ),
            accepted_path=accepted,
            log_path=args.log or data / "entry_classifier_decisions.jsonl",
        )
    except (OSError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(json.dumps(asdict(decision), sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
