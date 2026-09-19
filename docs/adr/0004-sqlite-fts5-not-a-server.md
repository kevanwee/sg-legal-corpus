# ADR 0004 — SQLite + FTS5 as the query artifact, not a database server

**Status:** accepted · **Date:** 2026-09-19

## Context

The MCP server needs full-text search with metadata filtering over a corpus in the low millions of records. Postgres with `pgvector` was the alternative, and would have given hybrid keyword + vector retrieval natively.

## Decision

Canonical storage is JSONL. Analytics is Parquet. The query artifact the MCP server opens is a single SQLite file with an FTS5 external-content index, built by `sgcorpus index`.

## Rationale

The corpus is read-mostly with a single writer — a batch pipeline — and a small number of concurrent readers. That is the exact workload SQLite is best at and the one where a server buys nothing.

An MCP server that requires a live database connection is meaningfully harder to install, and installation friction is most of what decides whether a tool gets used. One file, opened read-only, has no connection string, no credentials and no failure mode where the agent works and the database does not.

It also keeps the corpus on the machine, which is what [legal-posture.md](../legal-posture.md) requires. A hosted Postgres is a copy of restricted material sitting on someone else's infrastructure.

FTS5 with `porter unicode61 remove_diacritics 2` handles English stemming and the vernacular Hansard material adequately. BM25 ranking is built in.

## Consequences

Vector search is not free. The `embeddings` extra adds a vector table for hybrid retrieval, but it is opt-in and keyword stays the baseline — legal retrieval is dominated by exact-term matching on section numbers, party names and neutral citations, and a vector index that cannot find `s 157(1)` is not an improvement.

FTS5 tokenises provision references poorly, so exact identifier lookup deliberately goes through `documents.urn` and the alias table instead. Keyword search and identifier resolution are separate problems and are kept apart.

If concurrent write access or a hosted multi-user deployment ever becomes a requirement, this decision is revisited. Neither is on the roadmap.
