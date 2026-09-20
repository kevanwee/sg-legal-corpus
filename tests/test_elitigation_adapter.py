from __future__ import annotations

from datetime import date
from pathlib import Path

import httpx
import pytest
import respx
from bs4 import BeautifulSoup

from sgcorpus.adapters.elitigation import ElitigationAdapter, listing_items
from sgcorpus.config import POLICIES
from sgcorpus.net.cache import Snapshot, SnapshotStore
from sgcorpus.net.client import Client

FIXTURES = Path(__file__).parent / "fixtures"


def snapshot(tmp_path: Path, body: bytes | None = None) -> Snapshot:
    item = listing_items((FIXTURES / "elitigation_listing.html").read_text(encoding="utf-8"))[2][0]
    return SnapshotStore(tmp_path).put(adapter="elitigation", status=200,
        url="https://www.elitigation.sg" + item["href"],
        body=body or (FIXTURES / "elitigation_judgment.html").read_bytes(),
        params={"kind": "judgment", "item": item})


def test_source_numbering_and_footnote_text(tmp_path: Path) -> None:
    docs = list(ElitigationAdapter().parse(snapshot(tmp_path)))
    root, first, seventh = docs
    assert root.urn == "urn:sg:judgment:2024_SGHC_331"
    assert root.parts == [root.urn + ":para1", root.urn + ":para7"]
    assert first.text.startswith("1 ") and seventh.text.startswith("7 ")
    assert "Agreed Bundle" in seventh.text
    assert "FootNote" not in seventh.text
    assert "Soh" in first.text and "Pong" in first.text
    assert root.dates.issued == date(2024, 12, 31)
    assert root.meta["coram"] == ["Mavis Chionh Sze Chyi J"]
    assert len(root.meta["counsel"]) == 3
    assert root.meta["counsel"][0]["side"] == "the Prosecution"
    assert len(root.meta["catchwords"]) == 3
    assert root.provenance.snapshot_sha256


def test_no_truncation_and_continuation_blocks(tmp_path: Path) -> None:
    body = (FIXTURES / "elitigation_judgment.html").read_text(encoding="utf-8")
    soup = BeautifulSoup(body, "lxml")
    first = soup.select_one(".Judg-1")
    assert first is not None
    quote = soup.new_tag("div", attrs={"class": "Judg-Quote-0"})
    quote.string = "quotation " * 4000 + "QUOTATION END"
    first.insert_after(quote)
    table = soup.new_tag("table")
    table.append(BeautifulSoup('<tr><td>Cell A</td><td>Cell B</td><td><div class="Judg-1">1 June 2024</div></td></tr>', "lxml").tr)
    quote.insert_after(table)
    docs = list(ElitigationAdapter().parse(snapshot(tmp_path, str(soup).encode())))
    assert len(docs[1].text) > 32767
    assert "QUOTATION END" in docs[1].text and "Cell B" in docs[1].text
    assert "QUOTATION END" in docs[0].text
    assert "Cell A Cell B" in docs[0].text
    assert len(docs) == 3  # A table date is not paragraph 1 a second time.


def test_ambiguous_pincites_preserve_text_and_report_gap(tmp_path: Path) -> None:
    body = (FIXTURES / "elitigation_judgment.html").read_text(encoding="utf-8")
    soup = BeautifulSoup(body, "lxml")
    root = soup.select_one("#divJudgement")
    assert root is not None
    duplicate = soup.new_tag("div", attrs={"class": "Judg-1"})
    duplicate.string = "7\u2003Second opinion paragraph"
    root.append(duplicate)
    root, = ElitigationAdapter().parse(snapshot(tmp_path, str(soup).encode()))
    assert "Second opinion paragraph" in root.text
    assert "Ambiguous printed" in root.meta["paragraph_numbering_error"]
    assert root.parts == [] and root.meta["paragraph_count"] is None


