# Source: Hansard (SPRS)

**Authority:** Parliament of Singapore · **Corpus:** `hansard` · **Adapter:** `hansard` · **Status:** implemented
**Prototype:** [hansardscraper](https://github.com/kevanwee/hansardscraper)

The best-behaved of the four sources: a real JSON API, no browser needed, no pagination, stable schema.

## Endpoint

```
POST https://sprs.parl.gov.sg/search/getHansardReport
Content-Type: application/json

{"sittingDate": "06-02-2024"}
```

**This changed after the prototype was written.** SPRS now serves an Angular SPA, and the old form — `GET /search/getHansardReport/?sittingDate=DD-MM-YYYY` — returns HTTP 500 for *every* date, sitting day or not. The prototype therefore reports "no sitting" for the entire calendar and collects nothing, while exiting successfully.

The current call is a POST with a JSON body, confirmed from the front-end bundle:

```js
getHansardReport(X) {
  return this.authHttp.post(this.getHansardReportURL, {sittingDate: X}, at)
}
```

Only `DD-MM-YYYY` is accepted. `YYYY-MM-DD` returns 500; `DD/MM/YYYY` and `YYYYMMDD` return 400.

Response shape (verified against 6 February 2024, 638 KB, 92 sections):

```jsonc
{
  "metadata": {
    "parlimentNO": 14,        // sic — the source misspells it
    "sessionNO": 2,
    "volumeNO": 95,
    "sittingNO": 121,
    "sittingDate": "06-02-2024",
    "partSessionStr": "SECOND SESSION",
    "startTimeStr": "11:00 AM",
    "speaker": "Mr Speaker",
    "dateToDisplay": "Tuesday, 6 February 2024"
  },
  "takesSectionVOList": [
    {
      "startPgNo": 1, "endPgNo": 4,
      "title": "...", "subTitle": null,
      "sectionType": "OA",     // OA | WA | WANA | OS | BP
      "questionNo": 6,
      "content": "<html>..."
    }
  ],
  "attendanceList": [ { "mpName": "Ms Foo Mee Har (West Coast).", "attendance": false } ],
  "writtenAnswersVOList": [],  // empty in practice; written answers are sectionType WA
  "annexureList": [], "ptbaList": [], "vernacularList": []
}
```

`sectionType` is worth capturing: `OA` oral answer, `WA` written answer, `WANA` written answer not answered, `OS` oral statement, `BP` bill proceedings. `startPgNo`/`endPgNo` give the column reference a Hansard citation needs.

## Speaker markup

`content` is an HTML fragment in which every speaker label is wrapped in `<strong>`, which is a far more reliable signal than a text regex. Two shapes appear, and they are inverses of each other:

```html
<p><strong>Dr Wan Rizal (Jalan Besar)</strong>:&nbsp;Sir, I beg to move ...
<p><strong>The Minister for Manpower (Dr Tan See Leng)</strong>:&nbsp;Mr Speaker, Sir ...
```

Name-then-seat in the first, office-then-name in the second. Treating the parenthetical as a constituency in both cases mislabels every minister.

Questions carry the question number before the name and have no colon at all:

```
23 Mr Yip Hon Weng asked the Prime Minister whether ...
```

Anchoring the speaker pattern without allowing that leading number costs about 20 percentage points of attribution coverage, and throws away the question number as well.

## Behaviour

- **Non-sitting dates return HTTP 500** with `{"errorCode":500,...}`. Not an error — the correct interpretation is "no sitting".
- Some responses return 200 with an `errorCode` field instead. Both paths mean no data.
- Responses are UTF-8 but carry no `charset` in the `Content-Type`, so a client that guesses will produce mojibake. Decode as UTF-8 explicitly.
- The site sits behind Imperva; the JSON endpoint answers without a session or referer, but be conservative with request rates.
- No rate limiting observed at 0.5s intervals. The project uses 0.5s anyway.
- No pagination, no auth, no cookies.

## Work units

One per sitting date. Checkpointed by date, so an interrupted backfill resumes at the next unfetched date.

**Improvement over the prototype:** iterate the parliamentary sitting calendar rather than every calendar date. The prototype issues ~365 requests per year to find ~40 sitting days — a 9× waste that also makes a full backfill from 1955 impractical. Where the calendar is unavailable, fall back to day-by-day and cache the negative result so a re-run does not repeat the misses.

## Record mapping

One record per **speech**, with the sitting and section as ancestors:

```
urn:sg:hansard:2024-02-06          sitting      parts -> sections
urn:sg:hansard:2024-02-06:s3       section      parts -> speeches
urn:sg:hansard:2024-02-06:s3:sp12  speech       text  -> what was said
```

Speaker, role and constituency are parsed from the speaker markup in `content`. Where the markup does not identify a speaker, the speech is emitted with `speaker: null` and counted against the attribution coverage metric rather than dropped.

## Defects in the prototype this adapter must not inherit

**0. The endpoint it calls no longer works.** See above. The GET form returns 500 for every date, which the prototype reads as "no sitting", so a run over any range collects nothing and exits 0.

**1. Excel truncation at 32,767 characters.** `clamp_for_excel` silently cuts every value to Excel's cell limit. Measured against three real sitting days, the speech text totals 1,647,854 characters — about 549,000 per sitting. The prototype's one-row-per-day format therefore retains roughly **6%** of the debate and discards the rest at write time, with no warning and no record that it happened. This alone makes its output unusable as a corpus.

**2. One row per sitting day.** All sections are joined with `\n\n` into a single cell, and `SectionTitles` with ` || `. There is no way to recover which text belongs to which section, let alone to which speaker.

**3. No speaker attribution at all.** `attendanceList` is reduced to a count. A Hansard quotation without a named speaker is not usable as authority, which removes the source's entire evidential value.

**4. Excel as the store.** `pd.read_excel` on the whole master file to determine the resume point, then `to_excel` of the whole thing on every run. Cost grows quadratically with corpus size and the file is a single point of corruption.

**5. Vernacular material counted, not kept.** `VernacularDocCount` records that non-English sections existed; the text is dropped.

## Coverage denominator

Sitting days in the parliamentary calendar for the covered range. `corpus_stats` reports ingested / expected, and an absent sitting day carries a recorded reason.

## Measured baseline

Three sitting days (5-7 February 2024), adapter 1.0.0 / parser_rev 1:

| Metric | Value |
|---|---|
| Sitting days found | 3 of 5 weekdays probed |
| Documents | 1,419 (3 sittings, 258 sections, 1,158 speeches) |
| Speaker attribution | 98.0% |
| Distinct speakers | 129 |
| Question numbers captured | 339 |
| Speech text | 1,647,854 characters |
| Parse failures | 0 |

The residual 2% is timestamps (`2.03 pm`) and `[(proc text) ...]` procedural markers, which have no speaker in the source either. They are retained with a null speaker rather than dropped: they are part of the record, and being explicit about what they are costs nothing.
