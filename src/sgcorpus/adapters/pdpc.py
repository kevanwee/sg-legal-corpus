"""PDPC adapter -- enforcement decisions. Specified, not yet built.

Full plan and record mapping: docs/sources/pdpc.md.

Mostly a port rather than a rewrite: the prototype (pdpcscraper) is the best
engineered of the four. Its retry logic, citation regex and PDF extraction
carry over close to as-is, and the regexes below are lifted from it.

What changes: ingest all nine DP obligations rather than filtering to
Protection; store penalties as integers with zero distinguished from unknown;
extract the full grounds from the PDF rather than a summary paragraph; OCR the
roughly 25% of decisions published as scans, which currently lose their
citation entirely.

Worth trying before adding the browser dependency: call the underlying
``getenforcementcase`` JSON endpoint directly. The prototype drives Playwright
and intercepts that endpoint's responses, but never tests whether it answers
without a browser session.
"""

from __future__ import annotations

import re

from ..models import Authority, Corpus
from .base import NotImplementedAdapter

BASE_URL = "https://www.pdpc.gov.sg"
LISTING_URL = f"{BASE_URL}/all-commissions-decisions"
API_HINT = "getenforcementcase"

# [2024] SGPDPC 3 / [2023] SGPDPCS 12 / [2021] SGPDPC 4 (NFA)
CITATION_RE = re.compile(r"\[\d{4}\]\s+SGPDPCS?\s+\d+(?:\s*\(NFA\))?", re.IGNORECASE)

PENALTY_CONTEXT_RE = re.compile(
    r"(?:financial\s+penalty\s+of\s+|penalty\s+of\s+)(?:S\$|\$)([\d,]+(?:\.\d{2})?)",
    re.IGNORECASE,
)

OBLIGATIONS = (
    "Consent",
    "Purpose Limitation",
    "Notification",
    "Access and Correction",
    "Accuracy",
    "Protection",
    "Retention Limitation",
    "Transfer Limitation",
    "Accountability",
)

NO_PENALTY_REASONS = ("warning", "directions_only", "no_breach", "nfa")


class PdpcAdapter(NotImplementedAdapter):
    name = "pdpc"
    corpus = Corpus.PDPC
    authority = Authority.PDPC
    spec_doc = "docs/sources/pdpc.md"
