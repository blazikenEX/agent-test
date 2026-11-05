"""Routing logic: count independent engagements K and move the entry.

Rule (strict, direct match only): K is the number of distinct workspaces among the
new entry and the quarantined entries the matcher calls `same` when compared
directly with it. No chaining through other entries.
"""

import hashlib
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .matcher import Matcher
from .schema import base_entry, validate_entry
from .store import append_jsonl, locked, read_jsonl, write_jsonl

K_THRESHOLD = 3


@dataclass
class Decision:
    """One log line. Ids, counts and codes only: never client text."""

    entry_id: str
    route: str  # accepted | quarantined | skipped
    reason: str
    k: int = 0
    matched_ids: list[str] = field(default_factory=list)
    cluster_id: str | None = None
    candidates: int = 0
    cached_verdicts: int = 0
    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0


def cluster_id_for(entry_ids: list[str]) -> str:
    """Same members always give the same id."""
    digest = hashlib.sha256("\n".join(sorted(entry_ids)).encode("utf-8")).hexdigest()
    return "c-" + digest[:12]


def process_entry(
    entry: dict,
    matcher: Matcher,
    quarantine_path: Path,
    accepted_path: Path,
    log_path: Path,
    k_threshold: int = K_THRESHOLD,
) -> Decision:
    entry = base_entry(validate_entry(entry))
    entry_id = entry["entry_id"]

    with locked(quarantine_path):
        quarantine = read_jsonl(quarantine_path)
        accepted = read_jsonl(accepted_path)

        decision = _decide(entry, matcher, quarantine, accepted, k_threshold)

        if decision.route == "accepted":
            members = set(decision.matched_ids) | {entry_id}
            moved = [entry] + [q for q in quarantine if q["entry_id"] in members]
            for member in moved:
                accepted.append(
                    base_entry(member)
                    | {"route": "amber", "k": decision.k, "cluster_id": decision.cluster_id}
                )
            quarantine = [q for q in quarantine if q["entry_id"] not in members]
            # Accepted first: a crash in between leaves a duplicate, never a loss.
            write_jsonl(accepted_path, accepted)
            write_jsonl(quarantine_path, quarantine)
        elif decision.route == "quarantined":
            for q in quarantine:
                if q["entry_id"] in decision.matched_ids:
                    q["k"] = max(q.get("k", 1), decision.k)
                    q["matched_ids"] = sorted(set(q.get("matched_ids", [])) | {entry_id})
            quarantine.append(entry | {"k": decision.k, "matched_ids": decision.matched_ids})
            write_jsonl(quarantine_path, quarantine)

        append_jsonl(
            log_path,
            {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds")} | asdict(decision),
        )
    return decision


def _decide(entry, matcher, quarantine, accepted, k_threshold) -> Decision:
    entry_id = entry["entry_id"]

    # Idempotency: an entry already routed is never counted again.
    for name, records in (("accepted", accepted), ("quarantine", quarantine)):
        for record in records:
            if record["entry_id"] == entry_id:
                same_content = base_entry(record) == entry
                reason = f"duplicate_in_{name}" if same_content else f"duplicate_id_conflict_in_{name}"
                return Decision(entry_id=entry_id, route="skipped", reason=reason, k=record.get("k", 0))

    # Entries from the same workspace never add evidence, so they are not compared.
    candidates = [q for q in quarantine if q["workspace"] != entry["workspace"]]
    if not candidates:
        return Decision(entry_id=entry_id, route="quarantined", reason="no_candidates", k=1)

    result = matcher.match(entry["general"], [(q["entry_id"], q["general"]) for q in candidates])
    matched_ids = result.same_ids
    by_id = {q["entry_id"]: q for q in candidates}
    k = len({entry["workspace"]} | {by_id[i]["workspace"] for i in matched_ids})

    decision = Decision(
        entry_id=entry_id,
        route="accepted" if k >= k_threshold else "quarantined",
        reason="k_reached" if k >= k_threshold else "below_k",
        k=k,
        matched_ids=matched_ids,
        candidates=len(candidates),
        cached_verdicts=result.cached,
        model=result.model,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
    )
    if not result.ok:
        decision.reason = f"match_failed:{result.error}"
    if decision.route == "accepted":
        decision.cluster_id = cluster_id_for([entry_id] + matched_ids)
    return decision
