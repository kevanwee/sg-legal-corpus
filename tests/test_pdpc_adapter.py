from __future__ import annotations

import io
import json
from datetime import date
from pathlib import Path

import httpx
import pytest
import respx
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, NumberObject, StreamObject

from sgcorpus.adapters import pdpc
from sgcorpus.config import Paths
from sgcorpus.mcp.tools import handle
from sgcorpus.net.cache import Snapshot, SnapshotStore
from sgcorpus.pipeline import ingest, normalise
from sgcorpus.store import sqlite

FIXTURES = Path(__file__).parent / 'fixtures'
ITEM = json.loads((FIXTURES / 'pdpc_listing.json').read_text())['data'][0]
DETAIL = (FIXTURES / 'pdpc_detail.html').read_text()


def snapshot(tmp_path: Path, body: bytes, kind: str = 'pdf') -> Snapshot:
    return SnapshotStore(tmp_path).put(
        adapter='pdpc', url='https://www.pdpc.gov.sg/assets/fixture', body=body,
        status=200, params={'kind': kind, 'item': ITEM, 'detail_html': DETAIL},
    )


def test_live_cover_and_hydration_fixture(tmp_path: Path) -> None:
    snap = snapshot(tmp_path, (FIXTURES / 'pdpc_cover.pdf').read_bytes())
    doc, = pdpc.PdpcAdapter().parse(snap)
    assert doc.citation == '[2026] SGPDPC 1'
    assert doc.urn.startswith('urn:sg:pdpc:2026_SGPDPC_1:publication-')
    assert doc.meta['canonical_urn'] == 'urn:sg:pdpc:2026_SGPDPC_1'
    assert doc.meta['obligations'] == ['Protection', 'Accountability']
    assert doc.meta['penalty_sgd'] == 0
    assert doc.meta['no_penalty_reason'] == 'directions_only'
    assert doc.dates.issued == date(2026, 5, 7)
    assert doc.provenance.snapshot_sha256 == snap.sha256
    assert doc.provenance.retrieved_at == snap.retrieved_at
    assert doc.text


def test_hydration_text_reference_split_across_packets() -> None:
    content = '<p>' + ('完整 text ' * 10000) + 'FINAL</p>'
    row = '26:' + json.dumps(['$', 'article', None, {'data': {'content': '$29'}}]) + '\n'
    stream = row + f'29:T{len(content.encode()):x},' + content
    html = ''.join('<script>self.__next_f.push(' + json.dumps([1, part]) + ')</script>'
                   for part in (stream[:105], stream[105:]))
    assert pdpc.detail_content(html) == content


@pytest.mark.parametrize(('text', 'types', 'expected'), [
    ('financial penalty of $25,000', [], (25000, '$25,000', None)),
    ('Financial penalties of $50,000 and $10,000 were imposed', [], (60000, '$50,000 and $10,000', None)),
    ('Warning issued', ['Warning'], (0, None, 'warning')),
    ('No breach of consent', [], (0, None, 'no_breach')),
    ('No further action', [], (0, None, 'nfa')),
    ('Directions were issued', [], (0, None, 'directions_only')),
    ('Lost $90,000 in a data breach', [], (None, None, None)),
    ('Directions were issued', ['Financial Penalty'], (None, None, None)),
])
def test_penalties(text: str, types: list[str], expected: tuple[object, ...]) -> None:
    assert pdpc._penalty(text, types) == expected


def text_pdf(text: str) -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject({NameObject('/Type'): NameObject('/Font'),
                             NameObject('/Subtype'): NameObject('/Type1'),
                             NameObject('/BaseFont'): NameObject('/Helvetica')})
    page[NameObject('/Resources')] = DictionaryObject({
        NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})
    })
    stream = StreamObject()
    stream.set_data(('BT /F1 10 Tf 20 750 Td (' + text + ') Tj ET').encode())
    page[NameObject('/Contents')] = writer._add_object(stream)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def test_full_pdf_text_is_not_excel_truncated(tmp_path: Path) -> None:
    body = '[2000] SGPDPC 999 ' + 'fixture text ' * 4000 + 'FINAL SENTENCE'
    doc, = pdpc.PdpcAdapter().parse(snapshot(tmp_path, text_pdf(body)))
    assert len(doc.text) > 32767
    assert doc.text.endswith('FINAL SENTENCE')


