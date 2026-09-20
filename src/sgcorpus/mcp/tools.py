"""MCP tool definitions and handlers.

A thin layer: no parsing, no network, no business logic beyond argument
validation and result shaping. Full contracts in docs/mcp-tools.md.

Two rules hold throughout:

  * Every response carries the URN and the source URL, so an answer is always
    traceable to something a human can open.
  * Failures are structured, never empty. A tool that returns ``[]`` invites an
    agent to fall back on its own recollection, which is the failure this
    corpus exists to prevent.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from typing import Any

from .. import urn as urnlib
from ..store import sqlite as store

TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "search",
        "description": (
            "Full-text search across Singapore legislation, judgments, PDPC enforcement "
            "decisions and parliamentary debate. Returns ranked passages with citations "
            "and source URLs. Call corpus_stats first if you need to know what is covered."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "corpus": {
                    "type": "array",
                    "items": {"enum": ["act", "sl", "judgment", "pdpc", "hansard", "bill"]},
                },
                "date_from": {"type": "string", "format": "date"},
                "date_to": {"type": "string", "format": "date"},
                "limit": {"type": "integer", "default": 20, "maximum": 100},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_document",
        "description": "Fetch a document by URN. Use parts='none' to survey candidates cheaply.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "urn": {"type": "string"},
                "parts": {"oneOf": [{"enum": ["all", "none"]}, {"type": "array"}], "default": "none"},
            },
            "required": ["urn"],
        },
    },
    {
        "name": "get_provision",
        "description": (
            "A statutory provision as in force on a given date. Always returns the "
            "resolved in-force range. Never silently falls back to the current text."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "urn": {"type": "string"},
                "as_of": {"type": "string", "format": "date"},
            },
            "required": ["urn"],
        },
    },
    {
        "name": "list_amendments",
        "description": "The amendment history of a provision, with amending Acts and commencement dates.",
        "inputSchema": {
            "type": "object",
            "properties": {"urn": {"type": "string"}},
            "required": ["urn"],
        },
    },
    {
        "name": "find_citing",
        "description": "Everything in the corpus that cites a given authority, with the citing passage.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "urn": {"type": "string"},
                "kind": {"type": "array", "items": {"type": "string"}},
                "limit": {"type": "integer", "default": 50},
            },
            "required": ["urn"],
        },
    },
    {
        "name": "parliamentary_intent",
        "description": (
            "The parliamentary material behind a provision: the enacting or amending Act, "
            "the Bill, the second-reading sitting and the speeches on it. Admissible as an "
            "aid to construction under s 9A of the Interpretation Act 1965. Bill-to-sitting "
            "linkage is inferred from title matching and is confidence-scored; pass the "
            "caveat through to the user."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "urn": {"type": "string"},
                "include_speeches": {"type": "boolean", "default": True},
            },
            "required": ["urn"],
        },
    },
    {
        "name": "pdpc_decisions",
        "description": "PDPC enforcement decisions by obligation, outcome, penalty band, sector and year, with aggregates.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "obligation": {"type": "array", "items": {"type": "string"}},
                "outcome": {"type": "array", "items": {"type": "string"}},
                "penalty_min": {"type": "integer"},
                "penalty_max": {"type": "integer"},
                "year_from": {"type": "integer"},
                "sector": {"type": "string"},
                "limit": {"type": "integer", "default": 50},
            },
        },
    },
    {
        "name": "resolve_citation",
        "description": (
            "Resolve a citation string to a URN. On failure, distinguishes 'this authority "
            "does not exist' from 'this corpus does not cover it' and returns near-misses."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"citation": {"type": "string"}},
            "required": ["citation"],
        },
    },
    {
        "name": "corpus_stats",
        "description": "Document counts, date coverage and freshness per corpus. Check before relying on an absence of results.",
        "inputSchema": {
            "type": "object",
            "properties": {"corpus": {"type": "string"}},
        },
    },
]

PHASE_5 = "Requires the enrichment pass (docs/roadmap.md, Phase 5)."
PHASE_3 = "Requires the SSO adapter (docs/sources/sso.md, Phase 3)."
PHASE_4 = "Requires the PDPC adapter (docs/sources/pdpc.md, Phase 4)."


def _unavailable(tool: str, reason: str) -> dict[str, Any]:
    """A structured refusal.

    Saying which corpus is missing and why is what lets an agent tell the user
    it cannot answer, instead of answering from memory.
    """
    return {
        "error": "out_of_coverage",
        "tool": tool,
        "message": f"{tool} is not available against the current index. {reason}",
        "remedy": "Run `sgcorpus sources` to see which adapters are built.",
    }


def handle(conn: sqlite3.Connection, name: str, args: dict[str, Any]) -> dict[str, Any]:
    if name == "search":
        hits = store.search(
            conn,
            args["query"],
            corpus=args.get("corpus"),
            date_from=args.get("date_from"),
            date_to=args.get("date_to"),
            limit=min(int(args.get("limit", 20)), 100),
        )
        for hit in hits:
            hit["source_url"] = _source_url(conn, hit["urn"])
        if not hits:
            return {
                "hits": [],
                "note": "No match in the indexed corpus. Call corpus_stats to check coverage "
                        "before concluding the authority does not exist.",
            }
        return {"hits": hits}

    if name == "get_document":
        document = store.get_document(conn, args["urn"])
        if document is None:
            candidates = conn.execute(
                "SELECT urn FROM documents WHERE json_extract(meta, '$.canonical_urn') = ?",
                (args["urn"],),
            ).fetchall()
            if len(candidates) > 1:
                return {"error": "ambiguous_publication", "urn": args["urn"],
                        "publications": [row["urn"] for row in candidates],
                        "message": "The source publishes multiple texts under this identifier; choose a publication URN."}
            if candidates:
                document = store.get_document(conn, candidates[0]["urn"])
            if document is None:
                return {"error": "not_found", "urn": args["urn"]}
        document["meta"] = json.loads(document["meta"])
        document["provenance"] = json.loads(document["provenance"])
        if args.get("parts", "none") == "none":
            document.pop("text", None)
        return document

    if name == "corpus_stats":
        corpora = []
        for row in store.stats(conn):
            row = dict(row)
            row["quality"] = json.loads(row.pop("quality", None) or "{}")
            corpora.append(row)
        return {"corpora": corpora}

    if name == "get_provision":
        return _get_provision(conn, args)
    if name == "list_amendments":
        return _unavailable(name, PHASE_5)
    if name == "pdpc_decisions":
        return _pdpc_decisions(conn, args)
    if name in ("find_citing", "parliamentary_intent", "resolve_citation"):
        return _unavailable(name, PHASE_5)

    return {"error": "not_found", "message": f"unknown tool {name!r}"}


def _source_url(conn: sqlite3.Connection, urn: str) -> str | None:
    row = conn.execute("SELECT provenance FROM documents WHERE urn = ?", (urn,)).fetchone()
    return json.loads(row["provenance"]).get("source_url") if row else None


def _pdpc_decisions(conn: sqlite3.Connection, args: dict[str, Any]) -> dict[str, Any]:
    clauses = ["corpus = 'pdpc'"]
    params: list[Any] = []
    for argument, field in (("obligation", "obligations"), ("outcome", "decision_types")):
        values = args.get(argument)
        if values:
            clauses.append(f"EXISTS (SELECT 1 FROM json_each(meta, '$.{field}') "
                           f"WHERE value IN ({','.join('?' for _ in values)}))")
            params.extend(values)
    for argument, operator in (("penalty_min", ">="), ("penalty_max", "<=")):
        if argument in args:
            clauses.append(f"json_extract(meta, '$.penalty_sgd') {operator} ?")
            params.append(int(args[argument]))
    if args.get("year_from") is not None:
        clauses.append("issued >= ?")
        params.append(f"{int(args['year_from']):04d}-01-01")
    if args.get("sector"):
        clauses.append("json_extract(meta, '$.sector') = ?")
        params.append(args["sector"])
    where = " AND ".join(clauses)
    aggregate = conn.execute(
        "WITH matched AS (SELECT COALESCE(json_extract(meta,'$.canonical_urn'), urn) AS identity, "
        f"json_extract(meta,'$.penalty_sgd') AS penalty FROM documents WHERE {where}), "
        "amounts AS (SELECT identity, CASE WHEN COUNT(DISTINCT penalty) <= 1 "
        "AND COUNT(penalty) = COUNT(*) THEN MAX(penalty) ELSE NULL END AS penalty "
        "FROM matched GROUP BY identity) "
        "SELECT COUNT(*) AS count, SUM(penalty) AS total_penalty_sgd, "
        "SUM(CASE WHEN penalty IS NULL THEN 1 ELSE 0 END) AS unknown_penalties, "
        "(SELECT COUNT(*) FROM matched) AS publications FROM amounts", params,
    ).fetchone()
    rows = conn.execute(f"SELECT * FROM documents WHERE {where} ORDER BY issued DESC, urn LIMIT ?",
                        [*params, max(1, min(int(args.get('limit', 50)), 100))]).fetchall()
    decisions = []
    for row in rows:
        record = dict(row)
        record["meta"] = json.loads(record["meta"])
        record["provenance"] = json.loads(record["provenance"])
        record["source_url"] = record["provenance"]["source_url"]
        decisions.append(record)
    return {"decisions": decisions, "aggregates": dict(aggregate),
            "note": "Figures cover distinct indexed decision identifiers; publication variants are not double-counted. Unknown or conflicting penalties are excluded from the total."}


def _get_provision(conn: sqlite3.Connection, args: dict[str, Any]) -> dict[str, Any]:
    try:
        requested = urnlib.parse(args["urn"])
        if requested.corpus not in ("act", "sl") or not requested.parts:
            raise ValueError("get_provision requires a legislation provision URN")
        as_of = date.fromisoformat(args["as_of"]) if args.get("as_of") else requested.as_of
        if requested.as_of and as_of != requested.as_of:
            raise ValueError("URN date and as_of disagree")
    except ValueError as exc:
        return {"error": "invalid_argument", "message": str(exc)}
    canonical = str(requested.at(None))
    rows = [dict(row) for row in conn.execute(
        "SELECT * FROM documents WHERE urn = ? OR json_extract(meta, '$.canonical_urn') = ?",
        (canonical, canonical),
    ).fetchall()]
    ranges = []
    matches = []
    target = (as_of or date.today()).isoformat()
    for row in rows:
        meta = json.loads(row["meta"])
        start, end = row["in_force_from"], row["in_force_to"]
        if meta.get("deleted") or meta.get("repealed") or meta.get("uncommenced") or not start:
            continue
        ranges.append({"from": start, "to": end, "urn": row["urn"]})
        if start <= target and (end is None or target < end):
            matches.append(row)
    if not matches:
        return {"error": "not_in_force", "urn": canonical, "as_of": target,
                "available_ranges": sorted(ranges, key=lambda r: r["from"]),
                "message": "No indexed version is in force on this date. Historical coverage may be incomplete; current text was not substituted."}
    if len(matches) != 1:
        return {"error": "ambiguous_version", "urn": canonical, "as_of": target,
                "available_ranges": ranges}
    row = matches[0]
    row["meta"] = json.loads(row["meta"])
    row["provenance"] = json.loads(row["provenance"])
    row["source_url"] = row["provenance"]["source_url"]
    row["as_of"] = target
    row["validity_convention"] = "inclusive start, exclusive end"
    return row
