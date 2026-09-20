from __future__ import annotations

from pathlib import Path

import pytest

from sgcorpus.adapters.sso import SsoAdapter, listing_items
from sgcorpus.mcp.tools import handle
from sgcorpus.net.cache import Snapshot, SnapshotStore
from sgcorpus.store import sqlite

FIXTURES = Path(__file__).parent / 'fixtures'


def make_snapshot(tmp_path: Path, version: str, end: str, body: bytes | None = None) -> Snapshot:
    return SnapshotStore(tmp_path).put(
        adapter='sso', url=f'https://sso.agc.gov.sg/Act/PDPA2012/Historical/{version}',
        body=body or (FIXTURES / f'sso_{version}.html').read_bytes(), status=200,
        params={'kind': 'provisions', 'item': {'code': 'PDPA2012', 'corpus': 'act',
                'title': 'Personal Data Protection Act 2012', 'href': '/Act/PDPA2012'},
                'version_start': version, 'version_end': end, 'historical': True,
                'requested_ids': ['pr15-', 'pr16-'], 'all_ids': ['pr15-', 'pr16-'],
                'root_record': True, 'version_seq': 1},
    )


def test_listing_ignores_actions_and_never_guesses_codes() -> None:
    html = '''<table class="browse-list"><tr><td>
    <a class="non-ajax" href="/Act/CoA1967">Companies Act 1967</a>
    <div><a class="non-ajax" href="/Act/CoA1967">Add to My Collections</a></div>
    </td><td><a class="non-ajax" href="/Act/CoA1967?ViewType=Pdf">PDF</a></td></tr></table>'''
    assert listing_items(html) == [{'href': '/Act/CoA1967', 'title': 'Companies Act 1967',
                                    'code': 'CoA1967', 'corpus': 'act'}]


def test_historical_text_and_exclusive_ranges(tmp_path: Path) -> None:
    adapter = SsoAdapter()
    before = list(adapter.parse(make_snapshot(tmp_path, '2021-01-02', '2021-02-01')))
    after = list(adapter.parse(make_snapshot(tmp_path, '2021-02-01', '2021-12-31')))
    conn = sqlite.connect(tmp_path / 'test.db')
    sqlite.init(conn)
    sqlite.insert_documents(conn, [*before, *after])
    old = handle(conn, 'get_provision', {'urn': 'urn:sg:act:PDPA2012:s15', 'as_of': '2021-01-31'})
    new = handle(conn, 'get_provision', {'urn': 'urn:sg:act:PDPA2012:s15', 'as_of': '2021-02-01'})
    assert old['urn'].endswith('@2021-01-02')
    assert new['urn'].endswith('@2021-02-01')
    assert old['text'] != new['text']
    assert 'Without limiting subsection' not in old['text']
    assert 'Without limiting subsection' in new['text']
    assert new['source_url'] and new['provenance']['snapshot_sha256']
    absent = handle(conn, 'get_provision', {'urn': 'urn:sg:act:PDPA2012:s15', 'as_of': '2010-01-01'})
    assert absent['error'] == 'not_in_force'
    assert len(absent['available_ranges']) == 2
    assert 'text' not in absent
    conn.close()


def test_refuses_wrong_historical_page(tmp_path: Path) -> None:
    snap = make_snapshot(tmp_path, '2021-01-02', '2021-02-01',
                         (FIXTURES / 'sso_2021-02-01.html').read_bytes())
    with pytest.raises(ValueError, match='selected version'):
        list(SsoAdapter().parse(snap))


def test_refuses_wrong_range_or_missing_provision(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match='endpoint'):
        list(SsoAdapter().parse(make_snapshot(tmp_path, '2021-01-02', '2021-03-01')))
    body = (FIXTURES / 'sso_2021-01-02.html').read_bytes().replace(b'id="pr16-"', b'id="missing-"')
    with pytest.raises(ValueError, match='absent'):
        list(SsoAdapter().parse(make_snapshot(tmp_path, '2021-01-02', '2021-02-01', body)))


def test_soft_404_is_not_a_statute() -> None:
    with pytest.raises(ValueError, match='browse table'):
        listing_items('<html>Page Not Found</html>')


def test_conflicting_urn_date_is_rejected(tmp_path: Path) -> None:
    conn = sqlite.connect(tmp_path / 'test.db')
    result = handle(conn, 'get_provision', {'urn': 'urn:sg:act:PDPA2012:s15@2021-01-02',
                                          'as_of': '2021-02-01'})
    assert result['error'] == 'invalid_argument'
    conn.close()
