# Corpus specification

## The Document envelope

One shape for every record in every corpus. Source-specific material lives in `meta`, which is typed per corpus; everything outside `meta` means the same thing everywhere.

```jsonc
{
  "urn": "urn:sg:hansard:2024-02-06:s3:sp12",
  "corpus": "hansard",                  // act | sl | judgment | pdpc | hansard | bill
  "authority": "PARL",                  // AGC | SUPCT | STATECT | FAMCT | PDPC | PARL
  "title": "Second Reading — Personal Data Protection (Amendment) Bill",
  "citation": "Sing. Parl. Deb., 6 Feb 2024",   // canonical display form
  "dates": {
    "issued": "2024-02-06",
    "in_force_from": null,              // act / sl only
    "in_force_to": null
  },
  "language": "en",                     // en | ms | zh | ta
  "text": "Mr Speaker, Sir, I beg to move ...",
  "parts": [],                          // child URNs, ordered
  "parent": "urn:sg:hansard:2024-02-06:s3",
  "meta": { "...": "corpus-specific, see below" },
  "provenance": {
    "source_url": "https://sprs.parl.gov.sg/search/getHansardReport/?sittingDate=06-02-2024",
    "retrieved_at": "2026-09-19T04:11:22Z",
    "snapshot_sha256": "9f2a...e41",
    "adapter": "hansard",
    "adapter_version": "1.0.0",
    "parser_rev": 1
  }
}
```

### Field rules

**`text` is plain text and complete.** No truncation anywhere in the pipeline, for any reason. Markup is discarded during normalisation, apart from paragraph boundaries, which are load-bearing for pincites.

**`parts` holds child URNs, not nested objects.** A statute with 400 sections is 401 records, not one deeply nested record. Flat records mean a provision can be retrieved, indexed, versioned and cited independently — which is how provisions actually behave.

**`dates.issued` is always populated.** For a judgment it is the date of decision; for an Act, the date of assent or of the revised edition; for a Hansard speech, the sitting date; for a PDPC decision, the published date.

**`in_force_from` / `in_force_to` are populated only for `act` and `sl`.** `in_force_to` of `null` means currently in force. These two fields are what make `as_of` queries possible; a record without them cannot answer a point-in-time question and is marked incomplete in `corpus_stats`.

**`provenance` is mandatory and never inferred.** A record whose snapshot hash does not match a file in the snapshot store fails validation.

## Per-corpus `meta`

### `act` / `sl`

```jsonc
{
  "short_code": "PDPA2012",
  "long_title": "An Act to govern the collection, use and disclosure of personal data ...",
  "chapter": "Cap. 26",                 // pre-2020 chapter number, if any
  "revised_edition": "2020",
  "provision_number": "13",             // part records only
  "provision_heading": "Consent required",
  "part": "Part 4",
  "division": "Division 1",
  "enacted_by": "urn:sg:act:PDPA2012",
  "amended_by": ["urn:sg:act:PDPAA2020"],
  "repealed": false,
  "version_seq": 3                       // nth version of this provision
}
```

### `judgment`

```jsonc
{
  "neutral_citation": "[2024] SGCA 15",
  "report_citations": ["[2024] 1 SLR 123"],
  "court": "SGCA",                       // SGCA | SGHC | SGHC(A) | SGHC(I) | SGHCR | SGDC | SGMC | SGFC
  "case_number": "Civil Appeal No 42 of 2023",
  "parties": { "appellants": [], "respondents": [] },
  "coram": ["Sundaresh Menon CJ", "Judith Prakash JCA"],
  "author": "Sundaresh Menon CJ",
  "counsel": [{ "name": "...", "firm": "...", "for": "appellant" }],
  "catchwords": ["Contract — Breach", "Damages — Remoteness"],
  "outcome": "Appeal allowed",
  "paragraph_count": 118,
  "word_count": 24310,
  "has_full_text": true
}
```

