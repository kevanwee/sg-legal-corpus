from dataclasses import replace

import httpx
import pytest
import respx

from sgcorpus.config import POLICIES, USER_AGENT
from sgcorpus.net.client import Client, RobotsDisallowed


def test_policy_floors_and_source_user_agents() -> None:
    assert POLICIES['sso'].merge(2).min_interval == 6
    assert POLICIES['sso'].merge(10).min_interval == 10
    assert POLICIES['pdpc'].user_agent == USER_AGENT
    assert 'Mozilla' in POLICIES['sso'].user_agent


@respx.mock
def test_robots_counts_and_increases_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    waits: list[float] = []
    monkeypatch.setattr(Client, '_wait', lambda self: waits.append(self.policy.min_interval))
    robots = respx.get('https://sso.agc.gov.sg/robots.txt').mock(
        return_value=httpx.Response(200, text='User-agent: *\nDisallow: /search\nCrawl-delay: 8')
    )
    page = respx.get('https://sso.agc.gov.sg/Act/PDPA2012').mock(
        return_value=httpx.Response(200, text='act')
    )
    with Client(POLICIES['sso']) as client:
        client.get('https://sso.agc.gov.sg/Act/PDPA2012')
        assert client.request_count == 2
        with pytest.raises(RobotsDisallowed):
            client.get('https://sso.agc.gov.sg/search')
    assert waits == [6, 8]
    assert robots.calls[0].request.headers['User-Agent'] == POLICIES['sso'].user_agent
    assert page.call_count == 1


def test_merge_preserves_cap() -> None:
    policy = replace(POLICIES['elitigation'], min_interval=5)
    assert policy.merge(1).daily_cap == 2000
