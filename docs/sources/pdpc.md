# Source: PDPC enforcement decisions

**Authority:** PDPC · **Corpus:** `pdpc` · **Adapter:** `pdpc` · **Status:** stub
**Prototype:** [pdpcscraper](https://github.com/kevanwee/pdpcscraper)

The best-engineered of the four prototypes. This adapter is mostly a port rather than a rewrite: the retry logic, the PDF citation extraction and the penalty regexes are sound and carry over close to as-is.

## Endpoints

```
GET  https://www.pdpc.gov.sg/all-commissions-decisions          (SPA shell)
XHR  .../getenforcementcase?...                                  (JSON, intercepted)
GET  https://www.pdpc.gov.sg/{decision-slug}                     (detail page)
GET  https://www.pdpc.gov.sg/.../*.pdf                           (grounds of decision)
```

The listing is JavaScript-rendered, so discovery needs a browser (`browser` extra). The prototype's approach — drive Playwright but intercept the underlying `getenforcementcase` JSON responses and prefer those over the scraped DOM — is the right one and is kept.

**Worth attempting first:** call `getenforcementcase` directly with the parameters observed during interception. If it responds without a browser session, the `browser` dependency disappears from this adapter entirely. The prototype never tests this.

## Work units

One per listing page for discovery; one per decision for detail + PDF. Checkpointed per decision.

## Record mapping

```
urn:sg:pdpc:2024_SGPDPC_3        the decision
urn:sg:pdpc:x-{slug}             provisional, where the PDF is a scan
```

## What carries over unchanged

- `safe_get` with exponential backoff and explicit 429 handling.
- `CITATION_RE` — `\[\d{4}\]\s+SGPDPCS?\s+\d+(?:\s*\(NFA\))?` — covers the `SGPDPC`, `SGPDPCS` and `(NFA)` variants correctly.
- Citation extraction from the first three PDF pages, with a page-text fallback.
- Context-aware penalty extraction (`financial penalty of $X`) before any bare dollar-amount fallback.
- `normalise_case_name` turning "Breach of the Protection Obligation by Acme Pte Ltd" into "Re Acme Pte Ltd".

## Changes required

**1. All nine obligations, not just Protection.** The prototype ticks the Protection checkbox and filters client-side on `"protection" in nature`. Correct for a focused analysis, wrong for a corpus. Remove the filter at ingest; keep it as a query parameter on `pdpc_decisions`.

**2. Numeric penalties.** `penalty_sgd` is an integer. `"$25,000"` is kept separately as `penalty_stated`. Every downstream consumer currently re-parses the string.

**3. Distinguish zero from unknown.** The prototype returns `"None"`, `""` or `"$X"` from one function, conflating "no penalty imposed" with "could not determine". Split into `penalty_sgd` (`0` / number / `null`) plus `no_penalty_reason`.

**4. Full decision text.** The prototype keeps only the first paragraph over 80 characters from the `.rte` block as a summary. Extract the full grounds from the PDF.

**5. OCR fallback.** Roughly 25% of decisions are scanned images with no machine-readable text, so they lose their citation entirely and fall back to a provisional URN. An OCR pass recovers most of them.

**6. Commission undertakings.** A separate listing the prototype does not touch. Same record shape, `decision_types: ["Undertaking"]`.

**7. Sector classification.** Present in the source metadata, not captured. Valuable for the "what is the going rate for this kind of breach" query.

**8. JSONL, not Excel.** The analysis outputs (`pdpc_analysis.xlsx`, `pdpc_trends.png`) stay useful and move to a separate analytics step reading Parquet — they are a consumer of the corpus, not part of it.

## Coverage denominator

The listing's own total per obligation filter. Citation coverage is tracked separately, since a decision with a provisional URN is ingested but not fully identified.

## Rate limit and posture

1 request / 1.5s including PDF downloads. The most permissive of the four for metadata redistribution — respondent, obligations, decision type, penalty, date and citation are factual, and the prototype already publishes that shape. Full grounds text stays local.
