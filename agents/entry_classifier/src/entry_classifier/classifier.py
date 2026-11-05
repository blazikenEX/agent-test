"""Routing logic: give one entry a color and move it.

- green -> Accepted Entries, with `route: "green"`
- amber -> handed straight to the Evidence Counter, which quarantines it (K < 3)
           or moves it to Accepted Entries (K >= 3)
- red   -> nothing written: deletion. Only the decision log keeps ids and codes.

Final color = the most severe of: the local floor (leaks.py), Claude's color, and the
colors implied by Claude's reason codes. If Claude fails, the entry is amber.

The classifier always runs before the counter, one entry at a time (team decision).
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from .judge import Judge
from .leaks import check_entry
from .schema import base_entry, most_severe, validate_entry
from .store import append_jsonl, locked, read_jsonl, write_jsonl

HARD_HIT_REASONS = {
    "email": "identifies_client",
    "url": "identifies_client",
    "phone": "identifies_client",
    "workspace_name": "identifies_client",
    "injection": "prompt_injection",
}


class CounterOutcome(Protocol):
    """What the Evidence Counter returns (its `Decision`)."""

    route: str  # accepted | quarantined | skipped
    reason: str
    k: int
    input_tokens: int
    output_tokens: int


class AmberHandler(Protocol):
    def __call__(self, entry: dict) -> CounterOutcome: ...


@dataclass
class Decision:
    """One log line. Ids, colors and codes only: never client text."""

    entry_id: str
    route: str  # accepted | quarantined | deleted | skipped
    reason: str  # local_check | judged | judge_failed:<code> | duplicate_...
    color: str | None = None
    reasons: list[str] = field(default_factory=list)
    local_hard: list[str] = field(default_factory=list)
    local_soft: list[str] = field(default_factory=list)
    cached: bool = False
    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    # Filled only for amber entries, from the Evidence Counter's own decision.
    k: int | None = None
    counter_reason: str | None = None
    counter_input_tokens: int = 0
    counter_output_tokens: int = 0


def process_entry(
    entry: dict,
    judge: Judge,
    on_amber: AmberHandler,
    accepted_path: Path,
    log_path: Path,
) -> Decision:
    entry = base_entry(validate_entry(entry))

    with locked(accepted_path):
        accepted = read_jsonl(accepted_path)
        decision = _decide(entry, judge, accepted)

        if decision.color == "green":
            accepted.append(entry | {"route": "green"})
            write_jsonl(accepted_path, accepted)
        elif decision.color == "amber":
            # The counter reads and writes Accepted itself; nothing here holds a stale copy.
            outcome = on_amber(entry)
            decision.route = outcome.route  # quarantined | accepted | skipped
            decision.k = outcome.k
            decision.counter_reason = outcome.reason
            decision.counter_input_tokens = outcome.input_tokens
            decision.counter_output_tokens = outcome.output_tokens

        append_jsonl(
            log_path,
            {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds")} | asdict(decision),
        )
    return decision


def _decide(entry: dict, judge: Judge, accepted: list[dict]) -> Decision:
    entry_id = entry["entry_id"]

    # Idempotency: an entry already accepted is never classified again. Amber entries
    # already in quarantine are classified again (from cache) and the counter skips them.
    for record in accepted:
        if record["entry_id"] == entry_id:
            same_content = base_entry(record) == entry
            reason = "duplicate_in_accepted" if same_content else "duplicate_id_conflict_in_accepted"
            return Decision(entry_id=entry_id, route="skipped", reason=reason)

    check = check_entry(entry)
    decision = Decision(
        entry_id=entry_id, route="", reason="", local_hard=check.hard, local_soft=check.soft
    )

    if check.hard:
        decision.reason = "local_check"
        decision.color = "red"
        decision.reasons = sorted({HARD_HIT_REASONS[kind] for kind in check.hard})
    else:
        result = judge.classify(entry["specific"], entry["general"], check.hints)
        decision.cached = result.cached
        decision.model = result.model
        decision.input_tokens = result.input_tokens
        decision.output_tokens = result.output_tokens
        if result.ok:
            decision.reason = "judged"
            decision.color = most_severe(check.floor, result.strict_color)
            decision.reasons = result.reasons
        else:
            decision.reason = f"judge_failed:{result.error}"
            decision.color = "amber"

    # Amber routes are filled in from the counter's answer.
    decision.route = {"green": "accepted", "amber": "", "red": "deleted"}[decision.color]
    return decision
