"""Group accepted entries and turn each unprocessed unit into Checker input."""

import hashlib
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

from .audit import NullActionLogger
from .lessons import lesson_text_for_model, next_lesson_id, read_lesson_library, render_lesson_markdown
from .matcher import PROMPT_VERSION, MatchResult, Matcher
from .schema import (
    AcceptedEntry,
    CandidateMetadata,
    CheckerInput,
    Lesson,
    validate_accepted_entry,
    validate_candidate_metadata,
    validate_checker_input,
)
from .store import append_jsonl, locked, read_jsonl, write_jsonl


@dataclass(frozen=True)
class Unit:
    key: str
    unit_id: str
    route: str
    cluster_id: str | None
    entries: tuple[AcceptedEntry, ...]

    @property
    def entry_ids(self) -> list[str]:
        return sorted(item["entry_id"] for item in self.entries)

    @property
    def general_entries(self) -> list[str]:
        return [item["general"] for item in sorted(self.entries, key=lambda item: item["entry_id"])]

    @property
    def evidence_count(self) -> int:
        return len({item["workspace"] for item in self.entries})


@dataclass
class Decision:
    unit_id: str
    action: str
    reason: str
    entry_ids: list[str] = field(default_factory=list)
    candidate_id: str | None = None
    lesson_id: str | None = None
    existing_lesson_id: str | None = None
    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class Summary:
    units: int = 0
    created: int = 0
    updated: int = 0
    skipped: int = 0
    already_processed: int = 0
    failed: int = 0


def build_units(values: list[dict]) -> list[Unit]:
    entries = [validate_accepted_entry(value) for value in values]
    seen: set[str] = set()
    amber: dict[str, list[AcceptedEntry]] = {}
    green: list[AcceptedEntry] = []
    for entry in entries:
        if entry["entry_id"] in seen:
            raise ValueError(f"duplicate entry_id: {entry['entry_id']}")
        seen.add(entry["entry_id"])
        if entry["route"] == "amber":
            amber.setdefault(entry["cluster_id"], []).append(entry)  # type: ignore[arg-type]
        else:
            green.append(entry)

    for cluster_id, group in amber.items():
        k_values = {entry["k"] for entry in group}
        if len(k_values) != 1:
            raise ValueError(f"amber cluster has inconsistent k: {cluster_id}")
        claimed_k = next(iter(k_values))
        actual_k = len({entry["workspace"] for entry in group})
        if claimed_k != actual_k:
            raise ValueError(f"amber cluster workspace count does not match k: {cluster_id}")

    units = [
        Unit(
            key=f"amber:{cluster_id}",
            unit_id=cluster_id,
            route="amber",
            cluster_id=cluster_id,
            entries=tuple(sorted(group, key=lambda item: item["entry_id"])),
        )
        for cluster_id, group in amber.items()
    ]
    units.extend(
        Unit(
            key=f"green:{entry['entry_id']}",
            unit_id=entry["entry_id"],
            route="green",
            cluster_id=None,
            entries=(entry,),
        )
        for entry in green
    )
    return sorted(units, key=lambda unit: unit.key)


