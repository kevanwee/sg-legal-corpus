# sg-legal-corpus

The full stack of Singapore legal authority as one queryable, point-in-time corpus — statutes, case law, data-protection enforcement and parliamentary debate — plus the MCP server that exposes it to an agent.

Four sources, one record shape, one identifier scheme, provenance on every row:

| Corpus | Authority | Source | Adapter | Status |
|---|---|---|---|---|
| Statutes & subsidiary legislation | AGC | [Singapore Statutes Online](https://sso.agc.gov.sg/) | `sso` | planned |
| Written judgments | Supreme / State / Family Courts | [eLitigation](https://www.elitigation.sg/) | `elitigation` | planned |
| Data-protection enforcement | PDPC | [pdpc.gov.sg](https://www.pdpc.gov.sg/all-commissions-decisions) | `pdpc` | planned |
| Parliamentary debate | Parliament of Singapore | [SPRS Hansard](https://sprs.parl.gov.sg/) | `hansard` | **implemented** |

Prior art: this consolidates and rewrites four prototype scrapers — [sgstatutescraper](https://github.com/kevanwee/sgstatutescraper), [elitiscraper](https://github.com/kevanwee/elitiscraper), [pdpcscraper](https://github.com/kevanwee/pdpcscraper), [hansardscraper](https://github.com/kevanwee/hansardscraper). Those repos stay as-is; see [docs/adr/0002-vendor-the-scrapers.md](docs/adr/0002-vendor-the-scrapers.md) for why they are rewritten rather than wrapped.

## Why this exists

An agent answering a Singapore legal question needs four things at once and currently has none of them in a single place:

1. **The provision as it stood on the relevant date** — not the current consolidation.
2. **The judgment that construed it**, with paragraph-level pincites.
3. **The regulator's enforcement practice** where one exists.
4. **What Parliament said the provision was for** when it was enacted or amended.

(4) is the differentiator. The `parliamentary_intent` tool walks provision → amending Act → Bill → second-reading debate → the speeches on that clause, so a question about purposive interpretation under s 9A of the Interpretation Act 1965 can be answered with citations rather than recollection.

## Design commitments

**Point-in-time or it does not count.** Every provision record carries `in_force_from` / `in_force_to`. Every query takes an optional `as_of`. A corpus that only knows today's law cannot answer a question about a contract signed in 2019.

**Provenance on every record.** Each document carries the source URL, the retrieval timestamp, the SHA-256 of the raw snapshot it was parsed from, and the adapter + parser revision that produced it. Any record can be traced to bytes on disk and re-derived without a re-fetch.

**Raw snapshots are immutable; everything else is derived.** Parser bugs are fixed by re-running `normalise` over the existing snapshot store, not by re-scraping. Re-fetching is for new material only.

**Deterministic before clever.** Citation resolution, provision numbering and date computation are rule-based and testable. Embeddings are an optional retrieval layer on top, never the source of truth.

**Ship the pipeline, not the payload.** See [Legal posture](#legal-posture).

## Architecture

```
  fetch                normalise              enrich                 index               serve
┌─────────┐         ┌────────────┐        ┌────────────┐        ┌────────────┐      ┌───────────┐
│ adapter │ ──────> │  Document  │ ─────> │ cross-refs │ ─────> │  Parquet   │ ───> │    MCP    │
│  .fetch │         │  envelope  │        │ citations  │        │  SQLite    │      │  server   │
└─────────┘         └────────────┘        └────────────┘        │   FTS5     │      └───────────┘
     │                    │                     │               └────────────┘
     v                    v                     v
 snapshots/          documents/               refs/
 (immutable,          (JSONL,               (edge list)
  sha256-addressed)    canonical)
```

Full detail: [docs/architecture.md](docs/architecture.md).

## Identifiers

Everything addressable gets a URN. One scheme across all four corpora:

```
urn:sg:<corpus>:<work>[:<part>][@<as-of>]

urn:sg:act:PC1871                          Penal Code 1871
urn:sg:act:PC1871:s300@2024-01-01          s 300, as in force on 1 Jan 2024
urn:sg:sl:CA1967:S329-2015                 subsidiary legislation
urn:sg:judgment:2024_SGCA_15               [2024] SGCA 15
urn:sg:judgment:2024_SGCA_15:para42        pincite
urn:sg:pdpc:2024_SGPDPC_3                  [2024] SGPDPC 3
urn:sg:hansard:2024-02-06:s3:sp12          speech 12, section 3, sitting of 6 Feb 2024
```

Spec and grammar: [docs/identifiers.md](docs/identifiers.md).

## Quick start

```bash
pip install -e ".[dev]"

# Fetch raw snapshots (immutable, content-addressed)
sgcorpus fetch hansard --start 2024-01-01 --end 2024-03-31

# Parse snapshots into canonical Document JSONL
sgcorpus normalise hansard

# Build the queryable artifact
sgcorpus index

# Inspect
sgcorpus stats
sgcorpus search "purposive interpretation" --corpus hansard --limit 5

# Serve over MCP
sgcorpus serve
```

`data/` is gitignored. Nothing in this repo ships scraped content.

## MCP tools

| Tool | Answers |
|---|---|
| `search` | Full-text across any corpus, filtered by authority, court, date, obligation |
| `get_document` | Fetch by URN, whole or by part |
| `get_provision` | A statutory provision **as in force on a given date** |
| `list_amendments` | The amendment history of a provision, with commencement dates |
| `find_citing` | Everything that cites a given authority |
| `parliamentary_intent` | The debates behind a provision — Bill, second reading, speeches |
| `pdpc_decisions` | Enforcement decisions by obligation, outcome, penalty band, year |
| `resolve_citation` | A citation string → a URN, or an honest failure |
| `corpus_stats` | Coverage and freshness per corpus |

Contracts, argument schemas and worked examples: [docs/mcp-tools.md](docs/mcp-tools.md).

`resolve_citation` is deliberately the same shape as the existence check in [citecheck](https://github.com/kevanwee/citecheck), and the `as_of` convention in `get_provision` matches [ipatlas](https://github.com/kevanwee/ipatlas) and [sg-deadline](https://github.com/kevanwee/sg-deadline).

## Status

Phase 0 (foundations) is committed: record model, URN scheme, adapter interface, snapshot store, SQLite/FTS5 index, MCP tool contracts, and Hansard wired end to end as the reference implementation.

Verified against the live source over three sitting days (5–7 February 2024): 1,419 documents, 98.0% speaker attribution, 129 distinct speakers, 0 parse failures, searchable end to end. Two findings worth knowing about:

- **The SPRS endpoint has changed.** It is now a JSON `POST`, not a `GET` with a query string. The old form returns HTTP 500 for every date — which reads as "no sitting" — so the prototype scraper now collects nothing while exiting successfully. See [docs/sources/hansard.md](docs/sources/hansard.md#endpoint).
- **The scale of the Excel truncation is worse than it looks.** Three sitting days hold 1,647,854 characters of speech. At one 32,767-character cell per day, the prototype's format retains about 6% of the debate.

The other three adapters are specified in `docs/sources/` and stubbed in `src/sgcorpus/adapters/`. Each source note records the endpoints, the pagination behaviour, the known failure modes, and the specific defects in the prototype scraper that the rewrite must not inherit.

Roadmap and phase gates: [docs/roadmap.md](docs/roadmap.md).

## Legal posture

The four sources are **not** open data. SSO and eLitigation content is Government copyright with terms of use that restrict systematic reproduction; Hansard is Parliament copyright.

This repository therefore distributes **code, schemas and the build pipeline — never the corpus**. Running the pipeline produces a corpus on your own machine, for your own use, at a request rate that does not burden the source. Published release artifacts are limited to citation-level metadata (identifiers, dates, courts, catchwords, neutral citations) that is factual rather than expressive. Anything beyond that requires written permission from the relevant authority.

Read [docs/legal-posture.md](docs/legal-posture.md) before running a full backfill. It records the per-source terms, the rate limits the client enforces, and what would have to change to redistribute.

## Licence

Code: MIT ([LICENSE](LICENSE)). Data: not licensed by this project — see [docs/legal-posture.md](docs/legal-posture.md).
