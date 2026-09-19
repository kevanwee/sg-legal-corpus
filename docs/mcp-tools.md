# MCP tool surface

Nine tools. The design rule throughout: **every response carries the URN and the source URL**, so an agent's answer can always be traced back to something a human can open, and a wrong answer is falsifiable rather than merely plausible.

The second rule: **failures are structured, not empty**. A tool that finds nothing says what it looked for and why the lookup failed. Returning `[]` invites an agent to fall back on its own recollection, which is exactly the failure this corpus exists to prevent.

---

## `search`

Full-text across one or more corpora.

```jsonc
{
  "query": "purposive interpretation of taxing statutes",
  "corpus": ["judgment", "hansard"],     // optional; default all
  "authority": null,                      // AGC | SUPCT | STATECT | FAMCT | PDPC | PARL
  "court": ["SGCA", "SGHC"],             // judgment only
  "date_from": "2015-01-01",
  "date_to": null,
  "limit": 20
}
```

Returns ranked hits with URN, citation, title, date, a snippet with match highlighting, and the BM25 score. Snippets are windowed around the match and carry the URN of the smallest containing part, so a hit inside a judgment resolves to a paragraph pincite rather than to the whole judgment.

---

## `get_document`

```jsonc
{ "urn": "urn:sg:judgment:2024_SGCA_15", "parts": "all" }
```

`parts` accepts `"all"`, `"none"`, or a list of part URNs. `"none"` returns the envelope and metadata without the text, which is how an agent surveys candidates cheaply before committing context to one.

---

## `get_provision`

The point-in-time tool.

```jsonc
{
  "urn": "urn:sg:act:CA1967:s157A",
  "as_of": "2019-04-01"
}
```

Returns the version in force on that date, and always echoes:

```jsonc
{
  "urn": "urn:sg:act:CA1967:s157A@2019-04-01",
  "in_force_from": "2018-03-23",
  "in_force_to": "2021-01-31",
  "is_current": false,
  "superseded_by": "urn:sg:act:CA1967:s157A@2021-02-01",
  "text": "...",
  "source_url": "https://sso.agc.gov.sg/..."
}
```

If nothing was in force on that date, the response is an explicit `not_in_force` with the ranges that were, and the commencement or repeal date that explains the gap. It never falls back to current text. Silently returning the current version for a historical query is the single most damaging thing a legal corpus can do, because the answer looks right.

---

## `list_amendments`

```jsonc
{ "urn": "urn:sg:act:PDPA2012:s13" }
```

Returns the provision's version history: each version's date range, the amending Act, the commencement instrument, and a URN for each version. Feeds directly into [sg-deadline](https://github.com/kevanwee/sg-deadline)-style computation where a limitation period turns on which version applied.

---

## `find_citing`

```jsonc
{
  "urn": "urn:sg:judgment:2007_SGCA_37",
  "kind": ["considers", "applies", "distinguishes"],
  "limit": 50
}
```

The citator. Returns citing documents with the citing paragraph's URN, the raw citing sentence, the treatment kind, and the confidence. Treatment classification is Phase 5; until then everything comes back as `cites` with confidence 1.0, which is honest rather than impressive.

---

## `parliamentary_intent`

The tool the rest of the corpus exists to make possible.

```jsonc
{
  "urn": "urn:sg:act:PDPA2012:s26D",
  "include_speeches": true
}
```

Walks provision → enacting or amending Act → Bill → second-reading sitting → the speeches in that section, and returns the chain with a confidence on the Bill↔sitting join:

```jsonc
{
  "provision": "urn:sg:act:PDPA2012:s26D",
  "introduced_by": "urn:sg:act:PDPAA2020",
  "bill": { "urn": "urn:sg:bill:2020:37", "title": "Personal Data Protection (Amendment) Bill" },
  "readings": [
    {
      "stage": "Second Reading",
      "sitting": "urn:sg:hansard:2020-11-02",
      "section": "urn:sg:hansard:2020-11-02:s5",
      "confidence": 0.94,
      "speeches": [
        {
          "urn": "urn:sg:hansard:2020-11-02:s5:sp3",
          "speaker": "Mr S Iswaran",
          "role": "Minister for Communications and Information",
          "excerpt": "..."
        }
      ]
    }
  ],
  "caveat": "Bill-to-sitting linkage is inferred from title matching; verify against the sitting record before citing."
}
```

The `caveat` field is always present on inferred links and is written to be quoted, because an agent that passes it through has told the user something true about the limits of the answer.

This matters for real work: s 9A of the Interpretation Act 1965 makes purposive interpretation mandatory and expressly admits parliamentary material, so the second-reading speech is not colour — it is an admissible aid to construction that a Singapore court will actually consider.

---

## `pdpc_decisions`

```jsonc
{
  "obligation": ["Protection"],
  "outcome": ["Financial Penalty"],
  "penalty_min": 10000,
  "penalty_max": null,
  "year_from": 2020,
  "sector": null,
  "limit": 50
}
```

Returns matching decisions with citation, respondent, obligations, penalty and summary, plus aggregate counts for the filter set. The aggregates make the common question — "what is the going rate for this kind of breach" — answerable in one call instead of fifty.

---

## `resolve_citation`

```jsonc
{ "citation": "[2024] 1 SLR 123" }
```

Returns the canonical URN, the canonical display form, what kind of match it was (exact / alias / fuzzy), and the confidence. On failure it returns `resolved: false` with near-misses and the reason, which is the shape [citecheck](https://github.com/kevanwee/citecheck) needs to distinguish "this authority does not exist" from "this corpus does not have it" — a distinction that matters enormously when the output is used to flag a hallucinated citation in someone's draft.

---

## `corpus_stats`

```jsonc
{ "corpus": null }
```

Per-corpus document counts, date coverage, last fetch time, coverage percentage against the source's own denominator, adapter version and parser revision.

Exposing this as a tool rather than burying it in a CLI is deliberate: an agent that is about to answer a question about 2003 case law should be able to discover that the corpus starts in 2020, and say so.

---

## Response conventions

Every response object includes:

| Field | Meaning |
|---|---|
| `urn` | Canonical identifier |
| `source_url` | Where a human verifies this |
| `retrieved_at` | When this record was fetched |
| `confidence` | Present whenever any inference was involved |
| `caveat` | Present whenever the answer has a known limitation |

Errors are typed: `not_found`, `not_in_force`, `ambiguous`, `out_of_coverage`, `unresolved`. Each carries enough context for an agent to explain the failure to a user instead of papering over it.
