"""PDPC's public listing API, hydration HTML and full PDF grounds.

Only fetch accesses the network. Discovery snapshots let plan expand the
queue locally after the discovery unit, including after an interrupted run.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
from collections.abc import Iterator
from contextlib import suppress
from datetime import date, datetime
from functools import lru_cache
from importlib import import_module
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .. import urn as urnlib
from ..config import default_paths
from ..models import Authority, Corpus, Dates, Document, Provenance
from ..net.cache import Snapshot, SnapshotStore
from ..net.client import Client
from .base import WorkUnit
from .hansard import _clean

BASE_URL = "https://www.pdpc.gov.sg"
LISTING_PATH = "/organisations/regulations-decisions/enforcement-decisions"
API_URL = BASE_URL + "/api/listing-api"
SITEMAP_URL = BASE_URL + "/sitemap.xml"
COLLECTIONS = ("Commission's Decisions", "Voluntary Undertakings")
PAGE_SIZE = 100
CITATION_RE = re.compile(
    r"(?:Decision Citation:\s*)?\[?(\d{4})\]?\s*(SGPDPC[SR]?)\s*\[?(\d+)\]?"
    r"(\s*\(NFA\))?", re.I,
)
PENALTY_CONTEXT_RE = re.compile(
    r"(?:financial\s+penalt(?:y|ies)\s+of\s+|penalty\s+of\s+)"
    r"((?:S?\$[\d,]+(?:\.\d{2})?)(?:\s+and\s+S?\$[\d,]+(?:\.\d{2})?)*)", re.I,
)
OBLIGATIONS = (
    "Consent", "Purpose Limitation", "Notification", "Access and Correction",
    "Accuracy", "Protection", "Retention Limitation", "Transfer Limitation", "Accountability",
)


def _walk(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def detail_content(body: str) -> str:
    """Read rich text carried by Next's hydration stream, without executing JS."""
    soup = BeautifulSoup(body, "lxml")
    blocks: list[str] = []
    chunks: list[str] = []
    for script in soup.find_all("script"):
        raw = script.get_text()
        prefix = "self.__next_f.push("
        if raw.startswith(prefix):
            packet = json.loads(raw[len(prefix):].removesuffix(";").removesuffix(")"))
            if len(packet) > 1 and isinstance(packet[1], str):
                chunks.append(packet[1])
    stream = "".join(chunks).encode("utf-8")
    rows: dict[str, Any] = {}
    while stream:
        key, sep, remaining = stream.partition(b":")
        if not sep:
            raise ValueError("Incomplete PDPC hydration row")
        if remaining.startswith(b"T"):
            length, _, remaining = remaining[1:].partition(b",")
            size = int(length, 16)
            if len(remaining) < size:
                raise ValueError("Incomplete PDPC hydration text")
            rows[key.decode()] = remaining[:size].decode("utf-8")
            stream = remaining[size:]
        else:
            line, _, stream = remaining.partition(b"\n")
            # React import/hint records contain no article text.
            with suppress(ValueError):
                rows[key.decode()] = json.loads(line)
        stream = stream.lstrip(b"\n")
    for value in rows.values():
        if not isinstance(value, (dict, list)) or '"article"' not in json.dumps(value):
            continue
        for item in _walk(value):
            content = item.get("content")
            if isinstance(content, str) and re.fullmatch(r"\$[a-f0-9]+", content):
                content = rows.get(content[1:])
                if not isinstance(content, str):
                    raise ValueError("Missing PDPC hydration content reference")
            if isinstance(content, str) and content not in blocks:
                blocks.append(content)
    if not blocks:
        blocks = [str(el) for el in soup.select("article .rte") if el.get_text(strip=True)]
    return "\n".join(blocks)


def _paragraphs(markup: str) -> str:
    soup = BeautifulSoup(markup, "lxml")
    for el in soup.find_all(["script", "style"]):
        el.decompose()
    for el in soup.find_all(["p", "div", "li", "h1", "h2", "h3", "br", "tr"]):
        el.append("\n")
    return "\n\n".join(_clean(line) for line in soup.get_text().splitlines() if _clean(line))


def normalise_case_name(title: str) -> str:
    match = re.search(r"\bby\s+(.+)$", title, re.I)
    return "Re " + match[1].strip() if match else title


