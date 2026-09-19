"""Stage 4 -- index. JSONL into the queryable SQLite artifact.

Parquet output is optional and lives behind the `analytics` extra; the SQLite
file is what the MCP server opens.
"""

from __future__ import annotations

import logging
from typing import Any

from ..adapters import registry
from ..config import Paths
from ..store import sqlite
from ..store.jsonl import read_documents, read_refs

log = logging.getLogger(__name__)

BATCH = 2000


def run(paths: Paths, *, adapters: list[str] | None = None) -> dict[str, Any]:
    paths.ensure()
    names = adapters or list(registry.all_adapters())

    conn = sqlite.connect(paths.db)
    sqlite.init(conn)

    documents = refs = 0
    versions: dict[str, tuple[str, int]] = {}

    for name in names:
        path = paths.documents / f"{name}.jsonl"
        if not path.exists():
            continue

        adapter = registry.get(name)
        versions[str(adapter.corpus)] = (adapter.adapter_version, adapter.parser_rev)

        batch = []
        for document in read_documents(path):
            batch.append(document)
            if len(batch) >= BATCH:
                documents += sqlite.insert_documents(conn, batch)
                batch.clear()
        if batch:
            documents += sqlite.insert_documents(conn, batch)

        ref_path = paths.refs / f"{name}.jsonl"
        if ref_path.exists():
            refs += sqlite.insert_refs(conn, read_refs(ref_path))

        log.info("indexed %s", name)

    sqlite.rebuild_fts(conn)
    sqlite.refresh_corpus_meta(conn, adapter_versions=versions)
    conn.close()

    return {"documents": documents, "refs": refs, "db": str(paths.db)}
