# Handoff — sg-legal-corpus

For the next agent picking this up. Written 2026-09-19 at commit `1f65f11`.

Delete this file when Phases 2–4 are done; it is working state, not documentation.

---

## What this repo is

Singapore legal authority — statutes, case law, PDPC enforcement, Hansard — as one point-in-time corpus with an MCP server over it. It consolidates and rewrites four prototype scrapers on the same GitHub account (`sgstatutescraper`, `elitiscraper`, `pdpcscraper`, `hansardscraper`), which stay untouched upstream.

Read these before writing any code, in this order:

1. `README.md` — the shape of the thing
2. `docs/architecture.md` — five stages, the adapter protocol
3. `docs/corpus-spec.md` — the `Document` envelope, the SQLite schema
4. `docs/legal-posture.md` — **read this properly**, it constrains what you may do
5. `docs/sources/<source>.md` — the per-source plan you are implementing
6. `src/sgcorpus/adapters/hansard.py` — the reference implementation; copy its shape

## Environment

```bash
cd sg-legal-corpus
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -e ".[dev]"     # Windows; adjust for POSIX

./.venv/Scripts/python.exe -m pytest -q            # 36 tests, all offline
./.venv/Scripts/python.exe -m ruff check src tests
./.venv/Scripts/python.exe -m mypy src/sgcorpus    # strict, and CI enforces it
```

All three must stay green. CI also fails the build if any `.jsonl`/`.parquet`/`.db`/`.sqlite` file is committed outside `tests/fixtures/`.

## Phase status

| Phase | Scope | State |
|---|---|---|
| 0 | Foundations, contracts, Hansard reference adapter | done, `e5652f8` |
| 1 | Hansard depth: vernacular, question numbers, quality metrics | done, `1f65f11` |
| 2 | eLitigation — judgments | **blocked**, see below |
| 3 | SSO — legislation | research done, no code written |
| 4 | PDPC — enforcement decisions | not started, source verified reachable |
| 5–7 | Graph, MCP GA, freshness | not started |

Roadmap and exit gates: `docs/roadmap.md`. Each phase's gate is a checkable number, not a feeling. Do not mark a phase done without hitting its gate and recording the measurement.

---

## Rules that are not negotiable

These come from `docs/legal-posture.md` and from decisions the repo owner has already made. Do not quietly revise them.

1. **Never commit corpus data.** The repo ships the pipeline, not the payload. `data/` is gitignored and CI enforces it. Test fixtures under `tests/fixtures/` are the deliberate exception: small, trimmed, committed on purpose.
2. **Rate limits are floors.** `config.POLICIES` sets a per-source minimum interval. `SourcePolicy.merge` lets an operator go slower and silently ignores any attempt to go faster. Keep that property.
3. **`fetch` is the only method that touches the network.** `parse` must stay pure and offline so it is testable against fixtures. This is what makes a parser fix a local re-run instead of a re-crawl.
4. **Never truncate text.** The prototype Hansard scraper clamps every value to Excel's 32,767-character cell limit and so keeps about 6% of each sitting. That defect is most of why this repo exists.
5. **No silent fallbacks on point-in-time queries.** If no version of a provision was in force on the requested date, return a structured `not_in_force` with the ranges that were. Returning the current text instead is the single most damaging thing this corpus could do, because the answer looks right.
6. **Bump `parser_rev` whenever `parse` changes.** Records carry the revision that produced them, which is what makes partial re-derivation safe.
7. **Provenance is mandatory.** Every `Document` carries the source URL, retrieval time, snapshot SHA-256, adapter version and parser revision.

---

## Phase 3 — SSO (legislation). Start here.

The most valuable remaining work and the one with live research already done. Everything below was verified against the live site on 2026-09-19; none of it is assumed.

### Access: decided, do not re-litigate

SSO sits behind CloudFront, which **403s any non-browser User-Agent**. Verified: the project UA, `curl/8.4.0` and `python-httpx/0.27` all get 403; a Chrome UA gets 200.

SSO's own `robots.txt` (readable only with a browser UA, which is itself telling) says:

```
user-agent: *
disallow: /search
crawl-delay: 6
```

So AGC's published, considered crawling policy **permits** exactly what this phase needs, and the WAF rule contradicts it.

**The repo owner has decided:** send a browser User-Agent, and honour `robots.txt` to the letter in exchange —

