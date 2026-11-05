"""Command line for the Entry Aggregator."""

import argparse
import json
import sys
from dataclasses import asdict
from datetime import date
from pathlib import Path

from .aggregator import process_files
from .audit import ActionLogger
from .matcher import DEFAULT_MODEL, ClaudeMatcher


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aggregator", description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="base for action-log paths")
    parser.add_argument("--accepted", type=Path, help="default: <data-dir>/accepted_entries.jsonl")
    parser.add_argument("--lessons-dir", type=Path, help="default: <data-dir>/lessons")
    parser.add_argument("--checker-input", type=Path, help="default: <data-dir>/checker-input.jsonl")
    parser.add_argument("--metadata", type=Path, help="default: <data-dir>/candidate-metadata.jsonl")
    parser.add_argument("--state", type=Path, help="default: <data-dir>/aggregator-state.jsonl")
    parser.add_argument("--decision-log", type=Path, help="default: <data-dir>/aggregator-decisions.jsonl")
    parser.add_argument("--actions-log", type=Path, help="default: <data-dir>/actions.log.jsonl")
    parser.add_argument("--as-of-date", default=date.today().isoformat(), help="YYYY-MM-DD")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    args = parser.parse_args(argv)

    data = args.data_dir
    actions = ActionLogger(args.actions_log or data / "actions.log.jsonl", root=args.root)
    try:
        matcher = ClaudeMatcher(model=args.model, on_http=actions.http)
        summary = process_files(
            matcher=matcher,
            accepted_path=args.accepted or data / "accepted_entries.jsonl",
            lessons_dir=args.lessons_dir or data / "lessons",
            checker_input_path=args.checker_input or data / "checker-input.jsonl",
            metadata_path=args.metadata or data / "candidate-metadata.jsonl",
            state_path=args.state or data / "aggregator-state.jsonl",
            decision_log_path=args.decision_log or data / "aggregator-decisions.jsonl",
            created_date=args.as_of_date,
            actions=actions,
        )
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(json.dumps(asdict(summary), sort_keys=True))
    return 0 if summary.failed == 0 else 2


if __name__ == "__main__":
    sys.exit(main())

