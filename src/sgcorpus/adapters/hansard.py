"""Hansard adapter -- Singapore Parliamentary Reports (SPRS).

The reference implementation. A real JSON API, no browser, no pagination.
See docs/sources/hansard.md.

Emits one record per *speech*, with the sitting and section as ancestors. The
prototype scraper emits one row per sitting day, concatenated into a single
Excel cell truncated at 32,767 characters, with no speaker attribution. A real
sitting day is around 640 KB of JSON across ~90 sections, so that truncation
keeps roughly 5% of the debate and discards the rest silently.
"""

from __future__ import annotations

import html
import re
import unicodedata
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

from bs4 import BeautifulSoup, Tag

from .. import urn as urnlib
from ..models import Authority, Corpus, Dates, Document, Provenance
from ..net.cache import Snapshot
from ..net.client import Client
from .base import WorkUnit

# The front end is an Angular SPA and this endpoint takes a JSON POST body.
# It was a GET with ?sittingDate= when the prototype was written; the old form
# now answers HTTP 500 for every date, sitting or not.
API_URL = "https://sprs.parl.gov.sg/search/getHansardReport"
SOURCE_DATE_FORMAT = "%d-%m-%Y"

# Attribution appears in two shapes, and they are inverses of each other:
#   "Dr Wan Rizal (Jalan Besar)"                  -> name, then seat
#   "The Minister for Manpower (Dr Tan See Leng)" -> office, then name
OFFICE_FIRST_RE = re.compile(r"^(?P<office>The\s+.+?)\s*\((?P<name>[^)]+)\)\s*$")
NAME_FIRST_RE = re.compile(r"^(?P<name>[^(]+?)(?:\s*\((?P<paren>[^)]+)\))?\s*$")

# A parenthetical after a name is an office if it reads like one, else a seat.
ROLE_HINT_RE = re.compile(
    r"Minister|Speaker|Secretary|Chairman|Leader|Whip|President|Attorney|Ambassador|Mayor",
    re.IGNORECASE,
)

# Fallback for sections whose speakers are not marked up with <strong>.
_HONORIFIC = r"(?:Mr|Mrs|Ms|Miss|Mdm|Dr|Prof|Assoc\s+Prof|Asst\s+Prof|Er|The)"
SPEAKER_TEXT_RE = re.compile(
    rf"^\s*(?P<lead>{_HONORIFIC}\s+[^:(]{{1,80}}?)"
    r"(?:\s*\((?P<paren>[^)]{1,120})\))?\s*:\s*",
)
# Oral and written questions are printed with the question number first:
#   "23 Mr Yip Hon Weng asked the Prime Minister whether ..."
# That leading number is the question number, so it is captured rather than
# merely skipped.
ASKED_RE = re.compile(
    rf"^\s*(?:(?P<qno>\d{{1,4}})\s+)?(?P<lead>{_HONORIFIC}\s+[^:(]{{1,80}}?)"
    r"(?:\s*\((?P<paren>[^)]{1,120})\))?\s+asked\s+the\s+",
)

QUESTION_NO_RE = re.compile(r"\bQuestion\s+No\.?\s*(\d+)\b", re.IGNORECASE)

# sectionType, as the source labels it.
SECTION_TYPES = {
    "OA": "Oral Answer",
    "WA": "Written Answer",
    "WANA": "Written Answer (not answered)",
    "OS": "Oral Statement",
    "BP": "Bill Proceedings",
}


def _clean(value: Any) -> str:
    """Collapse whitespace and normalise Unicode.

    Text repair lives here, once, rather than as a growing list of
    per-character replacements scattered through the parsers -- which is what
    the prototype eLitigation scraper accumulated.
    """
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    text = html.unescape(text)
    try:
        from ftfy import fix_text

        text = fix_text(text)
    except ImportError:  # pragma: no cover
        pass
    text = unicodedata.normalize("NFC", text)
    text = text.replace(" ", " ")  # noqa: RUF001 - deliberate NBSP fold
    return re.sub(r"\s+", " ", text).strip()


def _split_attribution(label: str) -> tuple[str | None, str | None, str | None]:
    """Return (name, role, constituency) from a speaker label."""
    label = _clean(label).rstrip(":").strip()
    if not label:
        return None, None, None

    office_first = OFFICE_FIRST_RE.match(label)
    if office_first:
        return _clean(office_first.group("name")), _clean(office_first.group("office")), None

    name_first = NAME_FIRST_RE.match(label)
    if not name_first:
        return label, None, None

    name = _clean(name_first.group("name"))
    paren = _clean(name_first.group("paren") or "")
    if not paren:
        return name, None, None
    if ROLE_HINT_RE.search(paren):
        return name, paren, None
    # Anything else is a seat. Ambiguity here shows up in the attribution
    # coverage metric rather than being silently resolved.
    return name, None, paren


