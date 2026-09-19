"""The adapter protocol.

Three methods, strictly separated:

    plan()   cheap, deterministic, repeatable -- enumerates resumable work
    fetch()  the only impure method -- network in, raw bytes out, no parsing
    parse()  pure and offline -- bytes in, Documents out

``parse`` never touches the network, which is what allows every adapter to be
tested against committed fixtures with no live source. See docs/architecture.md.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Protocol, runtime_checkable

from ..models import Authority, Corpus, Document
from ..net.cache import Snapshot
from ..net.client import Client


@dataclass(frozen=True, slots=True)
class WorkUnit:
    """One resumable unit of fetching.

    ``key`` must be stable across runs -- it is what the checkpoint records.
    """

    key: str
    payload: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return self.key


@runtime_checkable
class SourceAdapter(Protocol):
    name: str
    corpus: Corpus
    authority: Authority
    adapter_version: str
    parser_rev: int
    """Increment whenever parse() changes. Records remember which revision
    produced them, so a partial re-derivation over the snapshot store is safe
    and diffable."""

    def plan(self, since: date | None = None, until: date | None = None) -> Iterator[WorkUnit]:
        """Enumerate work units. Must be cheap and deterministic."""
        ...

    def fetch(self, unit: WorkUnit, client: Client) -> Iterator[tuple[str, bytes, int, dict[str, Any]]]:
        """Yield (url, body, status, params) tuples. No parsing, no interpretation.

        One unit may yield several snapshots -- a listing plus the documents it
        points at, say. They are checkpointed together, so the unit is only
        marked done once all of its snapshots are on disk.
        """
        ...

    def parse(self, snapshot: Snapshot) -> Iterator[Document]:
        """Snapshot bytes to Documents. Pure, offline, deterministic.

        Where an adapter stores more than one kind of snapshot, ``params``
        carries the discriminator; parse dispatches on it rather than sniffing
        the bytes.
        """
        ...

    def configure(self, **options: Any) -> None:
        """Apply run-time options from the CLI. Optional; default is a no-op."""
        ...


class NotImplementedAdapter:
    """Base for planned-but-unbuilt adapters.

    Carries the real metadata so the registry, CLI and corpus_stats can list
    the source honestly as planned rather than pretending it does not exist.
    """

    name: str = "unimplemented"
    corpus: Corpus
    authority: Authority
    adapter_version: str = "0.0.0"
    parser_rev: int = 0
    spec_doc: str = ""

    def _unbuilt(self) -> Iterator[Any]:
        raise NotImplementedError(
            f"the {self.name} adapter is specified but not built. "
            f"See {self.spec_doc} for the endpoints, the record mapping, and the "
            f"defects in the prototype scraper that the rewrite must not inherit."
        )

    def plan(self, since: date | None = None, until: date | None = None) -> Iterator[WorkUnit]:
        yield from self._unbuilt()

    def fetch(self, unit: WorkUnit, client: Client) -> Iterator[tuple[str, bytes, int, dict[str, Any]]]:
        yield from self._unbuilt()

    def parse(self, snapshot: Snapshot) -> Iterator[Document]:
        yield from self._unbuilt()

    def configure(self, **options: Any) -> None:
        return None
