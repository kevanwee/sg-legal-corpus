"""Public judgments: source href discovery, full text and printed pincites."""
from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urljoin, urlsplit

from bs4 import BeautifulSoup, Tag

from .. import urn as urnlib
from ..config import default_paths
from ..models import Authority, Corpus, Dates, Document, Provenance
from ..net.cache import Snapshot, SnapshotStore
from ..net.client import Client
from .base import WorkUnit
from .hansard import _clean

BASE_URL = "https://www.elitigation.sg"
LISTING = BASE_URL + "/gd/Home/Index?Filter={filter}&YearOfDecision={year}&SortBy=Score&CurrentPage={page}"
FILTERS = {"SUPCT": Authority.SUPCT, "STATECT": Authority.STATECT, "FAMCT": Authority.FAMCT}
COURTS = ("SGCA", "SGHC(A)", "SGHC", "SGHC(I)", "SGHCR", "SGDC", "SGMC", "SGFC")


def listing_items(body: str) -> tuple[int, int, list[dict[str, Any]]]:
    soup = BeautifulSoup(body, "lxml")
    count = re.search(r"Total Judgment\(s\) Found\s*:\s*([\d,]+)", soup.get_text(" ", strip=True))
    if not count:
        raise ValueError("eLitigation listing count missing (possible HTTP-200 system error)")
    total = int(count[1].replace(",", ""))
    pages = [1]
    for anchor in soup.select('a[href]'):
        values = parse_qs(urlsplit(str(anchor["href"])).query)
        if values.get("CurrentPage", [""])[0].isdigit():
            pages.append(int(values["CurrentPage"][0]))
    items = []
    for card in soup.select(".card.col-12"):
        link = card.select_one('a.gd-heardertext[href]')
        citation = card.select_one(".citation-num-link")
        issued = card.select_one(".decision-date-link")
        case = card.select_one(".case-num-link")
        if link is None or citation is None or issued is None:
            raise ValueError("eLitigation listing card missing identity/date")
        href = str(link["href"])
        if not href.startswith("/gd/s/"):
            raise ValueError(f"Unexpected judgment href: {href}")
        issued_text = _clean(issued.get_text()).removeprefix("Decision Date:").strip(" |")
        items.append({"href": href, "title": _clean(link.get_text()),
                      "citation": _clean(citation.get_text()).strip(" |"),
                      "issued": datetime.strptime(issued_text, "%d %b %Y").date().isoformat(),
                      "case_number": _clean(case.get_text()) if case else None,
                      "catchwords": [_clean(x.get_text()).strip("[]") for x in card.select("a.gd-cw")]})
    if total and not items:
        raise ValueError("Nonempty eLitigation listing has no cards")
    return total, max(pages), items


def _judgment(body: str) -> Tag:
    soup = BeautifulSoup(body, "lxml")
    root = soup.select_one("#divJudgement")
    if root is None or root.select_one(".HN-NeutralCit") is None:
        raise ValueError("eLitigation judgment body/citation missing")
    return root


def _text(root: Tag) -> str:
    # Inline spans must remain contiguous (e.g. section numbers and italics).
    copy = BeautifulSoup(str(root), "lxml")
    for element in copy.select("script, style, .modal-header, .modal-footer"):
        element.decompose()
    for br in copy.find_all("br"):
        br.replace_with("\n")
    for block in copy.find_all(["div", "p", "tr", "table"]):
        block.append("\n")
    return "\n".join(_clean(line) for line in copy.get_text().splitlines() if _clean(line))


