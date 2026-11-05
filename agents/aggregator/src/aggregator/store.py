"""Private, atomic JSONL storage for Aggregator inputs and outputs."""

import contextlib
import fcntl
import json
import os
import tempfile
from pathlib import Path

FILE_MODE = 0o600
DIR_MODE = 0o700


def check_private_path(path: Path) -> None:
    if "submission" in path.resolve().parts:
        raise ValueError(f"refusing to write private data under submission/: {path}")


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(mode=DIR_MODE, parents=True, exist_ok=True)


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    records = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"{path.name}: invalid JSON on line {line_number}") from error
            if not isinstance(value, dict):
                raise ValueError(f"{path.name}: line {line_number} must be a JSON object")
            records.append(value)
    return records


def write_jsonl(path: Path, records: list[dict]) -> None:
    check_private_path(path)
    ensure_parent(path)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        os.fchmod(descriptor, FILE_MODE)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)
        raise


def append_jsonl(path: Path, record: dict) -> None:
    check_private_path(path)
    ensure_parent(path)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, FILE_MODE)
    with os.fdopen(descriptor, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


@contextlib.contextmanager
def locked(path: Path):
    """Prevent concurrent Aggregator runs from racing on output and state files."""
    check_private_path(path)
    ensure_parent(path)
    lock_path = path.with_name(path.name + ".lock")
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, FILE_MODE)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)