def _split_speeches(content_html: str) -> list[dict[str, Any]]:
    """Split a section's HTML into attributed speeches.

    The primary signal is the <strong> element SPRS wraps every speaker label
    in. Blocks without one are continuations of the speech before. Text
    preceding the first identified speaker becomes an unattributed speech
    rather than being dropped -- procedural text and the Chair's interjections
    are part of the record.
    """
    soup = BeautifulSoup(content_html, "lxml")
    # Innermost block elements only: a wrapping <div> yields the text of every
    # <p> inside it, so counting both duplicates the entire section.
    blocks = [
        el for el in soup.find_all(["p", "div", "h6"]) if not el.find(["p", "div", "h6"])
    ]
    if not blocks:
        blocks = [soup]

    speeches: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    def start(
        name: str | None,
        role: str | None,
        seat: str | None,
        body: str,
        question_no: str | None = None,
    ) -> None:
        nonlocal current
        if current is not None:
            speeches.append(current)
        current = {
            "speaker": name,
            "role": role,
            "constituency": seat,
            "question_no": question_no,
            "text": [body],
        }

    for block in blocks:
        text = _clean(block.get_text(" ", strip=True))
        if not text:
            continue

        strong = block.find("strong") if isinstance(block, Tag) else None
        label = _clean(strong.get_text(" ", strip=True)) if strong else ""

        # The label must open the block; a <strong> used mid-sentence for
        # emphasis is not an attribution.
        if label and text.startswith(label):
            name, role, seat = _split_attribution(label)
            start(name, role, seat, text[len(label) :].lstrip(" :—-").strip())
            continue

        match = SPEAKER_TEXT_RE.match(text)
        if match:
            paren = match.group("paren")
            name, role, seat = _split_attribution(
                match.group("lead") + (f" ({paren})" if paren else "")
            )
            start(name, role, seat, text[match.end() :].strip())
            continue

        # "X asked the Minister for Y ..." carries no colon and the whole
        # sentence is the question, so the prefix is kept.
        asked = ASKED_RE.match(text)
        if asked:
            paren = asked.group("paren")
            name, role, seat = _split_attribution(
                asked.group("lead") + (f" ({paren})" if paren else "")
            )
            start(name, role, seat, text, question_no=asked.group("qno"))
            continue

        if current is None:
            current = {
                "speaker": None,
                "role": None,
                "constituency": None,
                "question_no": None,
                "text": [text],
            }
        else:
            current["text"].append(text)

    if current is not None:
        speeches.append(current)

    for speech in speeches:
        speech["text"] = "\n\n".join(part for part in speech["text"] if part)

    return [s for s in speeches if s["text"]]


