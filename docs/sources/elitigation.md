# Source: eLitigation

**Authority:** Supreme Court / State Courts / Family Justice Courts · **Corpus:** `judgment` · **Adapter:** `elitigation` · **Status:** stub
**Prototype:** [elitiscraper](https://github.com/kevanwee/elitiscraper) (see also [crimewatch](https://github.com/kevanwee/crimewatch), [codeoflaw](https://github.com/kevanwee/codeoflaw))

Written judgments from 2000 onwards. The highest-value corpus and the one with the tightest terms of use — read [legal-posture.md](../legal-posture.md#elitigation-supreme-court--judiciary) before any backfill.

## Endpoints

```
GET https://www.elitigation.sg/gd/Home/Index?Filter={FILTER}&YearOfDecision={YYYY}&SortBy=Score&CurrentPage={N}
GET https://www.elitigation.sg/gd/s/{case_identifier}
```

`Filter` values: `SUPCT` (Supreme Court), `STATECT` (State Courts), `FAMCT` (Family Justice Courts). The prototype hardcodes `SUPCT` and therefore misses the State Courts entirely — which is most of the criminal and civil-claims output by volume.

## Structure

Listing page: `div.card.col-12` per result, with `span.gd-addinfo-text` holding the neutral citation and `a.gd-cw` the catchwords.

Judgment page: `div.Judg-1` paragraphs (numbered), `div.Judg-Author` / `div.Judg-Sign` for the judge, `div.Judg-Lawyers` for counsel, `div.Judg-EOF` terminating the body. Heading levels appear as `Judg-Heading-1` etc.

## Work units

One per (filter, year, listing page) for discovery; one per judgment for retrieval. Both checkpointed, so an interrupted backfill resumes at the page and judgment it reached.

## Record mapping

```
urn:sg:judgment:2024_SGCA_15            the judgment   parts -> paragraphs
urn:sg:judgment:2024_SGCA_15:para42     a paragraph
```

Paragraph URNs use the judgment's own numbering, because that is the only numbering a court accepts in a pincite. Paragraph records are what `search` returns as hits, so a result is citable without further work.

## Defects in the prototype this adapter must not inherit

**1. No full text.** `scrape_case_details` computes `word_count` and `paragraph_count` from the judgment divs and returns the counts, discarding the text. The output is a statistics table. A corpus needs the judgment.

**2. Supreme Court only.** `Filter=SUPCT` is hardcoded in the list URL.

**3. All results held in memory, written once at the end.** `all_cases` accumulates across every year and page; `to_csv` runs after the final loop. A failure at hour three loses hours one and two, and a multi-year backfill is one network blip away from total loss.

**4. URL construction by string mangling.** The case identifier is transformed with chained `.replace()` calls to build the judgment URL. It works for the common shape and breaks on citations containing parentheses in unexpected positions — `SGHC(I)` and `SGHC(A)` among them. The listing markup contains the href; read it.

**5. Mojibake patched per character.** `.replace("â€"", "-")`, `.replace("Â", "")` scattered through the parser. The underlying problem is an encoding mismatch and belongs in one normalisation step, not in a growing list of specific broken sequences.

**6. Counsel as one joined string.** `LegalParties` concatenates every `Judg-Lawyers` div and strips semicolons, producing a blob that cannot be queried by firm or by which side counsel appeared for.

**7. No coram.** Only a single `Author` is captured, so an appellate bench of three or five is recorded as one judge.

**8. No parties, no outcome, no case number.** All present on the page, none extracted.

**9. Paragraph count taken from the last paragraph's first word.** If the final `Judg-1` div does not begin with a digit — an annex, a schedule, a signature block — the count silently becomes 0.

**10. Unbounded pagination loop.** `while True` exits only on a non-200 or an empty card list. A soft error page returning 200 with no cards is indistinguishable from the end of results.

## Coverage denominator

The listing's own result count per (filter, year). A year ingested at 60% is a reported gap, not a clean exit.

## Rate limit and posture

1 request / 3s, single connection, daily cap enforced by the client. Judgment text stays local. The publishable derivative is the citator layer: neutral citation, court, date, coram, catchwords, parties, outcome, and the citation graph.