def test_scanned_page_uses_ocr(monkeypatch: pytest.MonkeyPatch) -> None:
    writer = PdfWriter()
    page = writer.add_blank_page(width=100, height=100)
    image = StreamObject()
    image.set_data(bytes([0, 0, 0]))
    image.update({NameObject('/Type'): NameObject('/XObject'),
                  NameObject('/Subtype'): NameObject('/Image'),
                  NameObject('/Width'): NumberObject(1), NameObject('/Height'): NumberObject(1),
                  NameObject('/ColorSpace'): NameObject('/DeviceRGB'),
                  NameObject('/BitsPerComponent'): NumberObject(8)})
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/XObject'):
        DictionaryObject({NameObject('/Im0'): writer._add_object(image)})})
    out = io.BytesIO()
    writer.write(out)
    monkeypatch.setattr(pdpc, '_ocr_page', lambda body, index: '[2000] SGPDPC 999 recovered')
    text, pages = pdpc.pdf_text(out.getvalue())
    assert 'recovered' in text and pages == [1]


@respx.mock
def test_discovery_resume_and_offline_normalise(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr('sgcorpus.net.client.Client._wait', lambda self: None)
    paths = Paths(tmp_path)
    respx.get('https://www.pdpc.gov.sg/robots.txt').mock(return_value=httpx.Response(200, text='User-agent: *\nAllow: /'))
    respx.get(pdpc.SITEMAP_URL).mock(return_value=httpx.Response(200, text='<urlset></urlset>'))
    listing = respx.get(pdpc.API_URL).mock(side_effect=[
        httpx.Response(200, json={'totalItems': 1, 'data': [ITEM]}),
        httpx.Response(200, json={'totalItems': 0, 'data': []}),
    ])
    detail = respx.get(pdpc.BASE_URL + ITEM['href']).mock(return_value=httpx.Response(200, text=DETAIL))
    pdf = respx.get('https://www.pdpc.gov.sg/assets/dfce7ef8-17a3-4d27-9451-a665f6c16114').mock(return_value=httpx.Response(200, content=(FIXTURES / 'pdpc_cover.pdf').read_bytes()))
    first = ingest.run('pdpc', paths, limit=2)
    assert first['fetched'] == 2
    second = ingest.run('pdpc', paths)
    assert second['failures'] == 0
    assert detail.call_count == pdf.call_count == 1
    assert listing.call_count == 2
    assert normalise.run('pdpc', paths)['documents'] == 1
    third = ingest.run('pdpc', paths)
    assert third['fetched'] == 0
    assert detail.call_count == pdf.call_count == 1


def test_missing_ocr_models_fails_before_engine_initialisation(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    pytest.importorskip('rapidocr')
    pdpc._ocr_engine.cache_clear()
    monkeypatch.setenv('SGCORPUS_OCR_MODELS', str(tmp_path))
    with pytest.raises(RuntimeError, match='never downloads'):
        pdpc._ocr_engine()


def test_body_citation_does_not_identify_uncited_summary(tmp_path: Path) -> None:
    doc, = pdpc.PdpcAdapter().parse(snapshot(tmp_path, text_pdf(
        'SUMMARY OF THE DECISION 1. We considered Jade E-Services [2018] SGPDPC 21.'
    )))
    assert doc.citation is None
    assert doc.meta['citation_provisional'] is True


@pytest.mark.parametrize(('raw', 'expected'), [
    ('[2018] SGPDPC [3]', '[2018] SGPDPC 3'),
    ('[2020] SGPDPCR 1', '[2020] SGPDPCR 1'),
    ('[2026]SGPDPC1', '[2026] SGPDPC 1'),
    ('Decision Citation: [2016] SGPDPC 20', '[2016] SGPDPC 20'),
])
def test_source_citation_variants(tmp_path: Path, raw: str, expected: str) -> None:
    doc, = pdpc.PdpcAdapter().parse(snapshot(tmp_path, text_pdf(raw)))
    assert doc.citation == expected


def test_distinct_publications_survive_index_and_lookup(tmp_path: Path) -> None:
    pdf = (FIXTURES / 'pdpc_cover.pdf').read_bytes()
    first, = pdpc.PdpcAdapter().parse(snapshot(tmp_path, pdf))
    second, = pdpc.PdpcAdapter().parse(snapshot(tmp_path, pdf + b'\n'))
    conn = sqlite.connect(tmp_path / 'index.db')
    sqlite.init(conn)
    sqlite.insert_documents(conn, [first, second])
    assert first.urn != second.urn
    result = handle(conn, 'get_document', {'urn': 'urn:sg:pdpc:2026_SGPDPC_1'})
    assert result['error'] == 'ambiguous_publication'
    assert len(result['publications']) == 2
    individual = handle(conn, 'get_document', {'urn': first.urn, 'parts': 'all'})
    assert individual['text'] == first.text
    hits = handle(conn, 'pdpc_decisions', {'obligation': ['Accountability'], 'penalty_max': 0})
    assert len(hits['decisions']) == 2
    assert all(d['source_url'] for d in hits['decisions'])
    conn.close()
