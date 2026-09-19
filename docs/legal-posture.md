# Legal posture

Read this before running a full backfill.

This is a working position adopted by the project, not legal advice, and it has not been cleared with any of the four authorities. It is deliberately conservative: where the position is uncertain, the project takes the narrower reading.

## The short version

Singapore legal material is **published**, which is not the same as **open**. None of the four sources carries an open-data licence. Three of them carry terms of use that restrict systematic copying.

So:

- This repository ships **code, schemas and the build pipeline**. It never ships the corpus.
- Running the pipeline builds a corpus **on your own machine, for your own use**.
- `data/` is gitignored, and CI has no step that could publish it.
- Any public release artifact is limited to **citation-level metadata** — identifiers, dates, courts, neutral citations, catchwords, penalty amounts. Facts about documents, not the documents.
- Redistributing full text requires **written permission** from the relevant authority.

## Per source

### Singapore Statutes Online (AGC)

**Copyright.** Government of Singapore. Legislation is protected; s 194 of the Copyright Act 2021 governs Government copyright.

**Terms.** SSO's terms of use permit viewing and downloading for personal and non-commercial reference. Systematic downloading, bulk reproduction and republication are outside that permission.

**Position.** Fetch at a low rate for local use. Do not redistribute provision text. The publishable derivative is the structural skeleton: short codes, provision numbers, headings, commencement dates, amendment relationships. A table of which section was amended by which Act on which date is a set of facts; the section text is not.

**Rate limit.** 1 request / 6s, single connection, including robots requests.

**SSO access exception (owner authorised, 2026-09-19).** CloudFront rejects the
project, curl and Python User-Agents while accepting a browser User-Agent.
SSO's published robots.txt permits crawling except `/search`, with a six-second
crawl-delay. The owner has authorised a per-source browser User-Agent on the
basis that we honour that published policy: never request `/search`, never
exceed one request per six seconds. This is an explicit exception to the
project-identifying UA and bot-detection rule below, not a global UA change.

### eLitigation (Supreme Court / Judiciary)

**Copyright.** Judgments are Government copyright. Publication on eLitigation is for public access to the courts, not a dedication to the public domain.

**Terms.** eLitigation's terms restrict automated and bulk retrieval. This is the source where the gap between "publicly readable" and "licensed for bulk copying" is widest, and the one most likely to attract objection.

**Position.** The narrowest of the four. Fetch slowly, cache aggressively, never re-fetch what is already on disk. Publishable derivative is the citator layer only: neutral citation, court, date, coram, catchwords, parties, outcome, paragraph count, and the citation graph. Judgment text stays local.

A licensing conversation with the Judiciary or SAL is the correct route for anything broader, and is on the roadmap as a Phase 2 gate rather than an afterthought.

**Rate limit.** 1 request / 3s, single connection, with a hard daily cap that the client enforces.

### PDPC decisions

**Copyright.** Government of Singapore.

**Terms.** Decisions are published for public education and deterrence, which is a purpose that sits more comfortably with reuse than the other three. The published grounds are still Government copyright.

**Position.** The most permissive of the four in practice. Metadata — respondent, obligations breached, decision type, penalty, date, citation — is factual and is the substance of what any analysis needs. The existing [pdpcscraper](https://github.com/kevanwee/pdpcscraper) already publishes this shape and is a reasonable precedent to follow. Decision text stays local by default.

**Rate limit.** 1 request / 1.5s. PDF downloads count against the same budget.

### Hansard (Parliament of Singapore)

**Copyright.** Parliament of Singapore.

**Terms.** SPRS is a public record of proceedings. Parliamentary privilege attaches to what is said in the House, which is a different regime from copyright and is worth noting: accurate reporting of proceedings is protected, but that protection is about defamation, not about reproduction rights.

**Position.** The most defensible source to build on, which is why it is Phase 1. Quotation of specific speeches with full attribution — speaker, date, column reference — is ordinary and expected use. Republishing the full historical record as a downloadable dataset is not, and is not planned.

**Rate limit.** 1 request / 0.5s. The endpoint is a real JSON API and tolerates this comfortably; the prototype's 0.2s is also fine but the extra margin costs nothing on an incremental run.

## What the client enforces

`net/client.py` applies these without an opt-out flag:

- Per-host token bucket at the rates above.
- `robots.txt` fetched once per host per run and respected. A disallowed path is skipped with a logged reason, not fetched.
- Exponential backoff on 429 and 5xx, with a ceiling and then a give-up that checkpoints rather than hammers.
- A User-Agent identifying the project and linking to this repository, so an administrator who sees the traffic can find out what it is and contact the author.
- Conditional requests (`If-None-Match`, `If-Modified-Since`) wherever the source supports them, so an incremental run mostly produces 304s.
- No concurrency across requests to the same host. Ever.

These are floors, not defaults. A configuration file can slow them down and cannot speed them up.

## What would have to change to redistribute

If full-text redistribution becomes the goal, the honest path is:

1. Written permission from AGC (legislation), the Judiciary or SAL (judgments), PDPC (decisions), and Parliament (Hansard). These are four separate conversations with four different answers.
2. A `LICENSE-DATA` file recording exactly what was permitted, by whom, on what date, and with what conditions.
3. Attribution and provenance surfaced in every downstream output — which the record model already carries, so this part is free.
4. A takedown and correction process, because a corpus of legal material that cannot be corrected is a liability.

Until step 1 happens for a given source, that source's full text does not leave a local machine.

## Things this project will not do

- Circumvent access controls, paywalls, rate limits or bot detection. If a source blocks automated access, that is an answer.
- Scrape LawNet or any subscription source.
- Publish a mirror of any source's full text.
- Present derived data as authoritative. Every MCP response carries the source URL so the answer can be checked against the real thing, and the server says so in its tool descriptions.
