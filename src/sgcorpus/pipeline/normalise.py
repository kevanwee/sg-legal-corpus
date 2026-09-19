"""Stage 2 -- normalise. Pure, offline, deterministic.

Reads the snapshot store, writes Document JSONL. No network access at all,
which is what makes the whole stage testable against committed fixtures and a
parser fix a minutes-long re-run rather than a re-crawl.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..adapters import registry
from ..config import Paths
from ..net.cache import SnapshotStore
from ..store.jsonl import JsonlWriter

log = logging.getLogger(__name__)


def run(adapter_name: str, paths: Paths) -> dict[str, int]:
    adapter = registry.get(adapter_name)
    paths.ensure()

    store = SnapshotStore(paths.snapshots)
    out_path: Path = paths.documents / f"{adapter_name}.jsonl"

    snapshots = failures = 0

    with JsonlWriter(out_path) as writer:
        for snapshot in store.iter_snapshots(adapter_name):
            snapshots += 1
            try:
                for document in adapter.parse(snapshot):
                    writer.write(document)
            except Exception as exc:
                # A parse failure is loud and localised: the snapshot hash is
                # enough to reproduce it exactly.
                failures += 1
                log.error("parse failed for snapshot %s: %s", snapshot.sha256[:12], exc)

    log.info("normalised %d snapshot(s) into %d document(s)", snapshots, writer.count)
    return {
        "snapshots": snapshots,
        "documents": writer.count,
        "failures": failures,
        "parser_rev": adapter.parser_rev,
    }