class ElitigationAdapter:
    name = "elitigation"
    corpus = Corpus.JUDGMENT
    authority = Authority.SUPCT
    adapter_version = "1.0.0"
    parser_rev = 1
    spec_doc = "docs/sources/elitigation.md"

    def __init__(self) -> None:
        self.snapshot_root = default_paths().snapshots

    def configure(self, **options: Any) -> None:
        if "snapshot_root" in options:
            self.snapshot_root = Path(options["snapshot_root"])

    def plan(self, since: date | None = None, until: date | None = None) -> Iterator[WorkUnit]:
        start, end = since or date.today(), until or date.today()
        if end < start:
            raise ValueError("End date precedes start date")
        store = SnapshotStore(self.snapshot_root)
        for year in range(start.year, end.year + 1):
            for court_filter in FILTERS:
                def page_unit(page: int, court_filter: str = court_filter, year: int = year) -> WorkUnit:
                    return WorkUnit(key=f"elitigation:listing:{court_filter}:{year}:{page}",
                                    payload={"kind": "listing", "filter": court_filter,
                                             "year": year, "page": page})
                yield page_unit(1)
                snapshots = [s for s in store.iter_snapshots(self.name)
                             if s.params.get("kind") == "listing"
                             and s.params.get("filter") == court_filter
                             and s.params.get("year") == year]
                first = [s for s in snapshots if s.params.get("page") == 1]
                if not first:
                    continue  # Failed discovery remains pending in the checkpoint.
                total, last, _ = listing_items(max(first, key=lambda s: s.retrieved_at).text())
                for page in range(2, last + 1):
                    yield page_unit(page)
                latest: dict[int, Snapshot] = {}
                for snapshot in store.iter_snapshots(self.name):
                    if (snapshot.params.get("kind") == "listing"
                            and snapshot.params.get("filter") == court_filter
                            and snapshot.params.get("year") == year):
                        page = int(snapshot.params["page"])
                        if page <= last and (page not in latest or snapshot.retrieved_at > latest[page].retrieved_at):
                            latest[page] = snapshot
                seen: dict[str, dict[str, Any]] = {}
                for snapshot in latest.values():
                    reported, _, items = listing_items(snapshot.text())
                    if reported != total:
                        raise ValueError("eLitigation listing total changed during discovery; retry")
                    for item in items:
                        seen[item["href"]] = item
                # Preserve available work even if a page failed; the missing page
                # is still pending and the measured denominator remains explicit.
                for href, item in sorted(seen.items()):
                    issued = date.fromisoformat(item["issued"])
                    if (since and issued < since) or (until and issued > until):
                        continue
                    yield WorkUnit(key=f"elitigation:judgment:{href}",
                                   payload={"kind": "judgment", "item": item,
                                            "filter": court_filter, "listing_total": total})

    def fetch(self, unit: WorkUnit, client: Client) -> Iterator[tuple[str, bytes, int, dict[str, Any]]]:
        params = unit.payload
        url = (LISTING.format(**params) if params["kind"] == "listing"
               else urljoin(BASE_URL, params["item"]["href"]))
        response = client.get(url)
        if response is None or response.status_code != 200:
            raise ValueError(f"eLitigation retrieval failed: {url}")
        if params["kind"] == "listing":
            listing_items(response.text)
        else:
            root = _judgment(response.text)
            citation = root.select_one(".HN-NeutralCit")
            assert citation is not None
            if urnlib.from_neutral_citation(_clean(citation.get_text())) != urnlib.from_neutral_citation(params["item"]["citation"]):
                raise ValueError("Judgment citation differs from listing")
        yield str(response.url), response.content, response.status_code, params

    def parse(self, snapshot: Snapshot) -> Iterator[Document]:
        if snapshot.params.get("kind") == "listing":
            return
        root = _judgment(snapshot.text())
        def texts(selector: str) -> list[str]:
            return [_clean(x.get_text(" ", strip=True)) for x in root.select(selector)]
        citation = texts(".HN-NeutralCit")[0]
        urn = urnlib.from_neutral_citation(citation)
        match = urnlib.CITATION_RE.search(citation)
        assert match is not None
        court = match["court"]
        authority = Authority.STATECT if court in ("SGDC", "SGMC") else Authority.FAMCT if court == "SGFC" else Authority.SUPCT
        item = snapshot.params.get("item", {})
        if item.get("citation") and urnlib.from_neutral_citation(item["citation"]) != urn:
            raise ValueError("Judgment citation differs from saved listing")
        if item.get("issued"):
            issued = date.fromisoformat(item["issued"])
        else:
            issued = datetime.strptime(texts(".Judg-Date-Reserved")[0], "%d %B %Y").date()
        title = texts(".HN-CaseName")[0]
        coram = root.select_one(".HN-Coram")
        coram_lines = [_clean(x) for x in coram.get_text("\n").splitlines() if _clean(x)] if coram else []
        counsel = []
        for raw in texts(".Judg-Lawyers"):
            names, separator, side = raw.rpartition(" for ")
            counsel.append({"raw": raw, "names_and_firms": names if separator else raw,
                            "side": side.rstrip(".;") if separator else None,
                            "firms": re.findall(r"\(([^)]+)\)", raw)})
        meta: dict[str, Any] = {"level": "judgment", "court": court,
            "case_numbers": texts(".CaseNumber"), "listing_case_number": item.get("case_number"),
            "coram_source_lines": coram_lines, "coram": coram_lines[1:-1] if len(coram_lines) >= 3 else [],
            "authors": texts(".Judg-Author"), "counsel": counsel,
            "parties": re.split(r"\s+v\s+", title, maxsplit=1),
            "catchwords": item.get("catchwords", []), "outcome": None,
            "outcome_reason": "not reliably structured by the source"}
        provenance = Provenance(source_url=snapshot.url, retrieved_at=snapshot.retrieved_at,
                                snapshot_sha256=snapshot.sha256, adapter=self.name,
                                adapter_version=self.adapter_version, parser_rev=self.parser_rev)
        children: list[Document] = []
        numbers: set[str] = set()
        for paragraph in root.select(".Judg-1"):
            body = _text(paragraph)
            number = re.match(r"^(\d+[A-Za-z]?)(?:\s|\.)", body)
            if not number:
                continue  # Unnumbered material is retained in full in the root.
            label = number[1]
            if label in numbers:
                raise ValueError(f"Duplicate printed paragraph {label}: cannot mint an unambiguous pincite")
            numbers.add(label)
            # Continuation quotes, subparagraphs and tables belong to the preceding
            # printed paragraph; stop at the next top-level paragraph or heading.
            continuation = []
            for sibling in paragraph.next_siblings:
                if not isinstance(sibling, Tag):
                    continue
                classes = sibling.get_attribute_list("class")
                if any(str(c) == "Judg-1" or str(c).startswith(("Judg-Heading", "Judg-Sign", "Judg-Lawyers", "Judg-EOF")) for c in classes):
                    break
                continuation.append(_text(sibling))
            body = "\n".join([body, *filter(None, continuation)])
            child = urn.child("para" + label)
            children.append(Document(urn=str(child), corpus=self.corpus, authority=authority,
                                     title=title, citation=f"{citation} at [{label}]", dates=Dates(issued=issued),
                                     text=body, parent=str(urn), meta={**meta, "level": "paragraph", "paragraph": label},
                                     provenance=provenance))
        full_text = _text(root)
        if not full_text:
            raise ValueError("Judgment body is empty")
        yield Document(urn=str(urn), corpus=self.corpus, authority=authority, title=title,
                       citation=citation, dates=Dates(issued=issued), text=full_text,
                       parts=[c.urn for c in children], meta={**meta, "paragraph_count": len(children)},
                       provenance=provenance)
        yield from children