def process_files(
    matcher: Matcher,
    accepted_path: Path,
    lessons_dir: Path,
    checker_input_path: Path,
    metadata_path: Path,
    state_path: Path,
    decision_log_path: Path,
    created_date: str,
    actions=None,
) -> Summary:
    """Process accepted units into exact Checker input records.

    `specific`, workspace, source files, ids, routing data and cluster ids never reach matcher.
    Candidate Markdown contains no provenance; private metadata retains the source entry ids.
    """
    try:
        date.fromisoformat(created_date)
    except ValueError as error:
        raise ValueError("created_date must be YYYY-MM-DD") from error
    actions = actions or NullActionLogger()

    accepted = _read_jsonl(accepted_path, actions)
    lessons = read_lesson_library(lessons_dir, on_read=actions.read)
    units = build_units(accepted)
    summary = Summary(units=len(units))
    lesson_by_id = {lesson["id"]: lesson for lesson in lessons}

    with locked(state_path):
        checker_inputs = [validate_checker_input(value) for value in _read_jsonl(checker_input_path, actions)]
        metadata = [validate_candidate_metadata(value) for value in _read_jsonl(metadata_path, actions)]
        state = _read_jsonl(state_path, actions)
        _require_unique(checker_inputs, "id")
        _require_unique(metadata, "candidate_id")
        state_by_key = _validate_state(state)
        checker_by_id = {item["id"]: item for item in checker_inputs}
        metadata_by_id = {item["candidate_id"]: item for item in metadata}
        reserved_lesson_ids = set(lesson_by_id) | {item["lesson_id"] for item in metadata}

        for unit in units:
            fingerprint = _unit_fingerprint(unit, lessons)
            previous = state_by_key.get(unit.key)
            if previous is not None and previous["fingerprint"] == fingerprint:
                summary.already_processed += 1
                _log_decision(
                    decision_log_path,
                    Decision(
                        unit_id=unit.unit_id,
                        action="skipped",
                        reason="already_processed",
                        entry_ids=unit.entry_ids,
                        candidate_id=previous.get("candidate_id"),
                        lesson_id=previous.get("lesson_id"),
                        existing_lesson_id=previous.get("existing_lesson_id"),
                    ),
                    actions,
                )
                continue

            result = matcher.aggregate(unit.general_entries, lessons, unit.evidence_count)
            if not result.ok:
                summary.failed += 1
                _log_decision(
                    decision_log_path,
                    _decision_for(unit, result),
                    actions,
                )
                continue

            old_candidate_id = previous.get("candidate_id") if previous is not None else None
            if old_candidate_id:
                old_metadata = metadata_by_id.pop(old_candidate_id, None)
                checker_by_id.pop(old_candidate_id, None)
                if old_metadata is not None and old_metadata["action"] == "create":
                    reserved_lesson_ids.discard(old_metadata["lesson_id"])

            if result.action == "skip":
                summary.skipped += 1
                candidate_id = None
                lesson_id = result.existing_lesson_id
                metadata_record = None
            else:
                candidate_id = _candidate_id(fingerprint, result)
                lesson_id, evidence_count, lesson_created = _lesson_identity(
                    result,
                    unit,
                    lesson_by_id,
                    reserved_lesson_ids,
                    created_date,
                )
                candidate_markdown = render_lesson_markdown(
                    lesson_id,
                    result.draft,  # type: ignore[arg-type]
                    evidence_count=evidence_count,
                    created=lesson_created,
                    reviewed_by_checker=False,
                )
                checker_record: CheckerInput = {"id": candidate_id, "candidate": candidate_markdown}
                metadata_record: CandidateMetadata = {
                    "candidate_id": candidate_id,
                    "action": result.action,  # type: ignore[typeddict-item]
                    "lesson_id": lesson_id,
                    "existing_lesson_id": result.existing_lesson_id,
                    "source_unit_id": unit.unit_id,
                    "source_entry_ids": unit.entry_ids,
                    "route": unit.route,  # type: ignore[typeddict-item]
                    "cluster_id": unit.cluster_id,
                    "evidence_count": evidence_count,
                    "candidate_sha256": _sha(candidate_markdown),
                }
                validate_checker_input(checker_record)
                validate_candidate_metadata(metadata_record)
                checker_by_id[candidate_id] = checker_record
                metadata_by_id[candidate_id] = metadata_record
                reserved_lesson_ids.add(lesson_id)
                if result.action == "create":
                    summary.created += 1
                else:
                    summary.updated += 1

            state_by_key[unit.key] = {
                "unit_key": unit.key,
                "unit_id": unit.unit_id,
                "fingerprint": fingerprint,
                "action": result.action,
                "candidate_id": candidate_id,
                "lesson_id": lesson_id,
                "existing_lesson_id": result.existing_lesson_id,
            }
            _log_decision(
                decision_log_path,
                _decision_for(unit, result, candidate_id=candidate_id, lesson_id=lesson_id),
                actions,
            )

        # Checker input first: a crash between writes may cause safe re-evaluation, never lost output.
        actions.write(checker_input_path)
        write_jsonl(checker_input_path, sorted(checker_by_id.values(), key=lambda item: item["id"]))
        actions.write(metadata_path)
        write_jsonl(metadata_path, sorted(metadata_by_id.values(), key=lambda item: item["candidate_id"]))
        actions.write(state_path)
        write_jsonl(state_path, sorted(state_by_key.values(), key=lambda item: item["unit_key"]))
    return summary


