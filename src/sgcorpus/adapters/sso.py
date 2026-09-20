"""SSO listing hrefs, batched provision HTML and explicit historical versions."""
from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup, Tag
from bs4.element import NavigableString

from .. import urn as urnlib
from ..config import default_paths
from ..models import Authority, Corpus, Dates, Document, Provenance
from ..net.cache import Snapshot, SnapshotStore
from ..net.client import Client
from .base import WorkUnit
from .hansard import _clean

BASE_URL = "https://sso.agc.gov.sg"
HREF_RE = re.compile(r"^/(Act|SL)/([A-Za-z0-9_.-]+)(?:\?[^#]*)?$")


def listing_items(body: str) -> list[dict[str, str]]:
    soup = BeautifulSoup(body, "lxml")
    if not soup.select_one("table.browse-list"):
        raise ValueError("SSO browse table missing (HTTP 200 can be an error page)")
    items = []
    for anchor in soup.select("table.browse-list tr > td:first-child > a.non-ajax[href]"):
        href = str(anchor["href"])
        match = HREF_RE.fullmatch(href)
        if match:
            items.append({"href": href, "title": _clean(anchor.get_text()),
                          "code": match[2], "corpus": "act" if match[1] == "Act" else "sl"})
    return items


def _soup(body: str) -> BeautifulSoup:
    soup = BeautifulSoup(body, "lxml")
    if not soup.select_one("#tocPanel") or not soup.select_one("#legisContent"):
        raise ValueError("SSO statute structure missing (possible soft 404)")
    return soup


def _toc(soup: BeautifulSoup) -> list[tuple[str, str]]:
    return list(dict.fromkeys((str(a["href"])[1:], _clean(a.get_text(" ", strip=True)))
                for a in soup.select('#tocPanel a.nav-link[href^="#"]')
                if a["href"] != "#top"))


def _timeline(soup: BeautifulSoup) -> list[tuple[date, str]]:
    versions: dict[date, str] = {}
    for anchor in soup.select("#versionsPopover a[href]"):
        href = str(anchor["href"])
        values = parse_qs(urlsplit(href).query)
        if values.get("ValidDate"):
            versions[datetime.strptime(values["ValidDate"][0], "%Y%m%d").date()] = href
    if not versions:
        raise ValueError("SSO version timeline missing; dates must not be guessed")
    return sorted(versions.items())


def _selected(soup: BeautifulSoup) -> date:
    button = soup.select_one("#versionsButton")
    if button is None:
        raise ValueError("SSO selected version date missing")
    return datetime.strptime(_clean(button.get_text()), "%d %b %Y").date()


def _with_ids(url: str, ids: list[str]) -> str:
    parts = urlsplit(url)
    params = parse_qs(parts.query)
    params["ProvIds"] = [",".join(ids)]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(params, doseq=True), ""))


def _root_urn(item: dict[str, Any]) -> urnlib.Urn:
    if item["corpus"] == "sl":
        parent, sep, number = item["code"].partition("-")
        if not sep:
            raise ValueError("SSO subsidiary legislation has no parent Act code")
        return urnlib.Urn("sl", parent, (number,))
    return urnlib.Urn("act", item["code"])


def _part_key(anchor: str, label: str, corpus: str) -> str:
    match = re.match(r"^pr([0-9]+[A-Za-z]*)-", anchor)
    if match:
        return ("s" if corpus == "act" else "r") + match[1]
    return anchor.rstrip("-").replace(".", "-")


def _partition_text(root: Tag, anchors: set[str]) -> dict[str, str]:
    """Walk every text node once; TOC anchors define complete text segments."""
    parts: dict[str, list[str]] = {"preamble": []}
    current = "preamble"
    def walk(node: Any) -> None:
        nonlocal current
        if isinstance(node, NavigableString):
            parts[current].append(str(node))
        elif isinstance(node, Tag):
            if node.name in ("script", "style"):
                return
            marker = str(node.get("id") or node.get("name") or "")
            if marker in anchors:
                current = marker
                parts.setdefault(current, [])
            for child in node.children:
                walk(child)
            if node.name in ("p", "div", "tr", "table", "br"):
                parts[current].append("\n")
    walk(root)
    return {key: "\n".join(_clean(line) for line in "".join(value).splitlines() if _clean(line))
            for key, value in parts.items()}


