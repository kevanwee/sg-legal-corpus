"""Canonical document storage: newline-delimited JSON.

Appended as parsing proceeds, so an interrupted run leaves a valid, shorter
file rather than nothing at all.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import TextIO

from ..models import Document, Ref


class JsonlWriter:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh: TextIO | None = None
        self.count = 0

    def __enter__(self) -> JsonlWriter:
        self._fh = self.path.open("w", encoding="utf-8")
        return self

    def __exit__(self, *exc: object) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None

    def write(self, document: Document) -> None:
        assert self._fh is not None, "use JsonlWriter as a context manager"
        self._fh.write(document.to_jsonl() + "\n")
        self.count += 1
        # Flush per record: the cost is negligible next to the network, and it
        # means a hard kill loses at most one document.
        self._fh.flush()

    def write_all(self, documents: Iterable[Document]) -> int:
        for document in documents:
            self.write(document)
        return self.count


def read_documents(path: Path) -> Iterator[Document]:
    if not path.exists():
        return
    with path.open(encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                yield Document.model_validate_json(line)
            except Exception as exc:
                raise ValueError(f"{path}:{line_no}: invalid Document record: {exc}") from exc


def read_refs(path: Path) -> Iterator[Ref]:
    if not path.exists():
        return
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield Ref.model_validate(json.loads(line))
