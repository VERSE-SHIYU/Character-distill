"""API test: GET /api/market/location returns requester IP geolocation.

- public China IP → 200 {country, region}
- public foreign IP → 200 {country, region: None}
- private IP → 204 (无数据语义，不伪造默认值)
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers.market import router as market_router


class FakeSearcher:
    def __init__(self, result: str):
        self._result = result

    def search(self, ip: str) -> str:
        return self._result


def _client(monkeypatch, result: str):
    monkeypatch.setattr("geo_guard._get_searcher", lambda: FakeSearcher(result))
    app = FastAPI()
    app.include_router(market_router)
    return TestClient(app)


def test_location_returns_province_for_china_ip(monkeypatch):
    client = _client(monkeypatch, "中国|0|广东|深圳|电信")
    r = client.get("/api/market/location", headers={"X-Forwarded-For": "113.108.11.9"})
    assert r.status_code == 200
    assert r.json() == {"country": "中国", "region": "广东"}


def test_location_returns_country_for_foreign_ip(monkeypatch):
    client = _client(monkeypatch, "美国|0|0|0|0")
    r = client.get("/api/market/location", headers={"X-Forwarded-For": "8.8.8.8"})
    assert r.status_code == 200
    assert r.json() == {"country": "美国", "region": None}


def test_location_returns_204_for_private_ip(monkeypatch):
    client = _client(monkeypatch, "中国|0|广东|深圳|电信")
    r = client.get("/api/market/location", headers={"X-Forwarded-For": "192.168.1.10"})
    assert r.status_code == 204