`catchwords` are the single most valuable field for subject retrieval and the reason [crimewatch](https://github.com/kevanwee/crimewatch) works at all — the courts have already done the classification. They are stored as a list, not a comma-joined string, so a filter can match one without substring matching the others.

### `pdpc`

```jsonc
{
  "neutral_citation": "[2024] SGPDPC 3",
  "citation_provisional": false,
  "respondent": "Acme Pte Ltd",
  "obligations": ["Protection", "Accountability"],
  "decision_types": ["Financial Penalty", "Directions"],
  "penalty_sgd": 25000,                  // integer cents-free SGD, or null
  "penalty_stated": "$25,000",           // as printed
  "no_penalty_reason": null,             // "warning" | "directions_only" | "no_breach" | "nfa"
  "sector": "Healthcare",
  "summary": "The organisation failed to ..."
}
```

`penalty_sgd` is an integer, not a string. The prototype writes `"$25,000"` and every downstream analysis re-parses it; storing the number once removes a class of bugs. `null` is genuinely unknown; a decision that imposed no penalty has `penalty_sgd: 0` and a populated `no_penalty_reason`, which is a distinction the prototype collapses.

`obligations` covers all nine DP obligations, not just Protection. The prototype hard-filters to Protection, which is the right call for a single analysis and the wrong one for a corpus.

### `hansard`

```jsonc
{
  "parliament_no": 14,
  "session_no": 2,
  "volume_no": 95,
  "sitting_no": 112,
  "sitting_type": "Parliament Sitting",
  "section_index": 3,
  "section_title": "Personal Data Protection (Amendment) Bill",
  "section_subtitle": "Second Reading",
  "speech_index": 12,
  "speaker": "Mrs Josephine Teo",
  "speaker_role": "Minister for Communications and Information",
  "constituency": "Jalan Besar GRC",
  "is_question": false,
  "question_no": null,
  "bill_ref": "urn:sg:bill:2024:7"
}
```

Speaker attribution is the whole point. The prototype concatenates an entire sitting day into one `DebateText` cell with no speaker structure, which makes it impossible to say who said what — and a Hansard quotation without a speaker is not usable as authority.

## Reference edges

`data/refs/*.jsonl`, one edge per line:

```jsonc
{
  "from": "urn:sg:judgment:2024_SGCA_15:para42",
  "to": "urn:sg:act:PDPA2012:s13",
  "kind": "cites",
  "raw": "section 13 of the Personal Data Protection Act 2012",
  "confidence": 1.0,
  "resolver": "statute_ref_v1"
}
```

`kind` is one of:

| kind | from → to | Source |
|---|---|---|
| `cites` | any → any | rule-based extraction from text |
| `considers` / `applies` / `distinguishes` / `follows` / `overrules` / `doubts` | judgment → judgment | signal-phrase classifier, Phase 5 |
| `amends` / `repeals` | act → act provision | SSO legislative history |
| `made_under` | sl → act | SSO |
| `enacted_by` | act provision → bill | enrichment |
| `debated_in` | bill → hansard section | enrichment, confidence-scored |

`confidence` below 1.0 means the edge was inferred rather than read. Every MCP response that traverses an inferred edge surfaces the confidence rather than hiding it.

Unresolved citations are kept as edges with `to: null` and the raw string preserved. They are the coverage metric for the citation resolver.

## Index schema

SQLite, built by `sgcorpus index`.

```sql
CREATE TABLE documents (
    urn            TEXT PRIMARY KEY,
    corpus         TEXT NOT NULL,
    authority      TEXT NOT NULL,
    title          TEXT,
    citation       TEXT,
    issued         TEXT,              -- ISO date
    in_force_from  TEXT,
    in_force_to    TEXT,
    parent         TEXT REFERENCES documents(urn),
    language       TEXT NOT NULL DEFAULT 'en',
    text           TEXT NOT NULL,
    meta           TEXT NOT NULL,     -- JSON
    provenance     TEXT NOT NULL      -- JSON
);

CREATE INDEX idx_documents_corpus_issued ON documents(corpus, issued);
CREATE INDEX idx_documents_parent        ON documents(parent);
CREATE INDEX idx_documents_force         ON documents(in_force_from, in_force_to);

-- External-content FTS5: the text lives once, in documents.
CREATE VIRTUAL TABLE documents_fts USING fts5(
    title, citation, text,
    content='documents',
    content_rowid='rowid',
    tokenize='porter unicode61 remove_diacritics 2'
);

CREATE TABLE refs (
    from_urn   TEXT NOT NULL,
    to_urn     TEXT,                  -- NULL = unresolved
    kind       TEXT NOT NULL,
    raw        TEXT,
    confidence REAL NOT NULL DEFAULT 1.0,
    resolver   TEXT
);

CREATE INDEX idx_refs_from ON refs(from_urn);
CREATE INDEX idx_refs_to   ON refs(to_urn);

CREATE TABLE aliases (
    alias      TEXT PRIMARY KEY,
    urn        TEXT NOT NULL REFERENCES documents(urn),
    kind       TEXT NOT NULL          -- report_citation | case_name | chapter | renumber
);

CREATE TABLE corpus_meta (
    corpus        TEXT PRIMARY KEY,
    document_count INTEGER NOT NULL,
    earliest      TEXT,
    latest        TEXT,
    last_fetch    TEXT,
    coverage_pct  REAL,               -- against the source's own denominator
    adapter_version TEXT,
    parser_rev    INTEGER
);
```

`documents_fts` uses FTS5 external-content so the text is stored once rather than twice. The `porter` tokenizer stems English; `remove_diacritics 2` handles the Malay and Tamil material that appears in vernacular Hansard sections without destroying them.

The tokenizer choice has a known cost: it stems section references poorly and does not tokenise `s 157(1)` usefully. Exact provision lookup therefore goes through `documents.urn` and the alias table, not through FTS. Keyword search and identifier resolution are different problems and are kept apart on purpose.

## Validation

JSON Schemas in `schemas/` are the machine-readable form of everything above, and CI validates every fixture against them. A schema change requires a `parser_rev` bump on every adapter it affects, which is enforced by a test rather than by discipline.
