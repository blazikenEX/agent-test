"""Connects the classifier to the Evidence Counter for amber entries (option B, Eze 2026-11-04)."""

from pathlib import Path

from evidence_counter.counter import process_entry as count_entry
from evidence_counter.matcher import DEFAULT_MODEL as COUNTER_MODEL
from evidence_counter.matcher import ClaudeMatcher


def counter_handler(
    accepted_path: Path,
    quarantine_path: Path,
    log_path: Path,
    cache_path: Path,
    model: str = COUNTER_MODEL,
):
    """Return a function that sends one amber entry to the Evidence Counter."""
    matcher = ClaudeMatcher(model=model, cache_path=cache_path)

    def on_amber(entry: dict):
        return count_entry(
            entry,
            matcher,
            quarantine_path=quarantine_path,
            accepted_path=accepted_path,
            log_path=log_path,
        )

    return on_amber
