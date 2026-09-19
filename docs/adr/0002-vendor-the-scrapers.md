# ADR 0002 — Rewrite the prototype scrapers as adapters rather than wrap them

**Status:** accepted · **Date:** 2026-09-19

## Context

Four working prototypes already exist: `sgstatutescraper`, `elitiscraper`, `pdpcscraper`, `hansardscraper`. Three options were considered — git submodules, shelling out to them unchanged, or vendoring and rewriting.

## Decision

Vendor and rewrite. Each becomes a source adapter implementing a shared `plan` / `fetch` / `parse` protocol. The upstream repos are left untouched and continue to stand on their own.

## Rationale

The blocking defects are all at the storage boundary, which is precisely where an external wrapper has no reach:

- `hansardscraper` truncates every sitting day to Excel's 32,767-character cell limit and stores one row per day with no speaker structure. The debate is destroyed before any wrapper could see it.
- `elitiscraper` computes word and paragraph counts from the judgment text and then discards the text. There is nothing downstream to recover.
- `sgstatutescraper` guesses SSO short codes from title initials, hardcodes `Act/AA2004` into every provision URL, extracts provision text and omits it from the CSV writer, and caps at ten Acts.

A wrapper can reformat an output. It cannot restore data the producer never wrote.

Beyond that, the four have no shared HTTP client, so rate limiting and `robots.txt` compliance would have to be enforced four times, and none of them checkpoints — `elitiscraper` holds a multi-year scrape in memory and writes once at the end.

## Consequences

The prototypes stop being load-bearing and become reference implementations and a historical record of what each source's markup looked like. Their READMEs and the PDPC analysis outputs stay valuable.

Fixes land here rather than upstream, so the upstream repos will drift. Each source note in `docs/sources/` names the specific defects the rewrite must not reproduce, so the knowledge in those prototypes transfers even where the code does not.

`pdpcscraper` is the exception that proves the rule: its retry logic, citation regex and PDF extraction are sound and port over largely intact.
