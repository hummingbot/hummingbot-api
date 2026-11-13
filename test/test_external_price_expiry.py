"""
Regression tests for CORR-120: externally pushed Gateway prices never expired and
permanently overrode live exchange tickers.

`set_price` used to write `_external_prices` with no timestamp, and `_rebuild_price_pool`
layered it over fresh ticker data last, so one Gateway balance lookup pinned a pair's price
for the process lifetime. These tests pin the age bound now applied to external prices,
mirroring how `_is_ticker_fresh` is already tested for tickers.

Run with: pytest test/test_external_price_expiry.py -v
"""
from decimal import Decimal

from services.market_data_service import MarketDataService
from services.ticker_sources import Ticker


def make_service(max_age: int = 300) -> MarketDataService:
    return MarketDataService(connector_service=None, external_price_max_age=max_age)


def test_expired_external_price_loses_to_a_fresh_ticker(monkeypatch):
    service = make_service(max_age=60)

    monkeypatch.setattr("services.market_data_service.time.time", lambda: 1_000.0)
    service.set_price("SOL-USDC", Decimal("100"))

    monkeypatch.setattr("services.market_data_service.time.time", lambda: 1_100.0)  # +100s
    service._tickers["binance"] = {"SOL-USDC": Ticker(price=Decimal("200"), timestamp=1_100.0)}
    service._rebuild_price_pool()

    assert service.get_pair_rate("SOL-USDC") == Decimal("200")


def test_fresh_external_price_still_wins_over_a_ticker():
    service = make_service(max_age=300)

    service.set_price("SOL-USDC", Decimal("100"))
    service._tickers["binance"] = {"SOL-USDC": Ticker(price=Decimal("200"), timestamp=0.0)}
    service._rebuild_price_pool()

    assert service.get_pair_rate("SOL-USDC") == Decimal("100")


def test_expired_external_price_is_dropped_from_the_pool(monkeypatch):
    service = make_service(max_age=60)

    monkeypatch.setattr("services.market_data_service.time.time", lambda: 1_000.0)
    service.set_price("SOL-USDC", Decimal("100"))
    assert "SOL-USDC" in service._external_prices

    monkeypatch.setattr("services.market_data_service.time.time", lambda: 1_100.0)  # +100s
    service._rebuild_price_pool()

    assert "SOL-USDC" not in service._external_prices
    assert "SOL-USDC" not in service._external_updated_at


def test_expired_external_price_with_no_ticker_resolves_to_none(monkeypatch):
    service = make_service(max_age=60)

    monkeypatch.setattr("services.market_data_service.time.time", lambda: 1_000.0)
    service.set_price("SOL-USDC", Decimal("100"))

    monkeypatch.setattr("services.market_data_service.time.time", lambda: 1_100.0)  # +100s
    service._rebuild_price_pool()

    assert service.get_pair_rate("SOL-USDC") is None
