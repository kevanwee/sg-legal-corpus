"""eLitigation adapter -- written judgments. Specified, not yet built.

Full plan, endpoints and record mapping: docs/sources/elitigation.md.
Read docs/legal-posture.md before any backfill; this is the source with the
tightest terms of use.

What the implementation must do differently from the prototype (elitiscraper):

1. Store the judgment text. The prototype computes word and paragraph counts
   from the text and discards the text itself.
2. Cover all three filters. ``SUPCT`` alone excludes the State Courts, which
   are most of the criminal and civil-claims output by volume.
3. Checkpoint per judgment and append as it goes. The prototype holds a
   multi-year scrape in memory and writes one CSV after the final loop.
4. Read the judgment href from the listing markup rather than rebuilding it by
   chained string replacement -- ``SGHC(I)`` and ``SGHC(A)`` break the guess.
5. Emit one record per numbered paragraph, so a search hit is a citable
   pincite rather than a whole judgment.
"""

from __future__ import annotations

from ..models import Authority, Corpus
from .base import NotImplementedAdapter

LISTING = (
    "https://www.elitigation.sg/gd/Home/Index"
    "?Filter={filter}&YearOfDecision={year}&SortBy=Score&CurrentPage={page}"
)
JUDGMENT = "https://www.elitigation.sg/gd/s/{case_identifier}"

FILTERS = {
    "SUPCT": Authority.SUPCT,
    "STATECT": Authority.STATECT,
    "FAMCT": Authority.FAMCT,
}

COURTS = ("SGCA", "SGHC(A)", "SGHC", "SGHC(I)", "SGHCR", "SGDC", "SGMC", "SGFC")


class ElitigationAdapter(NotImplementedAdapter):
    name = "elitigation"
    corpus = Corpus.JUDGMENT
    authority = Authority.SUPCT
    spec_doc = "docs/sources/elitigation.md"
