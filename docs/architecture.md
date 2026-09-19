# Architecture

## The shape of the problem

Four sources that share nothing. SSO is server-rendered HTML with a bespoke table-of-contents panel and a point-in-time versioning model. eLitigation is server-rendered HTML with a paginated search index. PDPC is a JavaScript SPA backed by a JSON API, with the authoritative citation locked inside a PDF. Hansard is a clean JSON API keyed by sitting date.

They differ in transport, in pagination, in how an item is identified, in whether history is modelled at all, and in what counts as a "document". A judgment is one document with numbered paragraphs. A statute is a tree of provisions each with its own independent amendment history. A sitting day is a sequence of speeches by different people on unrelated topics.

The architecture exists to absorb that variance in exactly one place — the adapter — and present everything downstream with one shape.

## Five stages

```
 ┌──────────┐    ┌───────────┐    ┌────────┐    ┌───────┐    ┌───────┐
 │  fetch   │───>│ normalise │───>│ enrich │───>│ index │───>│ serve │
 └──────────┘    └───────────┘    └────────┘    └───────┘    └───────┘
      │                │               │            │
      v                v               v            v
 data/snapshots/  data/documents/  data/refs/   data/index/
   raw bytes        Document        edge list    corpus.db
   sha256-named      JSONL          JSONL         Parquet
```

Each stage reads only the stage before it and writes only its own directory. Any stage can be re-run without re-running its predecessors. That property is what makes a parser fix cost minutes instead of a week of re-crawling.

### 1. fetch — network in, bytes to disk

The only stage that touches the network. Its single job is to put raw responses on disk without interpreting them.

Snapshots are content-addressed: the file name is the SHA-256 of the response body, and a sidecar manifest records the URL, the request parameters, the HTTP status, the response headers, and the retrieval timestamp. Re-fetching unchanged content is therefore free of storage cost and immediately detectable — a source that returns identical bytes produces a hash that already exists.

Fetching is checkpointed. Every adapter exposes a resumable unit of work (a sitting date, a year of judgments, a listing page) and the checkpoint file records which units have completed. A crash at 80% resumes at 80%. The prototype scrapers all accumulate results in memory and write once at the end, so a failure in hour three loses hours one and two; that is fixed here rather than carried forward.

HTTP goes through one client (`net/client.py`) with a global token-bucket rate limit, exponential backoff on 429 and 5xx, a descriptive User-Agent that identifies the project, and a `robots.txt` check per host. The rate limits are per-source and deliberately conservative — see [legal-posture.md](legal-posture.md).

### 2. normalise — bytes to the Document envelope

Pure, offline, deterministic. Reads snapshots, writes `Document` records as JSONL. No network access at all, which means the whole stage is testable against committed fixtures.

Every parser carries a `parser_rev` integer. When a parser changes, the revision increments, and `normalise` re-derives affected records from the existing snapshot store. Records remember which revision produced them, so a partial re-derivation is safe and diffable.

