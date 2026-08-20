"""Test ip_location: ip2region → (country, region), never fabricates defaults."""
from __future__ import annotations

from geo_guard import ip_location


class FakeSearcher:
    def __init__(self, result: str):
        self._result = result

    def search(self, ip: str) -> str:
        return self._result


def test_china_ip_returns_province(monkeypatch):
    monkeypatch.setattr("geo_guard._get_searcher", lambda: FakeSearcher("中国|0|广东|深圳|电信"))
    assert ip_location("113.108.11.9") == ("中国", "广东")


def test_china_ip_without_region_returns_country(monkeypatch):
    monkeypatch.setattr("geo_guard._get_searcher", lambda: FakeSearcher("中国|0|0|0|0"))
    assert ip_location("113.108.11.9") == ("中国", None)


def test_foreign_ip_returns_country(monkeypatch):
    monkeypatch.setattr("geo_guard._get_searcher", lambda: FakeSearcher("美国|0|0|0|0"))
    assert ip_location("8.8.8.8") == ("美国", None)


def test_private_ip_short_circuits_without_searcher(monkeypatch):
    called = {"hit": False}

    def fake_searcher():
        called["hit"] = True
        return FakeSearcher("中国|0|广东|深圳|电信")

    monkeypatch.setattr("geo_guard._get_searcher", fake_searcher)
    assert ip_location("192.168.1.10") == (None, None)
    assert called["hit"] is False


def test_empty_or_none_ip_returns_none():
    assert ip_location("") == (None, None)
    assert ip_location(None) == (None, None)


def test_searcher_missing_returns_none(monkeypatch):
    monkeypatch.setattr("geo_guard._get_searcher", lambda: None)
    assert ip_location("8.8.8.8") == (None, None)


def test_lookup_empty_result_returns_none(monkeypatch):
    monkeypatch.setattr("geo_guard._get_searcher", lambda: FakeSearcher(""))
    assert ip_location("8.8.8.8") == (None, None)


def test_lookup_unknown_country_returns_none(monkeypatch):
    monkeypatch.setattr("geo_guard._get_searcher", lambda: FakeSearcher("0|0|0|内网IP|内网IP"))
    assert ip_location("169.254.1.1") == (None, None)


def test_lookup_exception_returns_none(monkeypatch):
    class Boom:
        def search(self, ip: str) -> str:
            raise RuntimeError("boom")

    monkeypatch.setattr("geo_guard._get_searcher", lambda: Boom())
    assert ip_location("8.8.8.8") == (None, None)