def _lesson_identity(
    result: MatchResult,
    unit: Unit,
    lesson_by_id: dict[str, Lesson],
    reserved_lesson_ids: set[str],
    created_date: str,
) -> tuple[str, int, str]:
    if result.action == "create":
        return next_lesson_id(reserved_lesson_ids), unit.evidence_count, created_date
    if result.action == "update" and result.existing_lesson_id in lesson_by_id:
        existing = lesson_by_id[result.existing_lesson_id]
        # Existing provenance is intentionally absent from the shared lesson. `max` is the safe
        # lower bound when we cannot prove whether the old and new engagement sets overlap.
        return (
            existing["id"],
            max(existing["evidence_count"], unit.evidence_count),
            existing["created"],
        )
    raise ValueError("matcher selected an unknown existing lesson")


def _candidate_id(fingerprint: str, result: MatchResult) -> str:
    if result.action not in ("create", "update") or result.draft is None:
        raise ValueError("cannot build candidate from matcher result")
    digest_input = "\n".join([fingerprint, result.action, result.existing_lesson_id or ""])
    return "candidate-" + _sha(digest_input)[:16]


def _decision_for(
    unit: Unit,
    result: MatchResult,
    *,
    candidate_id: str | None = None,
    lesson_id: str | None = None,
) -> Decision:
    return Decision(
        unit_id=unit.unit_id,
        action=result.action or "failed",
        reason="model_decision" if result.ok else f"match_failed:{result.error}",
        entry_ids=unit.entry_ids,
        candidate_id=candidate_id,
        lesson_id=lesson_id,
        existing_lesson_id=result.existing_lesson_id,
        model=result.model,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
    )


def _unit_fingerprint(unit: Unit, lessons: list[Lesson]) -> str:
    parts = [PROMPT_VERSION, unit.key]
    parts.extend(f"{entry['entry_id']}:{_sha(entry['general'])}" for entry in unit.entries)
    parts.extend(
        f"lesson:{lesson['id']}:{_sha(lesson_text_for_model(lesson))}"
        for lesson in sorted(lessons, key=lambda item: item["id"])
    )
    return _sha("\n".join(parts))


def _validate_state(records: list[dict]) -> dict[str, dict]:
    by_key = {}
    for record in records:
        if not isinstance(record.get("unit_key"), str) or not isinstance(record.get("fingerprint"), str):
            raise ValueError("invalid aggregator state record")
        if record["unit_key"] in by_key:
            raise ValueError("duplicate unit_key in aggregator state")
        by_key[record["unit_key"]] = record
    return by_key


def _require_unique(records: list[dict], field: str) -> None:
    values = [item[field] for item in records]
    if len(values) != len(set(values)):
        raise ValueError(f"duplicate {field}")


def _read_jsonl(path: Path, actions) -> list[dict]:
    if path.exists():
        actions.read(path)
    return read_jsonl(path)


def _log_decision(path: Path, decision: Decision, actions) -> None:
    actions.write(path)
    append_jsonl(
        path,
        {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")}
        | asdict(decision),
    )


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()