- **6 second crawl-delay** for `sso.agc.gov.sg`, not the 2s currently in `config.POLICIES`. Change it.
- **Never request any path under `/search`.** Not once.
- Document the contradiction openly in `docs/legal-posture.md` — the reasoning, not just the outcome.

Implement the UA as a per-source override in `config.SourcePolicy` (add a `user_agent` field defaulting to `USER_AGENT`), not as a global change. Every other source keeps the honest project UA. Note in a comment why SSO differs.

### The bug this adapter exists to fix, now quantified

The prototype derives an Act's SSO short code from the initials of its title. Measured against all 501 Acts on browse page 1:

- **399/501 correct (79.6%)**, **102 wrong (20.4%)**
- **6 of the wrong guesses silently fetch a different real Act**, including two of the most-cited statutes in Singapore:

| Title | Real code | Guessed | Guess actually fetches |
|---|---|---|---|
| Companies Act 1967 | `CoA1967` | `CA1967` | Currency Act 1967 |
| Employment Act 1968 | `EA1968`* | `EA1968` | Extradition Act 1968 |
| Presidential Elections Act 1991 | — | `PEA1991` | Professional Engineers Act 1991 |
| Science Centre Act 1970 | — | `SCA1970` | State Courts Act 1970 |
| Debtors Act 1934 | — | `DA1934` | Distress Act 1934 |
| Fees Act 1920 | — | `FA1920` | Foreshores Act 1920 |

\* Re-derive the exact real codes when you run the listing; the collision set is what matters and is reproducible.

Reproduce with the snippet in "Re-deriving the research" below. Put the measured numbers into `docs/sources/sso.md`, replacing the vaguer wording currently there.

**The fix:** read the code from the `href` on the browse listing. Never derive it.

### Verified page structure

**Browse listing.** `GET /Browse/Act/Current/All?PageSize=500&SortBy=Title&SortOrder=ASC`

- `table.browse-list` exists; Act links are `a.non-ajax` with `href="/Act/<CODE>"` and the title as text.
- **Caution:** `a.non-ajax` is also used for "Download PDF", "Subsidiary Legislation", "Add to My Collections" and "Amendments RSS Feed" rows. My first extraction picked up `Add to My Collections` as an Act title. Filter to `href` matching `^/Act/[A-Za-z0-9_.-]+$` exactly (no query string) **and** scope to the title column of each row.
- The page reports **525 results** but `PageSize=500` yields 501 links on page 1. Pagination is unresolved — find it and cover the remainder. Do not assume 500 is the whole statute book.
- Subsidiary legislation: `/Browse/SL/Current/All?PageSize=500`. Not yet probed.

**Act document.** `GET /Act/<CODE>?WholeDoc=1`

- Returns ~264 KB for `PDPA2012` but **only Part 1 — four provisions**. `WholeDoc=1` does not mean whole document. `div#legisContent` holds 11,791 chars of body text; `.prov1` count is 4; `#pr13-` is absent.
- `div#tocPanel` holds the **complete** table of contents — 130 `a.nav-link` entries for PDPA2012, with `href="#pr13-"` style anchors and text like `13 Consent required`. The TOC is the authoritative provision list.
- Provision ids look like `pr1-`, `pr2-`, `pr13-`, `pr26D-`. Sub-provisions are nested ids like `pr2-ps1-pdevaluativepurpose-p1a-p2i-`.
- Part/Division anchors look like `#P11-` with text `Part 1 PRELIMINARY`.
- `PageIndex=2` does nothing. `ViewType=Print` and `/Print` return no `legisContent`.

**Fetching an individual provision — this works.** `GET /Act/<CODE>?ProvIds=pr13-` returns exactly s13 (`#pr13-` present, 451 chars of body).

**This is where I stopped.** The open question is whether `ProvIds` accepts **several ids in one request**. It matters enormously:

- One provision per request × 130 provisions × 6s = ~13 minutes per Act × 525 Acts ≈ **4.7 days**. Not viable.
- The page's print dialog says "Select the provisions you wish to print using the checkboxes", and there is a `data-bind="click: OnGetProvisions"`, so multi-select almost certainly exists.

Try, in order, at 6s spacing:

```
/Act/PDPA2012?ProvIds=pr13-,pr14-,pr15-
/Act/PDPA2012?ProvIds=pr13-&ProvIds=pr14-      (repeated param)
/Act/PDPA2012?ProvIds=pr13-%2Cpr14-
```

