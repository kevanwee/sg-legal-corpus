"""Paths, per-source rate limits and runtime settings.

The rate limits here are floors derived from docs/legal-posture.md. A config
file can slow them down and cannot speed them up -- see ``SourcePolicy.merge``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path

VERSION = "0.1.0"

USER_AGENT = (
    f"sg-legal-corpus/{VERSION} (+https://github.com/kevanwee/sg-legal-corpus) "
    "research corpus builder; contact via repository issues"
)
"""Identifies the project so an administrator who sees the traffic can find out
what it is and contact the author. SSO has an explicitly documented override."""


@dataclass(frozen=True, slots=True)
class SourcePolicy:
    """Per-host politeness policy. See docs/legal-posture.md."""

    host: str
    min_interval: float
    """Seconds between requests. Single connection; no concurrency, ever."""
    user_agent: str = USER_AGENT
    daily_cap: int | None = None
    respect_robots: bool = True
    redistributable: bool = False
    """Whether full text from this source may leave the local machine.
    False for every source until written permission exists."""

    def merge(self, min_interval: float | None = None) -> SourcePolicy:
        """Apply a user override. Slower is honoured; faster is ignored."""
        if min_interval is None:
            return self
        return replace(self, min_interval=max(self.min_interval, min_interval))


POLICIES: dict[str, SourcePolicy] = {
    # SSO permits crawling at 6s but rejects non-browser UAs at CloudFront.
    # Owner-authorised exception; see docs/legal-posture.md.
    "sso": SourcePolicy(
        "sso.agc.gov.sg", min_interval=6.0,
        user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"),
    ),
    "elitigation": SourcePolicy(
        "www.elitigation.sg", min_interval=3.0, daily_cap=2000
    ),
    "pdpc": SourcePolicy("www.pdpc.gov.sg", min_interval=1.5),
    "hansard": SourcePolicy("sprs.parl.gov.sg", min_interval=0.5),
}


@dataclass(frozen=True, slots=True)
class Paths:
    root: Path

    @property
    def snapshots(self) -> Path:
        return self.root / "snapshots"

    @property
    def documents(self) -> Path:
        return self.root / "documents"

    @property
    def refs(self) -> Path:
        return self.root / "refs"

    @property
    def index(self) -> Path:
        return self.root / "index"

    @property
    def checkpoints(self) -> Path:
        return self.root / "checkpoints"

    @property
    def db(self) -> Path:
        return self.index / "corpus.db"

    def ensure(self) -> None:
        for p in (self.snapshots, self.documents, self.refs, self.index, self.checkpoints):
            p.mkdir(parents=True, exist_ok=True)


def default_paths() -> Paths:
    root = os.environ.get("SGCORPUS_DATA")
    if root:
        return Paths(Path(root))
    return Paths(Path.cwd() / "data")
