"""JSONL files for Accepted entries.

Files are private (0600, parent dirs 0700) and replaced atomically so a crash
never leaves a half-written file.
"""

import contextlib
import fcntl
import json
import os
import tempfile
from pathlib import Path

FILE_MODE = 0o600
DIR_MODE = 0o700


def check_private_path(path: Path) -> None:
    """Refuse paths that look like shared output folders."""
    if "submission" in Path(path).resolve().parts:
        raise ValueError(f"refusing to write private data under submission/: {path}")


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(mode=DIR_MODE, parents=True, exist_ok=True)


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    records = []
    with path.open(encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise ValueError(f"{path.name}: invalid JSON on line {line_no}") from e
    return records


def write_jsonl(path: Path, records: list[dict]) -> None:
    """Write records atomically: temp file in the same folder, then rename."""
    check_private_path(path)
    ensure_parent(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        os.fchmod(fd, FILE_MODE)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for record in records:
                f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise


def append_jsonl(path: Path, record: dict) -> None:
    check_private_path(path)
    ensure_parent(path)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, FILE_MODE)
    with os.fdopen(fd, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


@contextlib.contextmanager
def locked(path: Path):
    """Exclusive lock so two runs never write the same files at once."""
    check_private_path(path)
    ensure_parent(path)
    lock_path = path.with_name(path.name + ".lock")
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, FILE_MODE)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