Check whether all requested provisions appear. If batching works, one or two requests per Act makes a full backfill roughly 1–2 hours, which is fine.

**Fallback if batching fails:** `GET /Act/<CODE>?ViewType=Pdf` returns the complete Act in one request (463.8 KB for AA2004, size is shown in the listing link text). One request per Act, full text, but you lose the HTML provision structure and must recover it by matching the TOC against the PDF text. Weigh that against 4.7 days. A third option is to fetch structure from the TOC and text from the PDF, joining on provision number.

**Point-in-time — the payoff, and it is enumerable.** The current Act page embeds the full version timeline as links:

```
/Act/PDPA2012/Historical/20130102?DocDate=20121203&ValidDate=20130102
/Act/PDPA2012/Historical/20131202?DocDate=20121203&ValidDate=20131202
/Act/PDPA2012/Historical/20140102?DocDate=20121203&ValidDate=20140102
...
```

So the set of commencement dates is discoverable from the current page — no need to guess dates or sweep a calendar. Each `ValidDate` becomes a provision version with `in_force_from`/`in_force_to`. This is what makes `get_provision(as_of=...)` real, and it is most of the Phase 3 value. Gate it behind a `--versions` flag so a first pass can build the current statute book quickly.

Also present: `/Act/<CODE>/History` returns 200 (~24.7 KB) but my table extraction found nothing useful. Re-examine its markup.

### Record mapping

Already specified in `docs/identifiers.md` and `docs/sources/sso.md`. Use `urn.provision()` — it already flattens `s 300(1)(a)` to `s300-1-a` and round-trips via `urn.to_citation()`. Tests exist in `tests/test_urn.py`.

Do not forget the **2020 Revised Edition** problem: the whole statute book was renumbered from `Cap N` to year-based citations, so every pre-2021 judgment cites provisions in a form that no longer appears on SSO. Without an alias table, citation resolution fails on most of the case law. That is Phase 5 work but a Phase 3 **data** requirement — capture the mapping while you are in the history pages.

---

## Phase 2 — eLitigation (judgments). Blocked.

**The site is under scheduled maintenance from 19-09-2026 14:00 to 20-09-2026 08:00 SGT.** Every path — including `robots.txt` — returns a 200 with an 847-byte maintenance notice. Verified for `SUPCT`, `STATECT` and `FAMCT` listings.

I deliberately did not write this adapter blind. The Hansard lesson was that the prototype's endpoint had silently changed and the spec I wrote from reading the prototype was wrong. Assume the same may be true here.

**When the site is back, verify before writing anything:**

1. Fetch `robots.txt` and honour whatever it says. It may impose a crawl-delay like SSO's.
2. Confirm the listing URL still works and still paginates:
   `/gd/Home/Index?Filter=SUPCT&YearOfDecision=2024&SortBy=Score&CurrentPage=1`
3. Confirm `Filter=STATECT` and `Filter=FAMCT` return results. The prototype hardcodes `SUPCT`, which excludes the State Courts entirely — most of the criminal and civil-claims output by volume.
4. Confirm the card markup: `div.card.col-12`, `span.gd-addinfo-text` for the neutral citation, `a.gd-cw` for catchwords.
5. Confirm the judgment markup: `div.Judg-1` numbered paragraphs, `div.Judg-Author` / `div.Judg-Sign`, `div.Judg-Lawyers`, `div.Judg-EOF`.
6. **Read the judgment href from the listing markup.** Do not rebuild it by string-mangling the citation the way the prototype does — that breaks on `SGHC(I)` and `SGHC(A)`.

Then implement per `docs/sources/elitigation.md`, which lists ten specific prototype defects not to inherit. The two that matter most: it discards the judgment text after computing word counts, and it holds a multi-year scrape in memory and writes one CSV at the end.

**Before any backfill beyond a single sampled year, stop and re-read `docs/legal-posture.md`.** This is the source where the gap between "publicly readable" and "licensed for bulk copying" is widest. The rate limit is 3s with a daily cap, and the cap is enforced in `net/client.py`.

---

## Phase 4 — PDPC (enforcement decisions). Ready to go.

Verified reachable 2026-09-19 with the honest project UA — no WAF issue, no maintenance:

