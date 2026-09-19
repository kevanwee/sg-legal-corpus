"""Stage 3 -- enrich. Where four datasets become one corpus. Phase 5.

Three passes, each producing typed edges into data/refs/. Specified here so the
contracts are fixed before the adapters that feed them land; see
docs/roadmap.md#phase-5--the-graph.

1. Citation extraction and resolution
   Neutral citations, law-report citations, statute references and Hansard
   references, extracted by rule from every document's text and resolved to
   URNs. Unresolvable citations are kept as edges with ``to: None`` and the raw
   string preserved -- a coverage metric, never a silent discard.

2. Legislative history
   Amending Acts linked to the provisions they amend, provisions to their
   commencement dates. This is what makes ``as_of`` answerable, and it depends
   on the SSO History view (docs/sources/sso.md).

3. Provision to debate linkage
   The chain behind ``parliamentary_intent``: provision -> enacting or amending
   Act -> Bill -> second-reading sitting -> the speeches in that section. The
   join is on Act long titles and Bill numbers against Hansard section titles,
   so every edge it produces is confidence-scored and every response that
   traverses one carries a caveat. There is no shared key between SSO and SPRS;
   pretending otherwise would be the easiest way to make this corpus
   confidently wrong.
"""

from __future__ import annotations

import re

from ..config import Paths

# [2024] SGCA 15
NEUTRAL_CITATION_RE = re.compile(r"\[(\d{4})\]\s+(SG[A-Z]+(?:\([A-Z]+\))?)\s+(\d+)")

# [2024] 1 SLR 123
REPORT_CITATION_RE = re.compile(r"\[(\d{4})\]\s+(\d+)\s+(SLR|SGLR|MLJ)(?:\(R\))?\s+(\d+)")

# section 13 of the Personal Data Protection Act 2012 / s 157(1) of the Companies Act
STATUTE_REF_RE = re.compile(
    r"\b(?:section|sections|s|ss)\s*(\d+[A-Z]*(?:\([^)]{1,8}\))*)"
    r"(?:\s+of\s+the\s+(?P<act>[A-Z][A-Za-z ()'-]{3,80}?\s+Act(?:\s+\d{4})?))?",
    re.IGNORECASE,
)


def run(paths: Paths) -> dict[str, int]:
    raise NotImplementedError(
        "enrichment is Phase 5. The edge schema is fixed (schemas/ref.schema.json) "
        "and the extraction patterns above are the starting point; the resolver "
        "needs the alias table from docs/identifiers.md#aliases, which needs the "
        "SSO adapter. See docs/roadmap.md."
    )