def _penalty(summary: str, types: list[str]) -> tuple[int | None, str | None, str | None]:
    # Only the case summary: grounds often quote penalties in other cases.
    matches = list(PENALTY_CONTEXT_RE.finditer(summary))
    if matches:
        amounts = [m.group(1) for m in matches]
        values = re.findall(r"\$([\d,]+(?:\.\d{2})?)", " ".join(amounts))
        numbers = [float(v.replace(",", "")) for v in values]
        if all(v.is_integer() for v in numbers):
            return sum(int(v) for v in numbers), "; ".join(amounts), None
    lower = summary.lower()
    if "Financial Penalty" not in types:
        for marker, reason in [("no further action", "nfa"), ("no breach", "no_breach"),
                               ("not in breach", "no_breach"), ("warning", "warning")]:
            if marker in lower:
                return 0, None, reason
        if "Directions" in types or re.search(r"directions (?:were )?(?:issued|imposed)", lower):
            return 0, None, "directions_only"
    return None, None, None


@lru_cache(maxsize=1)
def _ocr_engine() -> Any:
    try:
        RapidOCR = import_module("rapidocr").RapidOCR
    except ImportError as exc:
        raise RuntimeError("Scanned PDF needs the `ocr` extra") from exc
    root = Path(os.environ.get("SGCORPUS_OCR_MODELS", "data/ocr-models"))
    models = {kind: root / f"{kind.lower()}.onnx" for kind in ("Det", "Cls", "Rec")}
    if not all(path.is_file() for path in models.values()):
        raise RuntimeError("Install local det.onnx, cls.onnx and rec.onnx in SGCORPUS_OCR_MODELS; parse never downloads models")
    return RapidOCR(params={
        **{f"{kind}.model_path": str(path.resolve()) for kind, path in models.items()},
        "Global.log_level": "error", "Global.text_score": 0.0,
        "EngineConfig.onnxruntime.intra_op_num_threads": 2,
        "EngineConfig.onnxruntime.inter_op_num_threads": 2,
    })


def _ocr_page(body: bytes, page_number: int) -> str:
    """Local CPU OCR with explicit local model paths; never downloads models."""
    pypdfium2 = import_module("pypdfium2")
    engine = _ocr_engine()
    pdf = pypdfium2.PdfDocument(body)
    try:
        page = pdf[page_number]
        bitmap = page.render(scale=3)
        try:
            result = engine(bitmap.to_numpy())
            return "\n".join(result.txts or [])
        finally:
            bitmap.close()
            page.close()
    finally:
        pdf.close()


def pdf_text(body: bytes) -> tuple[str, list[int]]:
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(body))
    pages: list[str] = []
    recovered: list[int] = []
    for index, page in enumerate(reader.pages):
        text = page.extract_text() or ""
        # Preserve native text; OCR image-bearing pages with little readable
        # text (a page number alone does not make a scan machine readable).
        if len(re.sub(r"\s", "", text)) < 40 and page.images:
            ocr = _ocr_page(body, index)
            if not ocr.strip():
                raise ValueError(f"OCR returned no text on page {index + 1}")
            text = text + "\n" + ocr
            recovered.append(index + 1)
        pages.append("\n".join(_clean(line) for line in text.splitlines()))
    if not any(p.strip() for p in pages):
        raise ValueError("PDF has no extractable text")
    return "\n\f\n".join(pages), recovered