```
robots.txt                 -> 200   User-Agent: *  /  Allow: /
/all-commissions-decisions -> 200   183,995 bytes
```

`robots.txt` allows everything. This is the most permissive of the four sources and the best-engineered prototype, so it is mostly a port rather than a rewrite.

Carry over from `pdpcscraper/scraper.py` largely as-is: `safe_get` with backoff, `CITATION_RE`, PDF citation extraction from the first three pages, context-aware penalty extraction, `normalise_case_name`.

Changes required, per `docs/sources/pdpc.md`:

- All nine DP obligations, not just Protection. Remove the filter at ingest; keep it as a query parameter on the `pdpc_decisions` MCP tool.
- `penalty_sgd` as an integer, with `0` + `no_penalty_reason` distinguished from `null` unknown. The prototype conflates them.
- Full decision text from the PDF, not the first paragraph over 80 characters.
- OCR fallback for the ~25% of decisions published as scans, which currently lose their citation entirely and fall back to a provisional URN (`urn.provisional_pdpc()` already exists).
- Commission undertakings — a separate listing the prototype does not touch.

**Worth 10 minutes before you add Playwright:** the listing is a JS-rendered SPA, and the prototype drives a headless browser but intercepts the underlying `getenforcementcase` JSON endpoint and prefers that data. Try calling `getenforcementcase` directly with the observed parameters. If it answers without a browser session, the `browser` extra disappears from this adapter entirely. The prototype never tests this.

---

## Re-deriving the research

The scratchpad from my session is session-scoped and will be gone. To regenerate the SSO findings:

```python
import httpx, re
from bs4 import BeautifulSoup
CH = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")
c = httpx.Client(timeout=120, headers={"User-Agent": CH}, follow_redirects=True)
r = c.get("https://sso.agc.gov.sg/Browse/Act/Current/All"
          "?PageSize=500&SortBy=Title&SortOrder=ASC")
tbl = BeautifulSoup(r.text, "lxml").find("table", class_="browse-list")
acts = {}
for a in tbl.find_all("a", class_="non-ajax", href=True):
    m = re.fullmatch(r"/Act/([A-Za-z0-9_.-]+)", a["href"])
    if m and a.get_text(strip=True):
        acts.setdefault(a.get_text(strip=True), m.group(1))
# then compare against create_acronym() copied verbatim from
# kevanwee/sgstatutescraper/ssoscrape.py
```

Keep the output out of the repo. Record the measured numbers in the docs instead.

---

## Gotchas already paid for

- **Verify every endpoint against the live source before writing an adapter.** The SPRS Hansard endpoint had moved from `GET ?sittingDate=` to a JSON `POST`, and the old form returns HTTP 500 for every date — which the prototype reads as "no sitting", so it collects nothing and exits 0. The spec written from reading the prototype was simply wrong. Assume the same of eLitigation and PDPC.
- **When a front end is an SPA, read its JS bundle.** Both the Hansard POST endpoint and the vernacular PDF endpoint were found by fetching `main.<hash>.js` and grepping for `baseUrl + "/..."` concatenations. That is faster than guessing payloads — and note that guessing payloads for SPRS `/searchResult` failed entirely, which is why there is no sitting-calendar optimisation.
- **Nested block elements duplicate text.** `soup.find_all(["p","div"])` returns a wrapping `<div>` *and* its children, so a naive speech splitter counted every section twice. Filter to innermost blocks: `[el for el in soup.find_all([...]) if not el.find([...])]`.
- **Windows console encoding will blow up on CJK/Tamil `print`.** Set `PYTHONIOENCODING=utf-8` when inspecting vernacular output. It is a console artefact, not a data bug.
- **`ftfy` is in core deps and text repair belongs in one place** (`_clean`), not as per-character `.replace()` calls scattered through parsers.
- **The source misspells `parlimentNO`.** Preserved on their side, corrected on ours.

---

## Suggested order

1. **Phase 4 (PDPC)** — unblocked, permissive, best prototype, fastest win. Proves the adapter protocol generalises beyond Hansard.
2. **Phase 3 (SSO)** — highest value. Resolve the `ProvIds` batching question first; it determines whether the whole approach is viable.
3. **Phase 2 (eLitigation)** — once the site is back and re-verified, and with the licensing position re-read.

Phase 5 (the citation graph and `parliamentary_intent`) needs at least SSO and Hansard, so it cannot start before Phase 3 lands.