def _subparts(block: Tag, number: str) -> list[tuple[str, str, str]]:
    """Numbered subsections and letter/roman paragraphs; definitions stay intact."""
    result: list[tuple[str, str, str]] = []

    def paragraphs(container: Tag, parent: str) -> None:
        candidates = container.find_all("table", class_=re.compile(r"^p[1-9]_"))
        tables = [t for t in candidates if not any(a in candidates for a in t.parents)]
        for table in tables:
            for row in table.find_all("tr", recursive=False):
                cells = row.find_all("td", recursive=False)
                if len(cells) < 2:
                    continue
                label = _clean(cells[0].get_text())
                if not re.fullmatch(r"\([A-Za-z0-9]+\)", label):
                    continue
                child = parent + label
                result.append((child, parent, _partition_text(row, set())["preamble"]))
                paragraphs(cells[1], child)

    subsections = block.select(".prov2TxtIL, .prov2Txt")
    if subsections:
        for subsection in subsections:
            text = _partition_text(subsection, set())["preamble"]
            match = re.match(r"^[\u2014\s]*(\(\d+\))", text)
            if match:
                child = number + match[1]
                result.append((child, number, text))
                paragraphs(subsection, child)
    else:
        paragraphs(block, number)
    return result


class SsoAdapter:
    name = "sso"
    corpus = Corpus.ACT
    authority = Authority.AGC
    adapter_version = "1.0.0"
    parser_rev = 3
    spec_doc = "docs/sources/sso.md"

    def __init__(self) -> None:
        self.snapshot_root = default_paths().snapshots
        self.versions = False
        self.include_sl = False
        self.include_repealed = False

    def configure(self, **options: Any) -> None:
        if "snapshot_root" in options:
            self.snapshot_root = Path(options["snapshot_root"])
        self.versions = bool(options.get("versions", False))
        self.include_sl = bool(options.get("include_sl", False))
        self.include_repealed = bool(options.get("include_repealed", False))

    def plan(self, since: date | None = None, until: date | None = None) -> Iterator[WorkUnit]:
        if since or until:
            raise ValueError("SSO date-range discovery is unsupported; use --versions for source-enumerated history")
        collections = [("Act", "Current")]
        if self.include_repealed:
            collections.append(("Act", "Repealed"))
        if self.include_sl:
            collections.append(("SL", "Current"))
        for family, status in collections:
            path = f"/Browse/{family}/{status}/All"
            yield WorkUnit(f"sso:listing:{family}:{status}:{date.today()}", {"kind": "listing", "path": path})
            pages: dict[str, Snapshot] = {}
            for snap in SnapshotStore(self.snapshot_root).iter_snapshots(self.name):
                if snap.params.get("kind") == "listing" and snap.params.get("path") == path:
                    key = snap.params["page_url"]
                    if key not in pages or snap.retrieved_at > pages[key].retrieved_at:
                        pages[key] = snap
            if not pages:
                raise ValueError("SSO listing snapshots missing")
            seen: set[str] = set()
            for snap in pages.values():
                for item in listing_items(snap.text()):
                    if item["href"] in seen:
                        continue
                    seen.add(item["href"])
                    payload: dict[str, Any] = {"kind": "statute", "item": item, "repealed": status == "Repealed"}
                    yield WorkUnit(f"sso:{family}:{item['code']}:{status}:versions={self.versions}", payload)

    def fetch(self, unit: WorkUnit, client: Client) -> Iterator[tuple[str, bytes, int, dict[str, Any]]]:
        if unit.payload["kind"] == "listing":
            path = unit.payload["path"]
            pending = [BASE_URL + path + "?PageSize=500&SortBy=Title&SortOrder=ASC"]
            seen: set[str] = set()
            while pending:
                url = pending.pop(0)
                if url in seen:
                    continue
                seen.add(url)
                response = client.get(url)
                if response is None:
                    raise RuntimeError("SSO listing retries exhausted")
                response.raise_for_status()
                listing_items(response.text)
                yield str(response.url), response.content, 200, {**unit.payload, "page_url": url}
                soup = BeautifulSoup(response.text, "lxml")
                for a in soup.select("a[href]"):
                    href = str(a["href"])
                    if re.fullmatch(re.escape(path) + r"/\d+\?PageSize=500&SortBy=Title&SortOrder=ASC", href):
                        link = urljoin(BASE_URL, href)
                        if link not in seen and link not in pending:
                            pending.append(link)
            return
        item = unit.payload["item"]
        url = urljoin(BASE_URL, item["href"])
        response = client.get(url)
        if response is None:
            raise RuntimeError("SSO statute retries exhausted")
        response.raise_for_status()
        soup = _soup(response.text)
        current = _selected(soup)
        timeline = _timeline(soup)
        yield str(response.url), response.content, 200, {**unit.payload, "kind": "structure"}
        choices = timeline if self.versions else [(current, item["href"])]
        for start, href in choices:
            version_url = urljoin(BASE_URL, href)
            version_soup = soup
            if start != current:
                historical = client.get(version_url)
                if historical is None:
                    raise RuntimeError("SSO historical retries exhausted")
                historical.raise_for_status()
                version_soup = _soup(historical.text)
                if _selected(version_soup) != start:
                    raise ValueError("SSO returned a different historical version; refusing fallback")
                yield str(historical.url), historical.content, 200, {**unit.payload, "kind": "structure"}
            ids = [anchor for anchor, _ in _toc(version_soup)]
            if not ids:
                raise ValueError("SSO has an empty TOC")
            end = next((d for d, _ in timeline if d > start), None)
            # Bound URL length, not document text. Each chunk is validated.
            chunks: list[list[str]] = [[]]
            for anchor in ids:
                if len(_with_ids(version_url, [*chunks[-1], anchor])) > 6000:
                    chunks.append([])
                chunks[-1].append(anchor)
            for chunk_no, chunk in enumerate(chunks):
                batch = client.get(_with_ids(version_url, chunk))
                if batch is None:
                    raise RuntimeError("SSO provision retries exhausted")
                batch.raise_for_status()
                checked = _soup(batch.text)
                if _selected(checked) != start:
                    raise ValueError("SSO batch version differs from requested date")
                content = checked.select_one("#legisContent")
                assert content is not None
                missing = [anchor for anchor in chunk
                           if not content.find(id=anchor) and not content.find(None, attrs={"name": anchor})]
                if missing:
                    raise ValueError(f"SSO omitted requested anchors: {missing}")
                yield str(batch.url), batch.content, 200, {
                    **unit.payload, "kind": "provisions", "requested_ids": chunk,
                    "version_start": start.isoformat(), "version_end": end.isoformat() if end else None,
                    "version_seq": next(i + 1 for i, (d, _) in enumerate(timeline) if d == start), "historical": start != current,
                    "root_record": chunk_no == 0, "all_ids": ids,
                }

    def parse(self, snapshot: Snapshot) -> Iterator[Document]:
        if snapshot.params.get("kind") in ("listing", "structure"):
            return
        if snapshot.params.get("kind") != "provisions":
            raise ValueError("Unknown SSO snapshot kind")
        params = snapshot.params
        item = params["item"]
        soup = _soup(snapshot.text())
        selected = _selected(soup)
        if selected.isoformat() != params["version_start"]:
            raise ValueError("Snapshot selected version disagrees with provenance parameters")
        timeline = _timeline(soup)
        expected_end = next((d.isoformat() for d, _ in timeline if d > selected), None)
        if expected_end != params.get("version_end"):
            raise ValueError("SSO validity endpoint disagrees with source timeline")
        root = _root_urn(item).at(selected if params.get("historical") else None)
        toc = _toc(soup)
        content = soup.select_one("#legisContent")
        assert content is not None
        segments = _partition_text(content, {a for a, _ in toc})
        for anchor in params["requested_ids"]:
            if anchor not in segments:
                raise ValueError(f"SSO provision absent: {anchor}")
        version_dates = Dates(issued=selected, in_force_from=selected,
                              in_force_to=date.fromisoformat(expected_end) if expected_end else None)
        provenance = Provenance(source_url=snapshot.url, retrieved_at=snapshot.retrieved_at,
                                snapshot_sha256=snapshot.sha256, adapter=self.name,
                                adapter_version=self.adapter_version, parser_rev=self.parser_rev)
        parents: dict[str, str] = {}
        part: str | None = None
        division: str | None = None
        records: list[Document] = []
        urns = {a: str(root.child(_part_key(a, label, item['corpus']))) for a, label in toc}
        for anchor, label in toc:
            if label.lower().startswith('part '):
                part, division = urns[anchor], None
                parents[anchor] = str(root)
            elif label.lower().startswith('division '):
                parents[anchor] = part or str(root)
                division = urns[anchor]
            elif anchor.startswith(('Sc', 'xv', 'al')):
                parents[anchor] = str(root)
                if anchor.startswith('Sc'):
                    part, division = None, None
            else:
                parents[anchor] = division or part or str(root)
            if anchor not in params['requested_ids']:
                continue
            key = _part_key(anchor, label, item['corpus'])
            deleted = bool(re.search(r'\((?:Deleted|Repealed|Omitted)\)', label, re.I))
            text = segments[anchor]
            children = [urns[a] for a, _ in toc if parents.get(a) == urns[anchor]]
            records.append(Document(
                urn=urns[anchor], corpus=Corpus(item['corpus']), authority=self.authority,
                title=label, citation=f"{label} of {item['title']}", dates=version_dates,
                text=text, parent=parents[anchor], parts=children,
                meta={'level': 'provision' if anchor.startswith('pr') else 'structure',
                      'canonical_urn': str(_root_urn(item).child(key)), 'short_code': item['code'],
                      'source_anchor': anchor, 'provision_number': key[1:] if anchor.startswith('pr') else None,
                      'version_seq': params['version_seq'], 'repealed': params.get('repealed', False),
                      'deleted': deleted, 'issued_basis': 'source_version_date',
                      'part': part, 'division': division}, provenance=provenance,
            ))
        subrecords: list[Document] = []
        for record in records:
            anchor = record.meta['source_anchor']
            if not anchor.startswith('pr'):
                continue
            header = content.find(id=anchor)
            block = header.find_parent('div', class_='prov1') if header else None
            if block is None:
                continue
            number = record.meta['provision_number']
            def suburn(value: str) -> str:
                flattened = urnlib.provision(item['code'], value).parts[0]
                if item['corpus'] == 'sl':
                    flattened = 'r' + flattened[1:]
                return str(root.child(flattened))
            for child, parent, text in _subparts(block, number):
                subrecords.append(Document(
                    urn=suburn(child), corpus=record.corpus, authority=self.authority,
                    title=f"{child} {record.title}", citation=f"{child} of {item['title']}",
                    dates=version_dates, text=text, parent=suburn(parent),
                    meta={**record.meta, 'level': 'subprovision', 'provision_number': child,
                          'canonical_urn': str(urnlib.parse(suburn(child)).at(None))},
                    provenance=provenance,
                ))
        # Resolve parent children after the entire ordered TOC has been walked.
        for record in records:
            record.parts = [urns[a] for a, _ in toc if parents.get(a) == record.urn]
            record.parts.extend(d.urn for d in subrecords if d.parent == record.urn)
        for record in subrecords:
            record.parts = [d.urn for d in subrecords if d.parent == record.urn]
        if params.get('root_record'):
            history = '\n'.join(text for anchor, text in segments.items() if anchor.startswith('xv'))
            chapters = list(dict.fromkeys(re.findall(r'\b(?:Cap\.?|CHAPTER)\s+\d+[A-Za-z]?', history, re.I)))
            yield Document(urn=str(root), corpus=Corpus(item['corpus']), authority=self.authority,
                           title=item['title'], citation=item['title'], dates=version_dates,
                           text=segments.get('preamble', ''), parts=[urns[a] for a, _ in toc if parents[a] == str(root)],
                           meta={'level': 'statute', 'short_code': item['code'], 'repealed': params.get('repealed', False),
                                 'chapter_alias_candidates': chapters, 'legislative_history': history,
                                 'version_timeline': [{'from': d.isoformat(), 'href': h} for d, h in timeline],
                                 'made_under': str(urnlib.Urn('act', root.work)) if item['corpus'] == 'sl' else None,
                                 'issued_basis': 'source_version_date'}, provenance=provenance)
        yield from records
        yield from subrecords
