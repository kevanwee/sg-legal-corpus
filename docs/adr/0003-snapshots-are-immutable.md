# ADR 0003 — Raw snapshots are immutable; everything else is derived

**Status:** accepted · **Date:** 2026-09-19

## Context

Legal sources change their markup without notice, and parsers are wrong in ways that only surface months later. The question is what happens when a parser bug is found after a large backfill.

## Decision

`fetch` writes raw response bytes to a content-addressed store and interprets nothing. Every later stage is a pure function of that store. Fetching is the only stage that touches the network.

A snapshot's file name is the SHA-256 of its body. A sidecar manifest records the URL, request parameters, HTTP status, response headers and retrieval time. Every `Document` carries `snapshot_sha256` plus the `adapter_version` and `parser_rev` that produced it.

## Consequences

A parser fix costs a `normalise` re-run over local disk — minutes — instead of a re-crawl measured in days and rate-limit budget. Given the deliberately slow rates in [legal-posture.md](../legal-posture.md), a full eLitigation re-crawl is not a thing anyone would choose to do twice.

It also means the corpus is reproducible. Anyone with the same snapshot store and the same `parser_rev` derives byte-identical documents, so a disputed record is checkable rather than a matter of trust.

Re-fetching unchanged content is detectable for free: identical bytes produce a hash that already exists.

The cost is storage — raw HTML for the full judgment corpus is large, and it is kept forever alongside the parsed output. Snapshots compress well and disk is cheaper than a second crawl, so the trade is not close.