class HansardAdapter:
    name = "hansard"
    corpus = Corpus.HANSARD
    authority = Authority.PARL
    adapter_version = "1.0.0"
    parser_rev = 1
    spec_doc = "docs/sources/hansard.md"

    # -- plan --------------------------------------------------------------

    def plan(
        self, since: date | None = None, until: date | None = None
    ) -> Iterator[WorkUnit]:
        """One work unit per candidate sitting date.

        Day-by-day iteration issues ~250 requests a year to find ~40 sitting
        days. The checkpoint caches the negative result for each non-sitting
        date so a re-run does not repeat the misses; replacing this with the
        published sitting calendar is Phase 1 work (docs/roadmap.md).
        """
        start = since or date(2020, 1, 1)
        end = until or date.today()
        current = start
        while current <= end:
            if current.weekday() < 5:  # Parliament does not sit at weekends.
                yield WorkUnit(
                    key=f"hansard:{current.isoformat()}",
                    payload={"sitting_date": current.isoformat()},
                )
            current += timedelta(days=1)

    # -- fetch -------------------------------------------------------------

    def fetch(
        self, unit: WorkUnit, client: Client
    ) -> Iterator[tuple[str, bytes, int, dict[str, Any]]]:
        sitting = date.fromisoformat(unit.payload["sitting_date"])
        params = {"sittingDate": sitting.strftime(SOURCE_DATE_FORMAT)}

        # HTTP 500 from this endpoint means "no sitting", not "error".
        response = client.post_json(API_URL, params, accept_status=(200,))
        if response is None or response.status_code != 200:
            return

        body = response.content
        if b'"errorCode"' in body and b'"takesSectionVOList"' not in body:
            return

        yield str(response.url), body, response.status_code, params

    # -- parse -------------------------------------------------------------

    def parse(self, snapshot: Snapshot) -> Iterator[Document]:
        payload = snapshot.json()
        if not isinstance(payload, dict):
            return

        metadata = payload.get("metadata") or {}
        sitting_raw = metadata.get("sittingDate") or snapshot.params.get("sittingDate", "")
        try:
            sitting = datetime.strptime(sitting_raw, SOURCE_DATE_FORMAT).date()
        except ValueError:
            return

        sections = payload.get("takesSectionVOList") or []

        provenance = Provenance(
            source_url=snapshot.url,
            retrieved_at=snapshot.retrieved_at or datetime.now(UTC),
            snapshot_sha256=snapshot.sha256,
            adapter=self.name,
            adapter_version=self.adapter_version,
            parser_rev=self.parser_rev,
        )
        dates = Dates(issued=sitting)

        base_meta = {
            # The source misspells this field; preserved on their side, fixed on ours.
            "parliament_no": metadata.get("parlimentNO"),
            "session_no": metadata.get("sessionNO"),
            "volume_no": metadata.get("volumeNO"),
            "sitting_no": metadata.get("sittingNO"),
            "sitting_type": _clean(metadata.get("partSessionStr")),
        }

        sitting_urn = urnlib.hansard_sitting(sitting)
        section_urns: list[str] = []
        children: list[Document] = []

        for section_index, section in enumerate(sections):
            if not isinstance(section, dict):
                continue

            title = _clean(section.get("title"))
            subtitle = _clean(section.get("subTitle"))
            section_type = section.get("sectionType")
            speeches = _split_speeches(section.get("content") or "")
            if not speeches:
                continue

            section_urn = urnlib.hansard_section(sitting, section_index)
            heading = " - ".join(p for p in (title, subtitle) if p) or f"Section {section_index}"
            section_meta: dict[str, Any] = {
                **base_meta,
                "section_index": section_index,
                "section_title": title,
                "section_subtitle": subtitle,
                "section_type": section_type,
                "section_type_label": SECTION_TYPES.get(section_type or ""),
                "start_page": section.get("startPgNo"),
                "end_page": section.get("endPgNo"),
            }
            speech_urns: list[str] = []

            for speech_index, speech in enumerate(speeches):
                speech_urn = urnlib.hansard_speech(sitting, section_index, speech_index)

                # Most specific source first: the number printed against this
                # question, then the section's own field, then the text.
                question_no = speech.get("question_no") or section.get("questionNo")
                if not question_no:
                    found = QUESTION_NO_RE.search(speech["text"][:400])
                    question_no = found.group(1) if found else None

                children.append(
                    Document(
                        urn=str(speech_urn),
                        corpus=self.corpus,
                        authority=self.authority,
                        title=heading,
                        citation=urnlib.to_citation(speech_urn),
                        dates=dates,
                        text=speech["text"],
                        parent=str(section_urn),
                        meta={
                            **section_meta,
                            "level": "speech",
                            "speech_index": speech_index,
                            "speaker": speech["speaker"],
                            "speaker_role": speech["role"],
                            "constituency": speech["constituency"],
                            "is_question": bool(question_no)
                            or section_type in ("WA", "WANA", "OA"),
                            "question_no": str(question_no) if question_no else None,
                        },
                        provenance=provenance,
                    )
                )
                speech_urns.append(str(speech_urn))

            children.append(
                Document(
                    urn=str(section_urn),
                    corpus=self.corpus,
                    authority=self.authority,
                    title=heading,
                    citation=urnlib.to_citation(section_urn),
                    dates=dates,
                    text="",  # The text lives in the speeches; storing it twice helps nobody.
                    parts=speech_urns,
                    parent=str(sitting_urn),
                    meta={**section_meta, "level": "section"},
                    provenance=provenance,
                )
            )
            section_urns.append(str(section_urn))

        if not section_urns:
            return

        attendance = payload.get("attendanceList") or []
        yield Document(
            urn=str(sitting_urn),
            corpus=self.corpus,
            authority=self.authority,
            title=f"Parliamentary sitting, {metadata.get('dateToDisplay') or sitting.isoformat()}",
            citation=urnlib.to_citation(sitting_urn),
            dates=dates,
            text="",
            parts=section_urns,
            meta={
                **base_meta,
                "level": "sitting",
                "section_count": len(section_urns),
                "attendance_count": sum(1 for a in attendance if a.get("attendance")),
                "roll_count": len(attendance),
                "presiding": _clean(metadata.get("speaker")),
                "start_time": _clean(metadata.get("startTimeStr")),
            },
            provenance=provenance,
        )
        yield from children