Text is normalised here and only here: `ftfy`-style mojibake repair (the prototype eLitigation scraper's manual `â€"` → `-` replacements become a general fix), whitespace collapse, Unicode NFC, and preservation of paragraph boundaries because pincites depend on them.

### 3. enrich — the graph

Documents alone are a library. The value is in the edges.

Three passes, each producing a typed edge into `data/refs/`:

- **Citation extraction.** Neutral citations, law-report citations, statute references (`s 157(1) of the Companies Act 1967`) and Hansard references are extracted by rule from every document's text, then resolved to URNs. Unresolvable citations are recorded as unresolved with the raw string kept, never silently dropped — a coverage metric, not a failure.
- **Legislative history.** Amending Acts are linked to the provisions they amend, and provisions to their commencement dates. This is what makes `as_of` queries answerable.
- **Provision ↔ debate linkage.** The chain that powers `parliamentary_intent`: a provision's enacting or amending Act → the Bill number → the second-reading sitting → the speeches within that sitting section. Built from the Act's long title and enactment metadata on the SSO side and the section titles on the Hansard side, with a confidence score, because the join is on text rather than on any shared key.

### 4. index — the queryable artifact

Two outputs from the same JSONL.

**Parquet**, partitioned by corpus and year, for analytics — the descendant of what `codeoflaw`, `crimewatch` and the PDPC analyser do today with ad-hoc DataFrames.

**SQLite with FTS5**, the artifact the MCP server opens. One file, no server, no connection string, portable, and fast enough for interactive retrieval at this corpus size. The schema is in [corpus-spec.md](corpus-spec.md#index-schema). BM25 ranking is the default; an optional embeddings table can be built with the `embeddings` extra for hybrid retrieval, but keyword search stays the baseline because legal retrieval is dominated by exact-term matching — section numbers, party names, neutral citations.

### 5. serve — MCP

A thin layer. The server opens the SQLite file read-only and maps tool calls onto queries. It contains no parsing, no network access and no business logic beyond argument validation and result shaping.

That thinness is deliberate: a bug in retrieval should be reproducible with a `sgcorpus search` invocation from the CLI, without an agent in the loop.

## The adapter interface

```python
class SourceAdapter(Protocol):
    name: str
    corpus: Corpus
    authority: str
    adapter_version: str
    parser_rev: int

    def plan(self, since: date | None, until: date | None) -> Iterator[WorkUnit]:
        """Enumerate resumable units of work. Must be deterministic and cheap."""

    def fetch(self, unit: WorkUnit, client: Client) -> Iterator[Snapshot]:
        """Network in, raw bytes out. No parsing."""

    def parse(self, snapshot: Snapshot) -> Iterator[Document]:
        """Bytes in, Documents out. Pure, offline, deterministic."""
```

Three methods, strictly separated. `plan` is cheap and repeatable so the work queue can be rebuilt at any time. `fetch` is the only impure method. `parse` never touches the network, which is what allows every adapter to be tested against committed fixtures with no live source.

A new source is a new file in `adapters/` implementing these three methods plus a source note in `docs/sources/`. Nothing else in the codebase changes.

## Directory layout

```
src/sgcorpus/
├── models.py           Document envelope, per-corpus payloads, Provenance (pydantic)
├── urn.py              URN minting, parsing, validation
├── config.py           Paths, rate limits, per-source settings
├── checkpoint.py       Resumable work-unit tracking
├── cli.py              typer entrypoint: fetch | normalise | enrich | index | search | stats | serve
├── net/
│   ├── client.py       Rate-limited, retrying, robots-aware HTTP
│   └── cache.py        Content-addressed snapshot store
├── adapters/
│   ├── base.py         SourceAdapter protocol, WorkUnit, Snapshot
│   ├── registry.py     name -> adapter
│   ├── hansard.py      implemented
│   ├── sso.py          stub + plan
│   ├── elitigation.py  stub + plan
│   └── pdpc.py         stub + plan
├── pipeline/
│   ├── ingest.py       drives plan -> fetch -> snapshot store
│   ├── normalise.py    drives snapshot -> parse -> JSONL
│   ├── enrich.py       citation graph, legislative history, provision<->debate
│   └── index.py        JSONL -> Parquet + SQLite FTS5
├── store/
│   ├── jsonl.py        atomic append, streaming read
│   ├── parquet.py      partitioned write (optional dep)
│   └── sqlite.py       schema, FTS5, query helpers
└── mcp/
    ├── tools.py        tool definitions and handlers
    └── server.py       stdio MCP server
```

## Failure modes this design takes seriously

**The source changes its HTML.** Snapshots are already on disk, so the fix is a parser change and a `normalise` re-run. Structural assertions in each parser fail loudly on unexpected shape rather than silently emitting empty fields.

**A scrape is interrupted.** Checkpointing at the work-unit level. Documents are appended to JSONL atomically, so a partial run leaves a valid, shorter file.

**A record is wrong and someone has relied on it.** Provenance points at the exact snapshot hash and parser revision. The claim is reproducible or refutable.

**Excel-shaped data loss.** The prototype Hansard scraper truncates every sitting day to 32,767 characters because that is the Excel cell limit, and stores one row per day. A full sitting day runs to hundreds of thousands of characters, so the majority of the debate is discarded at write time, invisibly. This architecture stores one record per *speech*, in JSONL, with no cell limit anywhere. That single change is most of the reason this repo exists rather than a patch to the prototype.

**Silent under-collection.** `corpus_stats` reports coverage against an expected denominator per source (sitting days in the parliamentary calendar, judgments per year against the source's own result count, decisions against the listing total). A backfill that quietly collects 60% of a year is a reported gap, not a clean exit code.
