# Roadmap

Seven phases. Each has an exit gate that is a checkable fact, not a feeling. Sources are ordered by ascending legal risk and descending API quality, so the easy, defensible source proves the architecture before the hard ones stress it.

---

## Phase 0 — Foundations ✅ committed

Record model, URN scheme, adapter protocol, content-addressed snapshot store, rate-limited client, SQLite/FTS5 index, CLI, MCP tool contracts, CI.

**Gate:** Hansard runs end to end — `fetch → normalise → index → search` — against a committed fixture with no network access, and the output validates against `schemas/document.schema.json`.

---

## Phase 1 — Hansard depth

The reference corpus. Already has a working adapter; this phase makes it complete.

- Per-speech records with speaker, role and constituency, replacing the prototype's one-row-per-day, 32k-truncated `DebateText`.
- Parliamentary questions split out: question number, asking member, answering ministry, oral vs written.
- Vernacular sections (Malay, Chinese, Tamil) retained with a `language` tag rather than dropped.
- Sitting-calendar enumeration replacing day-by-day iteration over every date. The current approach issues ~250 requests per year to find ~40 sitting days.
- Backfill: 2020 → present first, then as far back as SPRS serves. Pre-1998 material is image-only and out of scope until there is a reason.

**Gate:** every sitting day in the parliamentary calendar for the covered range is either ingested or has a recorded reason for absence. Speaker attribution is present on ≥98% of speeches, measured against a hand-checked 50-sitting sample.

---

## Phase 2 — Case law

The highest-value and highest-risk corpus.

- Full judgment text with paragraph structure preserved — the prototype stores only word and paragraph counts, which is a statistics dataset, not a corpus.
- All courts: `SGCA`, `SGHC(A)`, `SGHC`, `SGHC(I)`, `SGHCR`, plus State Courts (`SGDC`, `SGMC`) and Family (`SGFC`). The prototype's `Filter=SUPCT` excludes the State Courts entirely, which is most of the criminal and small-claims output.
- Structured parties, coram, counsel and catchwords as lists rather than the prototype's single joined `LegalParties` string.
- Per-judgment resumability. The prototype holds every case in memory and writes one CSV at the end, so an interruption at case 4,000 of 5,000 loses all 4,000.
- Mojibake handled centrally instead of by per-character replacement.

**Gate:** ≥99% of judgments listed by the source for a sampled year are ingested with full text; paragraph numbering round-trips against the source for a hand-checked 20-judgment sample. Licensing position reviewed before any backfill beyond a sampled year — see [legal-posture.md](legal-posture.md#elitigation-supreme-court--judiciary).

---

## Phase 3 — Legislation

The hardest parsing problem and the one with the most valuable output.

- Short codes read from listing hrefs, replacing the prototype's acronym guessing. This is the defect that makes the current SSO scraper unusable: `create_acronym("Companies Act 1967")` yields `CA1967`, which happens to be right, while a large share of the statute book yields codes that fetch nothing or fetch a different Act.
- Fix the hardcoded `Act/AA2004` in every constructed provision URL — currently every provision URL the prototype emits points at the same wrong Act.
- Store provision text. The prototype extracts `content` and then omits it from the CSV writer.
- Provision tree: Parts, Divisions, sections, subsections, paragraphs, with headings.
- **Point-in-time versions.** SSO exposes historical versions; each provision version becomes its own record with `in_force_from` / `in_force_to`. This is the phase that makes `as_of` real, and it is most of the work.
- Subsidiary legislation, linked to parent Acts.
- Repealed Acts retained with `repealed: true`.
- 2020 Revised Edition renumbering captured as aliases, without which citation resolution fails on most pre-2021 case law.

**Gate:** every current Act resolves from its listing href; a 30-provision sample round-trips text against the live page; `get_provision` returns the correct historical version for 20 hand-verified provision/date pairs spanning a known amendment.

---

## Phase 4 — PDPC enforcement

Mostly a port. The existing scraper is the best-engineered of the four.

- All nine DP obligations, not only Protection.
- Numeric penalties, with `0` + reason distinguished from `null` unknown.
- Full decision text from the PDF, not just the summary paragraph.
- OCR fallback for the ~25% of decisions published as scans, which currently lose their citation entirely.
- Commission undertakings, a separate listing the prototype does not touch.

**Gate:** decision count matches the source's own listing total for every obligation filter; citation coverage ≥95% including OCR recovery.

---

## Phase 5 — The graph

Where four datasets become one corpus.

- Citation extraction across all corpora: neutral citations, law-report citations, statute references, Hansard references.
- Citation resolution to URNs, with unresolved citations retained and reported as a coverage metric.
- **Provision ↔ debate linkage** — the chain behind `parliamentary_intent`. Built by matching Act long titles and Bill numbers to Hansard section titles, confidence-scored because the join is textual.
- Case treatment classification (`follows` / `applies` / `distinguishes` / `doubts` / `overrules`) from signal phrases.
- Alias table: report citations, case names, pre-2020 chapter numbers, renumbered provisions.

**Gate:** ≥90% of extracted citations resolve to a URN or are explicitly recorded as out-of-corpus; provision↔debate linkage verified by hand against 20 known second readings; treatment classifier scores ≥0.85 F1 on a hand-labelled set of 200 citing sentences.

---

## Phase 6 — MCP server GA

- All nine tools implemented against the built index.
- Structured typed errors throughout.
- Read-only index access, no write path from the server.
- Published install instructions for Claude Code, Claude Desktop and any MCP client.
- A pinned evaluation set of Singapore legal questions with known-correct citations, run in CI, so a retrieval regression fails the build. The shape [harvey-labs](https://github.com/kevanwee/harvey-labs) already uses.

**Gate:** the eval set passes at a recorded baseline; every tool has a test that asserts the response carries URN, source URL and a caveat where inference was involved.

---

## Phase 7 — Freshness

- Scheduled incremental refresh per source, respecting the rate limits.
- Change detection: a provision whose text changed produces a new version record rather than overwriting.
- Coverage and freshness alerting when a source goes quiet for longer than its normal cadence — a silent scraper failure and a parliamentary recess look identical without this.
- Optional published release artifact, **metadata only**, per [legal-posture.md](legal-posture.md).

---

## Deliberately out of scope

**LawNet and any subscription source.** Licensed content, no route to compliant access.

**Generated legal advice.** This project retrieves and cites. It does not conclude. The nearest thing to a conclusion it produces is "here is what Parliament said this was for", attributed and dated.

**A hosted service.** Local SQLite, local corpus, no server, no data leaving the machine. Re-examine only if a licensing conversation changes the posture.

**Embeddings as the primary retrieval path.** Available behind the `embeddings` extra for hybrid search. Keyword remains the baseline because legal retrieval is dominated by exact-term matching, and a vector index that cannot find `s 157(1)` is not an improvement.
