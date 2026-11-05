"""Submission-compatible action logging without client text."""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from .store import DIR_MODE, FILE_MODE


class ActionLogger:
    def __init__(self, path: Path, root: Path, actor: str = "aggregator"):
        self.path = path
        self.root = root.resolve()
        self.actor = actor

    def read(self, path: Path, scope: str = "library") -> None:
        self.record(scope, "read", self._target(path))

    def write(self, path: Path, scope: str = "library") -> None:
        self.record(scope, "write", self._target(path))

    def http(self, host: str, scope: str = "library") -> None:
        self.record(scope, "http", host)

    def record(self, scope: str, action: str, target: str) -> None:
        if action not in ("read", "write", "http", "email", "other"):
            raise ValueError("invalid action log action")
        self.path.parent.mkdir(mode=DIR_MODE, parents=True, exist_ok=True)
        descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, FILE_MODE)
        record = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "scope": scope,
            "actor": self.actor,
            "action": action,
            "target": target,
        }
        with os.fdopen(descriptor, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")

    def _target(self, path: Path) -> str:
        resolved = path.resolve()
        try:
            return resolved.relative_to(self.root).as_posix()
        except ValueError:
            return str(resolved)


class NullActionLogger:
    def read(self, path: Path, scope: str = "library") -> None:
        pass

    def write(self, path: Path, scope: str = "library") -> None:
        pass

    def http(self, host: str, scope: str = "library") -> None:
        pass

