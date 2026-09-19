"""The query artifact: SQLite with an FTS5 external-content index.

One file, opened read-only by the MCP server. No connection string, no
credentials, no failure mode where the agent works and the database does not.
See docs/adr/0004-sqlite-fts5-not-a-server.md and docs/corpus-spec.md.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..models import Document, Ref

SCHEMA = """
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS documents (
    urn            TEXT PRIMARY KEY,
    corpus         TEXT NOT NULL,
    authority      TEXT NOT NULL,
    title          TEXT,
    citation       TEXT,
    issued         TEXT,
    in_force_from  TEXT,
    in_force_to    TEXT,
    parent         TEXT,
    language       TEXT NOT NULL DEFAULT 'en',
    text           TEXT NOT NULL,
    meta           TEXT NOT NULL,
    provenance     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_documents_corpus_issued ON documents(corpus, issued);
CREATE INDEX IF NOT EXISTS idx_documents_parent        ON documents(parent);
CREATE INDEX IF NOT EXISTS idx_documents_force         ON documents(in_force_from, in_force_to);

-- External content: the text is stored once, in documents.
CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
    title, citation, text,
    content='documents',
    content_rowid='rowid',
    tokenize='porter unicode61 remove_diacritics 2'
);

CREATE TABLE IF NOT EXISTS refs (
    from_urn   TEXT NOT NULL,
    to_urn     TEXT,
    kind       TEXT NOT NULL,
    raw        TEXT,
    confidence REAL NOT NULL DEFAULT 1.0,
    resolver   TEXT
);

CREATE INDEX IF NOT EXISTS idx_refs_from ON refs(from_urn);
CREATE INDEX IF NOT EXISTS idx_refs_to   ON refs(to_urn);

CREATE TABLE IF NOT EXISTS aliases (
    alias TEXT PRIMARY KEY,
    urn   TEXT NOT NULL,
    kind  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS corpus_meta (
    corpus          TEXT PRIMARY KEY,
    document_count  INTEGER NOT NULL,
    earliest        TEXT,
    latest          TEXT,
    last_fetch      TEXT,
    coverage_pct    REAL,
    adapter_version TEXT,
    parser_rev      INTEGER
);
"""


def connect(path: Path, *, read_only: bool = False) -> sqlite3.Connection:
    if read_only:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def insert_documents(conn: sqlite3.Connection, documents: Iterable[Document]) -> int:
    rows = [
        (
            d.urn,
            str(d.corpus),
            str(d.authority),
            d.title,
            d.citation,
            d.dates.issued.isoformat(),
            d.dates.in_force_from.isoformat() if d.dates.in_force_from else None,
            d.dates.in_force_to.isoformat() if d.dates.in_force_to else None,
            d.parent,
            d.language,
            d.text,
            json.dumps(d.meta, ensure_ascii=False),
            d.provenance.model_dump_json(),
        )
        for d in documents
    ]
    conn.executemany(
        "INSERT OR REPLACE INTO documents "
        "(urn, corpus, authority, title, citation, issued, in_force_from, in_force_to, "
        " parent, language, text, meta, provenance) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    return len(rows)


def insert_refs(conn: sqlite3.Connection, refs: Iterable[Ref]) -> int:
    rows = [(r.from_urn, r.to_urn, str(r.kind), r.raw, r.confidence, r.resolver) for r in refs]
    conn.executemany(
        "INSERT INTO refs (from_urn, to_urn, kind, raw, confidence, resolver) VALUES (?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    return len(rows)


def rebuild_fts(conn: sqlite3.Connection) -> None:
    """Rebuild the external-content index from documents.

    Cheaper in bulk than maintaining triggers during a large load.
    """
    conn.execute("INSERT INTO documents_fts(documents_fts) VALUES('rebuild')")
    conn.commit()


def refresh_corpus_meta(
    conn: sqlite3.Connection,
    *,
    adapter_versions: dict[str, tuple[str, int]] | None = None,
) -> None:
    now = datetime.now(UTC).isoformat()
    rows = conn.execute(
        "SELECT corpus, COUNT(*) AS n, MIN(issued) AS earliest, MAX(issued) AS latest "
        "FROM documents GROUP BY corpus"
    ).fetchall()
    for row in rows:
        version, rev = (adapter_versions or {}).get(row["corpus"], (None, None))
        conn.execute(
            "INSERT OR REPLACE INTO corpus_meta "
            "(corpus, document_count, earliest, latest, last_fetch, coverage_pct, "
            " adapter_version, parser_rev) VALUES (?,?,?,?,?,?,?,?)",
            (row["corpus"], row["n"], row["earliest"], row["latest"], now, None, version, rev),
        )
    conn.commit()


def search(
    conn: sqlite3.Connection,
    query: str,
    *,
    corpus: list[str] | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """BM25-ranked full-text search with metadata filtering.

    Exact identifier lookup deliberately does not come through here -- FTS5
    tokenises `s 157(1)` poorly, so provision lookup goes via documents.urn and
    the alias table instead. Keyword search and identifier resolution are
    different problems.
    """
    sql = [
        "SELECT d.urn, d.corpus, d.authority, d.title, d.citation, d.issued, d.parent,",
        "       snippet(documents_fts, 2, '<<', '>>', ' ... ', 24) AS snippet,",
        "       bm25(documents_fts) AS score",
        "FROM documents_fts",
        "JOIN documents d ON d.rowid = documents_fts.rowid",
        "WHERE documents_fts MATCH ?",
    ]
    params: list[Any] = [query]

    if corpus:
        sql.append(f"AND d.corpus IN ({','.join('?' * len(corpus))})")
        params.extend(corpus)
    if date_from:
        sql.append("AND d.issued >= ?")
        params.append(date_from)
    if date_to:
        sql.append("AND d.issued <= ?")
        params.append(date_to)

    sql.append("ORDER BY score LIMIT ?")
    params.append(limit)

    return [dict(row) for row in conn.execute(" ".join(sql), params).fetchall()]


def get_document(conn: sqlite3.Connection, urn: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM documents WHERE urn = ?", (urn,)).fetchone()
    return dict(row) if row else None


def stats(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute("SELECT * FROM corpus_meta ORDER BY corpus").fetchall()]
