"""Resumable work-unit tracking.

A crash at 80% resumes at 80%. All four prototype scrapers accumulate results
in memory and write once at the end, so a failure in hour three loses hours one
and two; that is fixed here rather than carried forward.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path


class Checkpoint:
    """An append-only record of completed work-unit keys, per adapter."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._done: set[str] = set()
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        with self.path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    self._done.add(json.loads(line)["key"])
                except (json.JSONDecodeError, KeyError):
                    # A torn final line from an interrupted write is expected.
                    continue

    def is_done(self, key: str) -> bool:
        return key in self._done

    def mark(self, key: str, *, note: str | None = None) -> None:
        if key in self._done:
            return
        self._done.add(key)
        record = {"key": key, "at": datetime.now(UTC).isoformat()}
        if note:
            record["note"] = note
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
            fh.flush()

    def mark_empty(self, key: str, reason: str) -> None:
        """Record a unit that legitimately produced nothing.

        A non-sitting day and a failed fetch look identical on a re-run unless
        the negative result is cached. This is also what lets corpus_stats
        report a recorded reason for every absent unit rather than a silent gap.
        """
        self.mark(key, note=f"empty: {reason}")

    def __len__(self) -> int:
        return len(self._done)
