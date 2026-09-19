# ADR 0001 — One record shape across all four corpora

**Status:** accepted · **Date:** 2026-09-19

## Context

A statute, a judgment, a PDPC decision and a parliamentary speech have almost nothing structurally in common. The obvious design is four schemas, four tables and four sets of query paths.

The four prototype scrapers each took that route independently and produced four incompatible outputs: a CSV of title strings, a CSV of per-case statistics, two Excel workbooks with different conventions, and an Excel master keyed by date.

## Decision

One `Document` envelope for every record. Corpus-specific fields live in a typed `meta` object. Everything outside `meta` — URN, corpus, authority, title, citation, dates, text, parts, parent, provenance — means the same thing in every corpus.

Documents are flat. A statute with 400 sections is 401 records linked by `parent` and `parts`, not one nested record.

## Consequences

Search, retrieval, provenance, indexing and the MCP response shape are written once. A fifth source costs an adapter and a source note, nothing more.

Flat records mean a provision or a paragraph can be retrieved, versioned and cited independently, which is how legal authority is actually used — nobody cites a whole Act.

The cost is that `meta` is a union type, and cross-corpus queries can only filter on envelope fields. That is the right trade: the envelope fields are exactly the ones worth filtering across corpora, and a corpus-specific filter belongs in a corpus-specific tool like `pdpc_decisions`.
