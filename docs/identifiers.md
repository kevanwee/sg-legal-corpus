# Identifiers

One URN scheme across all four corpora. Every document, every addressable part of a document, and every point-in-time version of a provision has exactly one canonical identifier.

## Grammar

```
urn:sg:<corpus>:<work>[:<part>][@<as-of>]

corpus  := act | sl | judgment | pdpc | hansard | bill
work    := source-specific work key, see below
part    := provision, paragraph, or speech locator
as-of   := ISO-8601 date (YYYY-MM-DD)
```

Case-sensitive. No whitespace. `work` and `part` segments are restricted to `[A-Za-z0-9._-]`, which keeps a URN safe in a file name, a URL path and a SQLite key without escaping.

## Per-corpus rules

### `act` — Acts of Parliament

Work key is the AGC short code as SSO itself uses it, **read from the listing page href, never derived from the title**.

```
urn:sg:act:PC1871                Penal Code 1871
urn:sg:act:CA1967                Companies Act 1967
urn:sg:act:PDPA2012              Personal Data Protection Act 2012
urn:sg:act:PC1871:s300           s 300
urn:sg:act:PC1871:s300-1         s 300(1)
urn:sg:act:PC1871:s300-1-a       s 300(1)(a)
urn:sg:act:CA1967:s157A@2019-04-01   as in force on 1 April 2019
```

Deriving the short code from the title is the central defect in the prototype SSO scraper: it builds an acronym from initials, which produces wrong codes for a large share of the statute book and silently fetches either nothing or, worse, the wrong Act. The href on the browse listing carries the real code. See [sources/sso.md](sources/sso.md).

Sub-provision punctuation maps to hyphens: `s 300(1)(a)(ii)` becomes `s300-1-a-ii`. The mapping is bijective and implemented in `urn.py` so a URN round-trips back to a display citation.

### `sl` — Subsidiary legislation

Work key is the parent Act code plus the SL number.

```
urn:sg:sl:CA1967:S329-2015       S 329/2015 made under the Companies Act 1967
urn:sg:sl:PDPA2012:S362-2021:r4  regulation 4
```

### `judgment` — Written judgments

Work key is the neutral citation with brackets and spaces removed and the year leading.

```
[2024] SGCA 15    -> urn:sg:judgment:2024_SGCA_15
[2019] SGHC(I) 3  -> urn:sg:judgment:2019_SGHCI_3
[2023] SGHCR 7    -> urn:sg:judgment:2023_SGHCR_7
[2022] SGDC 112   -> urn:sg:judgment:2022_SGDC_112
```

Parentheses are stripped rather than encoded, because `SGHC(I)` and `SGHCI` cannot collide — no court emits both.

Parts are paragraph pincites: `urn:sg:judgment:2024_SGCA_15:para42`. Paragraph numbers come from the judgment's own numbering, which is the only numbering a court will accept in a citation. Judgments without numbered paragraphs (rare, mostly pre-2003 grounds of decision) use `:p12` for a page reference instead, flagged in the record.

### `pdpc` — Enforcement decisions

Neutral citation, same transform as judgments.

```
[2024] SGPDPC 3       -> urn:sg:pdpc:2024_SGPDPC_3
[2023] SGPDPCS 12     -> urn:sg:pdpc:2023_SGPDPCS_12
[2021] SGPDPC 4 (NFA) -> urn:sg:pdpc:2021_SGPDPC_4_NFA
```

Roughly a quarter of PDPC decisions have no machine-readable citation because the PDF is a scan. Those get a provisional URN derived from the decision page slug, prefixed to make the provisional status explicit and unmistakable in any output:

```
urn:sg:pdpc:x-breach-of-protection-obligation-by-acme-pte-ltd
```

A provisional URN is replaced by the real one if OCR or a later publication supplies the citation, with the old URN retained as an alias so existing references keep resolving.

### `hansard` — Parliamentary debate

Keyed by sitting date, then section, then speech.

```
urn:sg:hansard:2024-02-06            the sitting
urn:sg:hansard:2024-02-06:s3         the third section of that sitting
urn:sg:hansard:2024-02-06:s3:sp12    the twelfth speech in that section
```

Section and speech indices are positional within the source payload and stable as long as the source payload is stable. The speech record carries the speaker name and constituency so a positional index is never the only handle on a quotation.

### `bill` — Bills

```
urn:sg:bill:2012:24     Bill 24 of 2012
```

Bills are the hinge between `act` and `hansard`. They are not a separately scraped corpus in Phase 0; they are synthesised during enrichment from the enactment metadata on the SSO side and the section titles on the Hansard side.

## The `@as-of` suffix

Only meaningful on `act` and `sl` parts. It selects the version of that provision in force on the given date.

```
urn:sg:act:CA1967:s157A              current version
urn:sg:act:CA1967:s157A@2019-04-01   version in force on 1 April 2019
```

A bare provision URN means *current*, which is convenient and dangerous in equal measure. `get_provision` therefore always echoes the resolved `in_force_from` / `in_force_to` in its response, so a caller that forgot to pass `as_of` can still see which version it received.

Resolution failures are explicit. If no version was in force on the requested date — the provision had not commenced, or had been repealed — the answer is a structured "not in force on that date, here is the range that was", never a silent fallback to the current text. Falling back silently is the failure mode that produces confidently wrong advice.

## Aliases

An alias table maps alternative forms to canonical URNs:

- Law-report citations: `[2024] 1 SLR 123` → `urn:sg:judgment:2024_SGCA_15`
- Case names: `Spandeck Engineering v DSTA` → its neutral citation
- Historic statute chapter numbers: `Cap 50` → `urn:sg:act:CA1967`
- Renumbered provisions across the 2020 revised edition

The chapter-number aliases matter more than they look. The 2020 Revised Edition renumbered the entire statute book from `Cap N` to year-based citations, so every pre-2021 judgment cites provisions in a form that no longer appears anywhere on SSO. Without the alias table, citation resolution fails on most of the case law.

Aliases are many-to-one and never authoritative in the other direction: a canonical URN has exactly one canonical display form, produced by `urn.to_citation()`.