def test_quoted_numbers_remain_continuations(tmp_path: Path) -> None:
    soup = BeautifulSoup((FIXTURES / "elitigation_judgment.html").read_bytes(), "lxml")
    first = soup.select_one(".Judg-1")
    assert first is not None
    quote = soup.new_tag("div", attrs={"class": "Judg-1"})
    quote.string = "32 A quoted judgment says this."
    first.insert_after(quote)
    docs = list(ElitigationAdapter().parse(snapshot(tmp_path, str(soup).encode())))
    assert docs[0].parts == [docs[0].urn + ":para1", docs[0].urn + ":para7"]
    assert "32 A quoted judgment" in docs[1].text


def test_error_pages_are_not_empty_discovery(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="listing count"):
        listing_items("<html>System error. Please try again later.</html>")
    with pytest.raises(ValueError, match="judgment body"):
        list(ElitigationAdapter().parse(snapshot(tmp_path, b"<html>System error</html>")))


@pytest.mark.parametrize("css_class", ["txt-body", "CaseNumber", "title"])
def test_cover_citation_variants_and_listing_title(tmp_path: Path, css_class: str) -> None:
    soup = BeautifulSoup((FIXTURES / "elitigation_judgment.html").read_bytes(), "lxml")
    citation = soup.select_one(".HN-NeutralCit")
    assert citation is not None
    citation["class"] = [css_class, "text-center"]
    for title in soup.select(".HN-CaseName"):
        title.decompose()
    docs = list(ElitigationAdapter().parse(snapshot(tmp_path, str(soup).encode())))
    assert docs[0].citation == "[2024] SGHC 331"
    assert "Soh Jing Zhe" in docs[0].title


def test_plan_reads_cached_listing_and_source_href(tmp_path: Path) -> None:
    adapter = ElitigationAdapter()
    adapter.configure(snapshot_root=tmp_path)
    SnapshotStore(tmp_path).put(adapter="elitigation", url="https://www.elitigation.sg/gd/Home/Index",
        body=(FIXTURES / "elitigation_listing.html").read_bytes(), status=200,
        params={"kind": "listing", "filter": "SUPCT", "year": 2024, "page": 1})
    work = list(adapter.plan(date(2024, 1, 1), date(2024, 12, 31)))
    judgments = [w for w in work if w.payload["kind"] == "judgment"]
    assert len(judgments) == 1
    assert judgments[0].payload["item"]["href"] == "/gd/s/2024_SGHC_331"
    assert {w.payload["filter"] for w in work} == {"SUPCT", "STATECT", "FAMCT"}
    assert list(adapter.parse(next(SnapshotStore(tmp_path).iter_snapshots("elitigation")))) == []


def test_live_family_layout_uses_printed_numbers(tmp_path: Path) -> None:
    snap = SnapshotStore(tmp_path).put(adapter="elitigation", status=200,
        url="https://www.elitigation.sg/gd/s/2024_SGFC_12",
        body=(FIXTURES / "elitigation_family.html").read_bytes(),
        params={"kind": "judgment", "item": {"citation": "[2024] SGFC 12", "title": "WUK v WUL", "issued": "2024-02-15"}})
    docs = list(ElitigationAdapter().parse(snap))
    assert len(docs) == 4
    assert docs[0].meta["court"] == "SGFC"
    assert docs[0].authority.value == "FAMCT"
    assert docs[1].urn.endswith(":para1")
    assert docs[-1].urn.endswith(":para3")


@respx.mock
def test_fetch_rejects_citation_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sgcorpus.net.client.time.sleep", lambda _: None)
    adapter = ElitigationAdapter()
    adapter.configure(snapshot_root=tmp_path)
    from sgcorpus.adapters.base import WorkUnit
    body = (FIXTURES / "elitigation_judgment.html").read_bytes()
    respx.get("https://www.elitigation.sg/robots.txt").mock(return_value=httpx.Response(200, text="User-agent: *\nAllow: /"))
    respx.get("https://www.elitigation.sg/gd/s/2024_SGCA_1").mock(return_value=httpx.Response(200, content=body))
    unit = WorkUnit("test", {"kind": "judgment", "item": {"href": "/gd/s/2024_SGCA_1", "citation": "[2024] SGCA 1"}})
    with Client(POLICIES["elitigation"]) as client, pytest.raises(ValueError, match="citation differs"):
        list(adapter.fetch(unit, client))