class PdpcAdapter:
    name = "pdpc"
    corpus = Corpus.PDPC
    authority = Authority.PDPC
    adapter_version = "1.0.0"
    parser_rev = 8
    spec_doc = "docs/sources/pdpc.md"

    def __init__(self, *, snapshot_root: Path | None = None) -> None:
        self.snapshot_root = snapshot_root or default_paths().snapshots

    def configure(self, **options: Any) -> None:
        if "snapshot_root" in options:
            self.snapshot_root = Path(options["snapshot_root"])

    def plan(self, since: date | None = None, until: date | None = None) -> Iterator[WorkUnit]:
        yield WorkUnit(f"pdpc:sitemap:{date.today()}", {"kind": "sitemap"})
        maps = [s for s in SnapshotStore(self.snapshot_root).iter_snapshots(self.name)
                if s.params.get("kind") == "sitemap"]
        sitemap_urls = []
        if maps:
            latest = max(maps, key=lambda s: s.retrieved_at)
            sitemap_urls = [el.get_text() for el in BeautifulSoup(latest.text(), "xml").find_all("loc")]
        for collection in COLLECTIONS:
            yield WorkUnit(f"pdpc:listing:{collection}:{date.today()}",
                           {"kind": "listing", "collection": collection})
            # The ingest driver has persisted the discovery responses before
            # resuming this generator. On restart it loads those same bytes.
            pages: dict[int, Snapshot] = {}
            for snap in SnapshotStore(self.snapshot_root).iter_snapshots(self.name):
                if snap.params.get("kind") != "listing" or snap.params.get("collection") != collection:
                    continue
                page = int(snap.params["page"])
                if page not in pages or snap.retrieved_at > pages[page].retrieved_at:
                    pages[page] = snap
            if not pages:
                raise ValueError("PDPC discovery snapshots missing; run fetch through ingest")
            total = int(pages[1].json()["totalItems"])
            items: dict[str, dict[str, Any]] = {}
            for page in range(1, (total + PAGE_SIZE - 1) // PAGE_SIZE + 1):
                for item in pages[page].json()["data"]:
                    items[item["id"]] = item
            if len(items) != total:
                raise ValueError(f"PDPC listing changed during discovery: {len(items)} != {total}")
            for identifier, item in items.items():
                issued = datetime.strptime(item["date"], "%d %b %Y").date()
                if (since and issued < since) or (until and issued > until):
                    continue
                slug = item["href"].rstrip("/").rsplit("/", 1)[-1]
                title_slug = re.sub(r"[^a-z0-9]+", "-", item["title"].lower()).strip("-")
                # Every candidate URL comes verbatim from the published sitemap.
                candidates = [url for url in sitemap_urls
                              if url.rsplit("/", 1)[-1] == slug
                              or url.rsplit("/", 1)[-1] == title_slug
                              or url.rsplit("/", 1)[-1].startswith(title_slug + "-")]
                yield WorkUnit(f"pdpc:decision:{identifier}",
                               {"kind": "detail", "item": item, "sitemap_candidates": candidates})

    def fetch(self, unit: WorkUnit, client: Client) -> Iterator[tuple[str, bytes, int, dict[str, Any]]]:
        if unit.payload["kind"] == "sitemap":
            response = client.get(SITEMAP_URL)
            if response is None:
                raise RuntimeError("PDPC sitemap request exhausted retries")
            response.raise_for_status()
            if not BeautifulSoup(response.text, "xml").find("urlset"):
                raise ValueError("PDPC sitemap is not a URL set")
            yield str(response.url), response.content, 200, {"kind": "sitemap"}
            return
        if unit.payload["kind"] == "listing":
            page = 1
            count = 0
            while True:
                params = {"listingtype": "enforcement_decisions", "itemsperpage": PAGE_SIZE,
                          "pathname": LISTING_PATH, "type": unit.payload["collection"],
                          "page": page, "sort": "oldest"}
                response = client.get(API_URL, params=params)
                if response is None:
                    raise RuntimeError("PDPC listing request exhausted retries")
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload.get("data"), list) or not isinstance(payload.get("totalItems"), int):
                    raise ValueError("Unexpected PDPC listing shape")
                yield str(response.url), response.content, 200, {**unit.payload, "page": page}
                count += len(payload["data"])
                if count >= payload["totalItems"]:
                    return
                if not payload["data"]:
                    raise ValueError("PDPC pagination stopped before listing total")
                page += 1
        item = unit.payload["item"]
        url = urljoin(BASE_URL, item["href"])
        response = client.get(url)
        if response is None:
            raise RuntimeError(f"PDPC detail request exhausted retries: {url}")
        if response.status_code == 404:
            for candidate in unit.payload.get("sitemap_candidates", []):
                if candidate == url or urlparse(candidate).hostname != "www.pdpc.gov.sg":
                    continue
                alternate = client.get(candidate)
                if alternate is None or alternate.status_code != 200:
                    continue
                candidate_page = BeautifulSoup(alternate.text, "lxml")
                heading = candidate_page.select_one("h1")
                published = candidate_page.select_one(".page-banner__date")
                if (heading and published and _clean(heading.get_text()) == _clean(item["title"])
                        and _clean(published.get_text()).removeprefix("Published on ") == item["date"]):
                    response = alternate
                    url = str(alternate.url)
                    break
        response.raise_for_status()
        content = detail_content(response.text)
        if not content.strip():
            raise ValueError(f"PDPC detail has no rich text: {url}")
        links = list(dict.fromkeys(urljoin(BASE_URL, str(a["href"]))
                    for a in BeautifulSoup(content, "lxml").select("a[href]")
                    if str(a["href"]).startswith("/assets/") or ".pdf" in str(a["href"]).lower()))
        params = {"kind": "detail", "item": item, "has_pdf": bool(links)}
        yield str(response.url), response.content, 200, params
        for link in links:
            if urlparse(link).hostname != "www.pdpc.gov.sg":
                raise ValueError(f"Unexpected external PDPC asset host: {link}")
            pdf = client.get(link)
            if pdf is None:
                raise RuntimeError(f"PDPC PDF request exhausted retries: {link}")
            pdf.raise_for_status()
            if not pdf.content.startswith(b"%PDF"):
                raise ValueError(f"PDPC grounds link is not a PDF: {link}")
            yield str(pdf.url), pdf.content, 200, {
                "kind": "pdf", "item": item, "detail_html": response.text,
                "detail_sha256": hashlib.sha256(response.content).hexdigest(), "detail_url": url,
            }

    def parse(self, snapshot: Snapshot) -> Iterator[Document]:
        kind = snapshot.params.get("kind")
        if kind in ("listing", "sitemap") or (kind == "detail" and snapshot.params.get("has_pdf")):
            return
        if kind not in ("pdf", "detail"):
            raise ValueError(f"Unknown PDPC snapshot kind: {kind}")
        item = snapshot.params["item"]
        body = snapshot.params["detail_html"] if kind == "pdf" else snapshot.text()
        content = detail_content(body)
        summary = _paragraphs(content)
        title = _clean(item["title"])
        tags = [_clean(a.get_text()) for a in BeautifulSoup(body, "lxml").select(".browse-by a")]
        types = [tag for tag in tags if tag in ("Financial Penalty", "Directions", "Warning", "No Breach")]
        if item["topic"] == "Voluntary Undertakings":
            types = ["Undertaking"]
        if PENALTY_CONTEXT_RE.search(summary) and "Financial Penalty" not in types:
            types.append("Financial Penalty")
        if re.search(r"directions (?:were )?(?:issued|imposed)", summary, re.I) and "Directions" not in types:
            types.append("Directions")
        evidence = " ".join(tags) if any(o in tags for o in OBLIGATIONS) else title
        obligations = [o for o in OBLIGATIONS if re.search(r"\b" + re.escape(o) + r"\b", evidence, re.I)]
        if "Openness" in evidence and "Accountability" not in obligations:
            obligations.append("Accountability")
        text, ocr_pages = pdf_text(snapshot.body) if kind == "pdf" else (summary, [])
        # First citation on cover/front matter, never a citation in the summary's links.
        found = next((match for line in "\n".join(text.split("\f")[:3]).splitlines()
                      if (match := CITATION_RE.fullmatch(line.strip()))), None) if kind == "pdf" else None
        citation = (f"[{found[1]}] {found[2].upper()} {found[3]}"
                    + (" (NFA)" if found[4] else "")) if found else None
        identifier = urnlib.from_neutral_citation(citation, corpus="pdpc") if citation else urnlib.provisional_pdpc(item["href"].rstrip("/").rsplit("/", 1)[-1])
        canonical = str(identifier)
        if kind == "pdf":
            identifier = identifier.child("publication-" + snapshot.sha256)
        penalty, stated, reason = _penalty(title + "\n" + summary, types)
        reconsideration = bool(citation and "SGPDPCR" in citation)
        if reconsideration:
            # The listing summary describes the original decision; do not
            # attribute its penalty to a separate reconsideration attachment.
            types = ["Reconsideration"]
            penalty, stated, reason = None, None, None
        yield Document(
            urn=str(identifier), corpus=self.corpus, authority=self.authority,
            title=normalise_case_name(title), citation=citation,
            dates=Dates(issued=datetime.strptime(item["date"], "%d %b %Y").date()), text=text,
            meta={"canonical_urn": canonical, "neutral_citation": citation, "citation_provisional": citation is None,
                  "respondent": normalise_case_name(title).removeprefix("Re "),
                  "obligations": obligations, "obligations_source": "tags" if any(o in tags for o in OBLIGATIONS) else "title",
                  "decision_types": types, "penalty_sgd": penalty, "penalty_stated": stated,
                  "no_penalty_reason": reason, "sector": None, "summary": summary,
                  "source_id": item["id"], "issued_basis": "listing_publication_date", "source_tags": tags, "has_full_text": kind == "pdf" or types == ["Undertaking"],
                  "case_numbers": list(dict.fromkeys(re.findall(r"\bDP-\d{4}-\s*[A-Z]\d+\b", text))),
                  "ocr_pages": ocr_pages, "detail_sha256": snapshot.params.get("detail_sha256"),
                  "detail_url": snapshot.params.get("detail_url", snapshot.url)},
            provenance=Provenance(source_url=snapshot.url, retrieved_at=snapshot.retrieved_at,
                                  snapshot_sha256=snapshot.sha256, adapter=self.name,
                                  adapter_version=self.adapter_version, parser_rev=self.parser_rev),
        )
