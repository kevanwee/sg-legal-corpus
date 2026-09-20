# Source: Singapore Statutes Online

**Authority:** AGC · **Corpus:** `act`, `sl` · **Adapter:** `sso` · **Status:** implemented; gate verification in progress
**Prototype:** [sgstatutescraper](https://github.com/kevanwee/sgstatutescraper)

The hardest parse of the four and the most valuable output. This is the only source where point-in-time versioning is both available and essential.

## Endpoints

```
GET https://sso.agc.gov.sg/Browse/Act/Current/All?PageSize=500&SortBy=Title&SortOrder=ASC
GET https://sso.agc.gov.sg/Browse/SL/Current/All?PageSize=500
GET https://sso.agc.gov.sg/Act/{SHORT_CODE}?WholeDoc=1
GET https://sso.agc.gov.sg/Act/{SHORT_CODE}?ProvIds={id}
GET https://sso.agc.gov.sg/Act/{SHORT_CODE}?ValidDate={YYYYMMDD}        <- point-in-time
GET https://sso.agc.gov.sg/Act/{SHORT_CODE}/History
```

`ValidDate` is the key to the entire `as_of` feature. It is the reason legislation is worth the parsing effort rather than being deferred to a manual PDF collection.

## Structure

- Browse listing: `table.browse-list`, each row an `a.non-ajax` whose **`href` carries the real short code**.
- Whole-document view: `div#tocPanel` holds the provision tree as `a.nav-link`; provision bodies are in the main pane, addressed by the fragment id in each TOC href.
- History view: amendment records with amending Act and commencement date.

## Work units

1. One per listing page (Acts, then SL).
2. One per Act — whole-document fetch.
3. One per Act — history fetch.
4. One per (Act, distinct commencement date) for point-in-time versions. Only dates that appear in the history are fetched; the space is sparse, not daily.

Stage 4 is the expensive one and is gated behind an explicit `--versions` flag so a first pass can build the current statute book quickly and add history later.

## Record mapping

```
urn:sg:act:PDPA2012                    the Act        parts -> provisions
urn:sg:act:PDPA2012:s13                a provision
urn:sg:act:PDPA2012:s13@2021-02-01     a version of that provision
urn:sg:sl:PDPA2012:S362-2021:r4        SL regulation
```

Provision records carry `in_force_from` / `in_force_to` derived from the history view. A provision with a single version still gets the fields populated, so the query path is uniform.

## Defects in the prototype this adapter must not inherit

**1. Short codes guessed from titles.** `create_acronym()` builds an acronym from the initials of the title words. SSO short codes are official abbreviations that frequently do not follow that pattern, so a large share of the statute book resolves to a code that either 404s or — the dangerous case — matches a *different* Act. The fix is not a better heuristic. The `href` on the browse listing contains the actual code; read it.

**2. Every provision URL points at the wrong Act.** `get_provisions()` constructs:

```python
prov_url = f"https://sso.agc.gov.sg/Act/AA2004?WholeDoc=1&ProvIds={prov_id}#{prov_id}"
```

`AA2004` is hardcoded — a copy-paste left in place. Every URL the prototype has ever emitted points into the Arbitration Act 2001 regardless of which Act was being scraped.

**3. Provision text is extracted and then thrown away.** `get_provisions()` populates `prov['content']`, and the CSV writer omits it. The output is a table of contents, not a corpus.

**4. Capped at ten Acts.** `statutes[:10]  # limit to first 10 for testing`, still in `main()`.

**5. No subsidiary legislation, no repealed Acts, no versions.** Current Acts only.

**6. No provision hierarchy.** The TOC regex `^(\d+[A-Z]*)\s+(.+)` matches section-level entries and discards Parts and Divisions, so structural context is lost. Subsections are not captured at all.

**7. Titles as the primary key.** `statutes.csv` is a list of title strings, which cannot be joined to anything and breaks when a title is revised.

## The 2020 Revised Edition problem

The 2020 Revised Edition renumbered the entire statute book from `Cap N` citations to year-based ones, and renumbered provisions within many Acts. Every judgment before 2021 cites provisions in a form that no longer appears on SSO.

Without an alias table covering both the chapter-number mapping and the provision renumbering, citation resolution fails on the majority of the case law corpus. This is Phase 5 work but it is a Phase 3 data requirement, and the history view is where the renumbering is recorded.

## Rate limit and posture

1 request / 6s, single connection. No redistribution of provision text — see [legal-posture.md](../legal-posture.md#singapore-statutes-online-agc). The publishable derivative is the structural and amendment skeleton.


## Live verification, 19?20 September 2026

Comma-separated `ProvIds` works. A request for `pr13-,pr14-,pr15-` returned
all three provisions; repeated `ProvIds` parameters returned only the first.
A full batch of **129/129 PDPA TOC anchors** returned bodies with no missing
anchors (606,938 response bytes). `ProvIds=root-.` does not work. The adapter
chunks long URLs, never text, and refuses a response missing any requested ID.

Current browse pagination is a path component: page two is `/All/1`, not
`PageIndex=2`. The title columns contain **500 + 25 = 525 unique current Acts**.
The handoff's 501 on page one included an action link. Codes come solely from
those title-column hrefs. The Companies Act is **CoA1967**, not CA1967 (Currency
Act). `/Act/<code>/History` is a soft 404; legislative history is the `xv-`
TOC entry within the document itself.

The version popover supplies dated hrefs and the selected-version button
supplies the actual version returned. Both are checked. Historical HTML that
returns a different version is rejected. Version intervals use inclusive
starts and exclusive ends at the next source-enumerated version. `--versions`
fetches the enumerated history; no calendar dates or historical text are guessed.

`--include-sl` adds the current SL listing; its hrefs contain an Act code and
SL identifier (for example `AA2004-R5`) and often a `DocDate` query. The original
href and query are retained. `--include-repealed` adds the published repealed
Act listing (298 title rows observed). Repealed records remain stored and are
not returned as current law. Parser fixtures cover the 2021 PDPA amendment.

Source text is partitioned at TOC anchors, preserving long title, Parts,
Divisions, provisions, schedules and legislative-history material. Numbered
subsections and letter/roman paragraphs also have child records. Full section
text is retained so a provision query does not require stitching partial text.
Chapter references in legislative history are retained as **alias candidates**,
not silently asserted mappings: references to other Acts can occur there.
Phase 5 must resolve those candidates against the preserved source context.
`dates.issued` currently records the selected source version date, explicitly
labelled `issued_basis=source_version_date`; it is not represented as assent.

Measured text checks: **30/30 current provisions** and **20/20 provision/date
pairs** (ss 13?22, excluding 15A, on 31 January and 1 February 2021) match the
live-fetched HTML after whitespace and curly-quote normalisation. The latter
checks exercise `get_provision` through SQLite across the 2021 amendment.
Source pages were fetched first; these are comparisons to source bytes, not
fixture-only assertions. Current Act href coverage remains open until the
serialized traversal and retry pass finish; initial pass: **394/525** with
TOCs, **131/525** HTTP-200 responses without a TOC. Two sampled failures later
returned proper statute pages and are being retried at the same six-second floor.

No phase is marked complete here. Remaining limits include exhaustive SL and
repealed-Act validation, resolving chapter/renumbering candidates, and checking
uncommenced material independently of a consolidation's version date.
