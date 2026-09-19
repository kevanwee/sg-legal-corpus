"""SSO adapter -- Singapore Statutes Online. Specified, not yet built.

Full plan, endpoints and record mapping: docs/sources/sso.md.

Three things must be true of the implementation, because the prototype
(sgstatutescraper) gets each of them wrong:

1. The AGC short code is read from the ``href`` on the browse listing. It is
   never derived from the Act's title -- title-initial acronyms resolve a large
   share of the statute book to a code that 404s or, worse, matches a different
   Act.
2. Provision URLs are built from the Act actually being scraped. The prototype
   hardcodes ``Act/AA2004`` into every provision URL it emits.
3. Provision text is stored. The prototype extracts it and then omits it from
   the writer, leaving a table of contents rather than a corpus.

The point-in-time work is the reason this adapter matters: SSO exposes
historical versions via ``?ValidDate=YYYYMMDD``, and the History view lists the
commencement dates worth fetching. That sparse set of dates -- not a daily
sweep -- is what makes ``get_provision(as_of=...)`` answerable.
"""

from __future__ import annotations

from ..models import Authority, Corpus
from .base import NotImplementedAdapter

BROWSE_ACTS = "https://sso.agc.gov.sg/Browse/Act/Current/All?PageSize=500&SortBy=Title&SortOrder=ASC"
BROWSE_SL = "https://sso.agc.gov.sg/Browse/SL/Current/All?PageSize=500"
ACT_WHOLE = "https://sso.agc.gov.sg/Act/{short_code}?WholeDoc=1"
ACT_AS_OF = "https://sso.agc.gov.sg/Act/{short_code}?ValidDate={yyyymmdd}"
ACT_HISTORY = "https://sso.agc.gov.sg/Act/{short_code}/History"


class SsoAdapter(NotImplementedAdapter):
    name = "sso"
    corpus = Corpus.ACT
    authority = Authority.AGC
    spec_doc = "docs/sources/sso.md"
