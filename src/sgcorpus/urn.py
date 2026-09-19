"""URN minting, parsing and display-form conversion.

One identifier scheme across all four corpora. See docs/identifiers.md for the
grammar and the per-corpus rules.

    urn:sg:<corpus>:<work>[:<part>][@<as-of>]
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

SEGMENT_RE = re.compile(r"^[A-Za-z0-9._-]+$")

URN_RE = re.compile(
    r"^urn:sg:"
    r"(?P<corpus>act|sl|judgment|pdpc|hansard|bill):"
    r"(?P<rest>[A-Za-z0-9._:-]+?)"
    r"(?:@(?P<asof>\d{4}-\d{2}-\d{2}))?$"
)

# [2024] SGCA 15 / [2019] SGHC(I) 3 / [2021] SGPDPC 4 (NFA)
CITATION_RE = re.compile(
    r"\[(?P<year>\d{4})\]\s+"
    r"(?P<court>[A-Z]+(?:\([A-Z]+\))?)\s+"
    r"(?P<number>\d+)"
    r"(?P<suffix>\s*\(NFA\))?",
    re.IGNORECASE,
)

# s 300(1)(a)(ii) -> the bracketed tail
SUBPROVISION_RE = re.compile(r"\(([A-Za-z0-9]+)\)")


class UrnError(ValueError):
    """Raised when a URN cannot be parsed or a component is not representable."""


@dataclass(frozen=True, slots=True)
class Urn:
    corpus: str
    work: str
    parts: tuple[str, ...] = ()
    as_of: date | None = None

    def __str__(self) -> str:
        head = f"urn:sg:{self.corpus}:{self.work}"
        if self.parts:
            head += ":" + ":".join(self.parts)
        if self.as_of is not None:
            head += f"@{self.as_of.isoformat()}"
        return head

    @property
    def work_urn(self) -> Urn:
        """The containing work, with parts and as-of stripped."""
        return Urn(self.corpus, self.work)

    def child(self, part: str) -> Urn:
        if not SEGMENT_RE.match(part):
            raise UrnError(f"illegal URN segment: {part!r}")
        return Urn(self.corpus, self.work, (*self.parts, part), self.as_of)

    def at(self, when: date | None) -> Urn:
        """Return this URN pinned to a point in time.

        Only meaningful for legislation; a point-in-time judgment or speech is a
        category error rather than a missing feature.
        """
        if when is not None and self.corpus not in ("act", "sl"):
            raise UrnError(f"as-of is not meaningful for corpus {self.corpus!r}")
        return Urn(self.corpus, self.work, self.parts, when)


def parse(value: str) -> Urn:
    match = URN_RE.match(value.strip())
    if not match:
        raise UrnError(f"not a valid sg-legal-corpus URN: {value!r}")

    segments = match.group("rest").split(":")
    work, parts = segments[0], tuple(segments[1:])

    for segment in (work, *parts):
        if not SEGMENT_RE.match(segment):
            raise UrnError(f"illegal URN segment {segment!r} in {value!r}")

    as_of_raw = match.group("asof")
    as_of = date.fromisoformat(as_of_raw) if as_of_raw else None

    corpus = match.group("corpus")
    if as_of is not None and corpus not in ("act", "sl"):
        raise UrnError(f"as-of is not meaningful for corpus {corpus!r}: {value!r}")

    return Urn(corpus, work, parts, as_of)


def is_valid(value: str) -> bool:
    try:
        parse(value)
    except UrnError:
        return False
    return True


# --------------------------------------------------------------------------
# Minting
# --------------------------------------------------------------------------


def from_neutral_citation(citation: str, corpus: str = "judgment") -> Urn:
    """[2024] SGCA 15 -> urn:sg:judgment:2024_SGCA_15

    Parentheses are stripped rather than encoded: SGHC(I) and SGHCI cannot
    collide because no court emits both.
    """
    match = CITATION_RE.search(citation)
    if not match:
        raise UrnError(f"unrecognised neutral citation: {citation!r}")

    court = match.group("court").upper().replace("(", "").replace(")", "")
    work = f"{match.group('year')}_{court}_{match.group('number')}"
    if match.group("suffix"):
        work += "_NFA"
    return Urn(corpus, work)


def provisional_pdpc(slug: str) -> Urn:
    """A PDPC decision whose PDF is a scan and carries no readable citation.

    The ``x-`` prefix makes the provisional status unmistakable wherever the URN
    is printed. Replaced by the real citation if OCR or a later publication
    supplies one, with this URN retained as an alias.
    """
    cleaned = re.sub(r"[^a-z0-9]+", "-", slug.lower()).strip("-")
    if not cleaned:
        raise UrnError(f"cannot build a provisional URN from slug {slug!r}")
    return Urn("pdpc", f"x-{cleaned}")


def provision(short_code: str, number: str, as_of: date | None = None) -> Urn:
    """Mint a provision URN.

    ``short_code`` must be the official AGC code read from the SSO listing href.
    Deriving it from the Act's title is the central defect in the prototype SSO
    scraper -- see docs/sources/sso.md.

    s 300(1)(a) -> urn:sg:act:PC1871:s300-1-a
    """
    tail = SUBPROVISION_RE.findall(number)
    base = SUBPROVISION_RE.sub("", number).strip()
    part = "s" + "-".join([base, *tail])
    return Urn("act", short_code, (part,), as_of)


def hansard_sitting(sitting: date) -> Urn:
    return Urn("hansard", sitting.isoformat())


def hansard_section(sitting: date, section_index: int) -> Urn:
    return hansard_sitting(sitting).child(f"s{section_index}")


def hansard_speech(sitting: date, section_index: int, speech_index: int) -> Urn:
    return hansard_section(sitting, section_index).child(f"sp{speech_index}")


# --------------------------------------------------------------------------
# Display forms
# --------------------------------------------------------------------------


def to_citation(urn: Urn | str) -> str:
    """Render a URN back to its canonical display form.

    A canonical URN has exactly one display form. Aliases map the other way
    only -- many alternative citations resolve to one URN, never the reverse.
    """
    u = parse(urn) if isinstance(urn, str) else urn

    if u.corpus in ("judgment", "pdpc"):
        bits = u.work.split("_")
        if len(bits) >= 3:
            year, court, number = bits[0], bits[1], bits[2]
            nfa = " (NFA)" if len(bits) > 3 and bits[3] == "NFA" else ""
            base = f"[{year}] {court} {number}{nfa}"
            if u.parts and u.parts[0].startswith("para"):
                base += f" at [{u.parts[0][4:]}]"
            return base
        return u.work

    if u.corpus in ("act", "sl"):
        if not u.parts:
            return u.work
        head, *tail = u.parts[0].lstrip("s").split("-")
        rendered = f"s {head}" + "".join(f"({t})" for t in tail)
        suffix = f" (as at {u.as_of.isoformat()})" if u.as_of else ""
        return f"{rendered} of {u.work}{suffix}"

    if u.corpus == "hansard":
        try:
            sitting = date.fromisoformat(u.work).strftime("%d %b %Y")
        except ValueError:
            sitting = u.work
        return f"Sing. Parl. Deb., {sitting}"

    return str(u)
