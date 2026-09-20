# Source: PDPC enforcement decisions

**Authority:** PDPC · **Corpus:** `pdpc` · **Adapter:** `pdpc` · **Status:** implemented; Phase 4 gate not met
**Prototype:** [pdpcscraper](https://github.com/kevanwee/pdpcscraper)

## Live verification, 19–20 September 2026

The prototype endpoint and selectors are obsolete. The old listing redirects to
`/organisations/regulations-decisions/enforcement-decisions`. Its Next.js bundle
calls the following public endpoint, which answers without cookies, a browser
session or a browser User-Agent:

```
GET https://www.pdpc.gov.sg/api/listing-api
    ?listingtype=enforcement_decisions
    &itemsperpage=100
    &pathname=/organisations/regulations-decisions/enforcement-decisions
    &type=Commission%27s+Decisions&page=1&sort=oldest
```

`type=Voluntary+Undertakings` selects undertakings on the same endpoint.
The response has `totalItems`, `data` and `listingIntro`. Rows carry `id`,
`title`, `href`, `date` and `topic`. Read the href; do not invent a detail URL.

Measured discovery: **269/269 unique decisions** over pages of 100, 100 and 69;
**118/118 unique undertakings** over pages of 100 and 18. The API accepts
`itemsperpage=100`; no Playwright dependency is needed. The old candidate
`/api/PDPC/General/GetEnforcementCase` returned 404. No observed current bundle
references `getenforcementcase`.

**Original obligation gate unavailable:** the current UI offers type/year,
not obligation filters. API queries with `topic=Protection`, `topic=Accuracy`,
and a deliberately nonexistent topic each return **269** decisions. These
are ignored filters, not measured obligation-specific denominators. Do not
claim the per-obligation gate has passed. All listed decisions are selected;
obligations are read from source tags, falling back to the title with the
basis recorded. Historical “Openness” maps to Accountability. Unlabelled
obligations and sector remain unknown; there is no invented classification.

## Detail and PDF transport

Detail HTML renders an empty `.rte`, but carries the rich text in Next hydration
records. Long content uses `$<id>` references and length-prefixed UTF-8 text
frames spanning script tags. The parser resolves these offline and checks byte
lengths. PDF links now use `/assets/<uuid>` without a `.pdf` extension. Fetch
verifies the PDF magic bytes. The first, middle and latest sampled decision
pages and their asset links were verified live.

Some undertaking hrefs supplied by the API return 404, including HSBC Bank
(Singapore) Limited and Cantley Lifecare. They are recorded as fetch failures,
left pending, and never checkpointed as successful. Bud Studio's undertaking
is reachable and its complete text is in hydration data.

## Work units and offline derivation

A discovery unit per collection/day fetches raw listing pages. `plan()` then
reads the persisted snapshots and emits one unit per decision, filtered by
published date. This also works when discovery was checkpointed by a previous
process. Listing counts and unique IDs must reconcile before planning details.
The ingest driver supplies its snapshot directory through the existing
`configure()` hook. Failed detail units stay pending while other units proceed.

A decision unit stores its raw HTML and PDFs together. PDF snapshot parameters
carry the original detail HTML, its SHA-256 and the listing row so `parse()`
needs only that snapshot. Full grounds text, paragraph/page boundaries and
provenance are retained. Undertakings without PDFs use their full rich text.
No summary is presented as full grounds for a decision with no PDF.

Citation extraction searches the first three pages, supports SGPDPC, SGPDPCS
and NFA, and normalises OCR spacing. Missing citations retain a provisional
slug URN. **A missing citation is not proof of a scan:** Henry Park Primary
School Parents' Association has readable PDF text without a neutral citation.

Financial penalties come from explicit penalty phrases in the case summary,
not a bare dollar amount or a cited comparator in the grounds. Multiple
amounts in one explicit penalty phrase are totalled across respondents.
`0` has a reason; `null` remains unknown. The source summary is retained for
checking the extraction.

## Optional local OCR

```
pip install -e ".[pdf,ocr]"
python scripts/fetch_pdpc_ocr_models.py
```

The separate setup command downloads and checksum-verifies the model files
from RapidOCR's manifest into ignored `data/ocr-models/`. Set
`SGCORPUS_OCR_MODELS` if moved. Parsing supplies explicit local model paths,
never invokes model downloads, and fails loudly if an image-bearing page needs
OCR but dependencies/models are absent. OCR runs on low-text image pages;
original PDF snapshots always remain available. Native text is preserved.

Measured local OCR smoke test: rasterising the live MCST 4869 cover to an
image-only PDF recovered **1/1 page**, including **[2026] SGPDPC 1**. This is
an OCR capability check, not a population citation-coverage measurement.

## Fixtures and gate

`pdpc_detail.html` contains only the live MCST article hydration record;
`pdpc_cover.pdf` contains only its first page; `pdpc_listing.json` is a one-row
listing fixture. Full source payloads stay in ignored `data/`.

Phase 4 remains open. Population ingestion and citation measurements are
recorded below when available; ignored obligation filters cannot satisfy the
original roadmap gate.

## Rate and posture

One request per 1.5 seconds, including PDF downloads; robots.txt allows `/`.
Honest project User-Agent. Full text stays local; only deliberate small test
fixtures are committed.


## Publication identity correction and recovery pass

Population validation found that some uncited summaries cite earlier decisions
in their reasoning. Citation extraction now accepts only a standalone citation
line (or an explicit `Decision Citation:` line) in the first three pages, not
an arbitrary citation in the body. Live variants include bracketed numbers
(`[2018] SGPDPC [3]`) and reconsiderations (`SGPDPCR`). Reconsideration attachments
do not inherit the original decision's penalty from the listing summary.

PPLingo has two different full PDFs, published on 21 March and 23 May 2024,
under the same `[2023] SGPDPC 12` citation. Each PDF therefore has a stable
`:publication-<snapshot-sha256>` child URN, with its printed citation's URN
stored in `meta.canonical_urn`. A bare identifier lookup returns an explicit
`ambiguous_publication` when multiple texts exist. It never arbitrarily
replaces one publication with another. `pdpc_decisions` filters by obligations,
outcomes, penalty, sector and year; aggregates count distinct canonical
identifiers and exclude unknown/conflicting penalties.

Initial population fetch: **268/269 decision entries**, **21/118 undertakings**;
98 failed hrefs remained pending. Citation coverage after correcting comparator
misidentification: **205/269 (76.21%)** source decision entries. **0/271 fetched
PDFs triggered the low-text OCR fallback**; many uncited publications are
machine-readable summaries without a neutral citation. The earlier ?25% scans?
assumption is not supported by this sample. OCR capability was separately
verified with the rasterised-cover check above.

PDPC's published `/sitemap.xml` supplies corrected undertaking URLs. The
adapter now snapshots that sitemap and, after a listing href returns 404,
tries only source-supplied candidates. It accepts one only if the detail page's
exact normalised title and publication date match the listing. A recovery pass
is running; updated counts will be recorded here. No URLs are invented and
no source errors are counted as ingested decisions.
