"""The Hansard adapter parses offline, against a committed fixture.

No network, no live source. That property is the point of splitting fetch from
parse (docs/architecture.md#the-adapter-interface). The fixture is trimmed from
a real SPRS response; its markup, field names and section types are verbatim.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from sgcorpus.adapters.hansard import HansardAdapter, _split_attribution
from sgcorpus.models import Document
from sgcorpus.net.cache import Snapshot

FIXTURE = Path(__file__).parent / "fixtures" / "hansard_2024-02-06.json"


@pytest.fixture
def snapshot() -> Snapshot:
    body = FIXTURE.read_bytes()
    return Snapshot(
        sha256=hashlib.sha256(body).hexdigest(),
        body=body,
        url="https://sprs.parl.gov.sg/search/getHansardReport",
        retrieved_at=datetime(2026, 9, 19, tzinfo=UTC),
        status=200,
        params={"sittingDate": "06-02-2024"},
        headers={},
        adapter="hansard",
    )


@pytest.fixture
def documents(snapshot: Snapshot) -> list[Document]:
    return list(HansardAdapter().parse(snapshot))


def _by_level(documents: list[Document], level: str) -> list[Document]:
    return [d for d in documents if d.meta["level"] == level]


def test_emits_sitting_sections_and_speeches(documents: list[Document]) -> None:
    assert len(_by_level(documents, "sitting")) == 1
    assert len(_by_level(documents, "section")) == 3
    assert len(_by_level(documents, "speech")) == 8


def test_sitting_is_the_root_and_links_its_sections(documents: list[Document]) -> None:
    sitting = _by_level(documents, "sitting")[0]
    assert sitting.urn == "urn:sg:hansard:2024-02-06"
    assert sitting.parent is None
    assert sitting.parts == [
        "urn:sg:hansard:2024-02-06:s0",
        "urn:sg:hansard:2024-02-06:s1",
        "urn:sg:hansard:2024-02-06:s2",
    ]
    # attendanceList carries a present/absent flag the prototype discards.
    assert sitting.meta["roll_count"] == 3
    assert sitting.meta["attendance_count"] == 2


def test_speeches_are_attributed(documents: list[Document]) -> None:
    """A Hansard quotation without a named speaker is not usable as authority,
    so attribution is the adapter's headline coverage metric.

    Procedural text that precedes the first speaker -- the sitting's
    ``<h6>2.20 pm</h6>`` timestamp here -- is deliberately retained with a null
    speaker rather than dropped. It is part of the record and it is honest
    about what it is; it is not a quotable speech.
    """
    speeches = _by_level(documents, "speech")
    speakers = [d.meta["speaker"] for d in speeches]

    assert "Dr Wan Rizal" in speakers
    assert "Mrs Josephine Teo" in speakers

    unattributed = [d for d in speeches if d.meta["speaker"] is None]
    assert len(unattributed) == 1
    assert unattributed[0].text == "2.20 pm"
    assert len(speakers) - len(unattributed) == 7


def test_office_first_attribution_is_inverted_correctly(
    documents: list[Document],
) -> None:
    """'The Minister for Manpower (Dr Tan See Leng)' puts the office first and
    the name in parentheses -- the inverse of the 'Name (Seat)' form."""
    minister = next(
        d for d in documents if d.meta.get("speaker") == "Dr Tan See Leng"
    )
    assert minister.meta["speaker_role"] == "The Minister for Manpower"
    assert minister.meta["constituency"] is None


def test_name_first_attribution_splits_seat_from_office() -> None:
    assert _split_attribution("Dr Wan Rizal (Jalan Besar)") == (
        "Dr Wan Rizal",
        None,
        "Jalan Besar",
    )
    assert _split_attribution("Mrs Josephine Teo (Minister for Communications)") == (
        "Mrs Josephine Teo",
        "Minister for Communications",
        None,
    )
    assert _split_attribution("Mr Speaker") == ("Mr Speaker", None, None)


def test_speech_text_is_complete_and_untruncated(documents: list[Document]) -> None:
    """The prototype clamps every value to Excel's 32,767-character cell limit,
    discarding most of each sitting. Nothing here truncates."""
    opening = next(
        d
        for d in _by_level(documents, "speech")
        if d.meta["speaker"] == "Mrs Josephine Teo"
    )
    assert "I beg to move" in opening.text
    # The continuation paragraph belongs to the same speech.
    assert "mandatory breach notification" in opening.text
    # Entities decoded, markup gone, the speaker label consumed.
    assert "&quot;" not in opening.text
    assert "<p>" not in opening.text
    assert not opening.text.startswith("Mrs Josephine Teo")


def test_question_number_taken_from_the_source_field(
    documents: list[Document],
) -> None:
    asked = next(
        d for d in documents if d.meta.get("speaker") == "Mr Louis Ng Kok Kwang"
    )
    assert asked.meta["question_no"] == "6"
    assert asked.meta["is_question"] is True
    # The "X asked the Minister ..." form has no colon, so the whole sentence
    # is the question and the prefix is kept.
    assert asked.text.startswith("asked the Minister for Manpower")


def test_section_type_and_page_range_preserved(documents: list[Document]) -> None:
    sections = _by_level(documents, "section")
    assert [s.meta["section_type"] for s in sections] == ["OS", "OA", "BP"]
    assert sections[2].meta["section_type_label"] == "Bill Proceedings"
    assert sections[0].meta["start_page"] == 1
    assert sections[0].meta["end_page"] == 4


def test_provenance_points_at_the_snapshot(
    documents: list[Document], snapshot: Snapshot
) -> None:
    for document in documents:
        assert document.provenance.snapshot_sha256 == snapshot.sha256
        assert document.provenance.adapter == "hansard"
        assert document.provenance.parser_rev == HansardAdapter.parser_rev


def test_records_validate_against_the_json_schema(documents: list[Document]) -> None:
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(
        (Path(__file__).parents[1] / "schemas" / "document.schema.json").read_text(
            encoding="utf-8"
        )
    )
    for document in documents:
        jsonschema.validate(json.loads(document.to_jsonl()), schema)


def test_plan_skips_weekends() -> None:
    units = list(HansardAdapter().plan(since=date(2024, 2, 5), until=date(2024, 2, 11)))
    assert len(units) == 5
    assert units[0].key == "hansard:2024-02-05"
