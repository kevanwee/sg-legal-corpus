"""The Document envelope and its typed payloads.

One record shape for every corpus. Source-specific material lives in ``meta``;
everything outside ``meta`` means the same thing everywhere. See
docs/corpus-spec.md and docs/adr/0001-one-record-shape.md.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import urn as urnlib


class Corpus(StrEnum):
    ACT = "act"
    SL = "sl"
    JUDGMENT = "judgment"
    PDPC = "pdpc"
    HANSARD = "hansard"
    BILL = "bill"


class Authority(StrEnum):
    AGC = "AGC"
    SUPCT = "SUPCT"
    STATECT = "STATECT"
    FAMCT = "FAMCT"
    PDPC = "PDPC"
    PARL = "PARL"


class RefKind(StrEnum):
    CITES = "cites"
    CONSIDERS = "considers"
    APPLIES = "applies"
    DISTINGUISHES = "distinguishes"
    FOLLOWS = "follows"
    OVERRULES = "overrules"
    DOUBTS = "doubts"
    AMENDS = "amends"
    REPEALS = "repeals"
    MADE_UNDER = "made_under"
    ENACTED_BY = "enacted_by"
    DEBATED_IN = "debated_in"


class Dates(BaseModel):
    model_config = ConfigDict(frozen=True)

    issued: date
    in_force_from: date | None = None
    in_force_to: date | None = None
    """None means currently in force. Populated for act/sl only; a record
    without these cannot answer a point-in-time question and is reported as
    incomplete by corpus_stats."""


class Provenance(BaseModel):
    """Mandatory on every record, never inferred.

    A record whose snapshot hash does not match a file in the snapshot store
    fails validation. This is what makes any claim the corpus makes either
    reproducible or refutable.
    """

    model_config = ConfigDict(frozen=True)

    source_url: str
    retrieved_at: datetime
    snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    adapter: str
    adapter_version: str
    parser_rev: int = Field(ge=1)


class Document(BaseModel):
    model_config = ConfigDict(extra="forbid")

    urn: str
    corpus: Corpus
    authority: Authority
    title: str
    citation: str | None = None
    dates: Dates
    language: Literal["en", "ms", "zh", "ta"] = "en"

    text: str = ""
    """Complete plain text. Never truncated, at any stage, for any reason --
    the defect that makes the prototype Hansard output unusable."""

    parts: list[str] = Field(default_factory=list)
    parent: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)
    provenance: Provenance

    @field_validator("urn", "parent")
    @classmethod
    def _valid_urn(cls, value: str | None) -> str | None:
        if value is not None and not urnlib.is_valid(value):
            raise ValueError(f"invalid URN: {value!r}")
        return value

    @field_validator("parts")
    @classmethod
    def _valid_parts(cls, value: list[str]) -> list[str]:
        for part in value:
            if not urnlib.is_valid(part):
                raise ValueError(f"invalid part URN: {part!r}")
        return value

    def to_jsonl(self) -> str:
        return self.model_dump_json(exclude_none=False)


class Ref(BaseModel):
    """A typed edge in the corpus graph."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_urn: str = Field(alias="from")
    to_urn: str | None = Field(default=None, alias="to")
    kind: RefKind
    raw: str | None = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    """Below 1.0 means inferred rather than read. Every MCP response that
    traverses an inferred edge surfaces this rather than hiding it."""
    resolver: str | None = None

    @field_validator("raw")
    @classmethod
    def _unresolved_keeps_raw(cls, value: str | None, info: Any) -> str | None:
        if info.data.get("to_urn") is None and not value:
            raise ValueError("an unresolved ref must preserve its raw citation string")
        return value
