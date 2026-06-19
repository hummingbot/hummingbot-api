"""
Tests for PortfolioAnalyticsService pure portfolio-distribution math.

Run with: pytest test/test_portfolio_analytics.py -v
"""
import pytest

from services.portfolio_analytics_service import PortfolioAnalyticsService


@pytest.fixture
def analytics():
    return PortfolioAnalyticsService()


@pytest.fixture
def accounts_state():
    """Plain dict fixture shaped like AccountsService.accounts_state."""
    return {
        "master_account": {
            "binance": [
                {"token": "BTC", "units": 0.5, "price": 50000.0, "value": 25000.0, "available_units": 0.5},
                {"token": "USDT", "units": 5000.0, "price": 1.0, "value": 5000.0, "available_units": 5000.0},
            ],
            "kraken": [
                {"token": "BTC", "units": 0.1, "price": 50000.0, "value": 5000.0, "available_units": 0.1},
            ],
        },
        "sub_account": {
            "binance": [
                {"token": "ETH", "units": 5.0, "price": 3000.0, "value": 15000.0, "available_units": 5.0},
            ],
        },
    }


class TestPortfolioDistribution:
    def test_total_value_and_token_count(self, analytics, accounts_state):
        result = analytics.get_portfolio_distribution(accounts_state)

        assert result["total_portfolio_value"] == 50000.0
        assert result["token_count"] == 3
        assert result["account_filter"] == "all_accounts"
        assert "error" not in result

    def test_response_shape(self, analytics, accounts_state):
        result = analytics.get_portfolio_distribution(accounts_state)

        assert set(result.keys()) == {"total_portfolio_value", "token_count", "distribution", "account_filter"}
        token_dist = result["distribution"][0]
        assert set(token_dist.keys()) == {"token", "total_value", "total_units", "percentage", "accounts"}
        account_entry = next(iter(token_dist["accounts"].values()))
        assert set(account_entry.keys()) == {"value", "units", "percentage", "connectors"}
        connector_entry = next(iter(account_entry["connectors"].values()))
        assert set(connector_entry.keys()) == {"value", "units"}

    def test_token_percentages(self, analytics, accounts_state):
        result = analytics.get_portfolio_distribution(accounts_state)
        by_token = {d["token"]: d for d in result["distribution"]}

        # BTC: 25000 (binance) + 5000 (kraken) = 30000 -> 60%
        assert by_token["BTC"]["total_value"] == 30000.0
        assert by_token["BTC"]["total_units"] == 0.6
        assert by_token["BTC"]["percentage"] == 60.0
        # ETH: 15000 -> 30%
        assert by_token["ETH"]["percentage"] == 30.0
        # USDT: 5000 -> 10%
        assert by_token["USDT"]["percentage"] == 10.0

    def test_account_and_connector_breakdown(self, analytics, accounts_state):
        result = analytics.get_portfolio_distribution(accounts_state)
        btc = next(d for d in result["distribution"] if d["token"] == "BTC")

        master = btc["accounts"]["master_account"]
        assert master["value"] == 30000.0
        assert master["units"] == 0.6
        assert master["percentage"] == 60.0
        assert master["connectors"]["binance"] == {"value": 25000.0, "units": 0.5}
        assert master["connectors"]["kraken"] == {"value": 5000.0, "units": 0.1}

    def test_sorted_by_value_descending(self, analytics, accounts_state):
        result = analytics.get_portfolio_distribution(accounts_state)
        values = [d["total_value"] for d in result["distribution"]]

        assert values == sorted(values, reverse=True)
        assert [d["token"] for d in result["distribution"]] == ["BTC", "ETH", "USDT"]

    def test_account_filter(self, analytics, accounts_state):
        result = analytics.get_portfolio_distribution(accounts_state, "sub_account")

        assert result["account_filter"] == "sub_account"
        assert result["total_portfolio_value"] == 15000.0
        assert result["token_count"] == 1
        assert result["distribution"][0]["token"] == "ETH"
        assert result["distribution"][0]["percentage"] == 100.0

    def test_unknown_account_filter_returns_empty(self, analytics, accounts_state):
        result = analytics.get_portfolio_distribution(accounts_state, "missing_account")

        assert result["total_portfolio_value"] == 0
        assert result["token_count"] == 0
        assert result["distribution"] == []
        assert result["account_filter"] == "missing_account"

    def test_empty_state(self, analytics):
        result = analytics.get_portfolio_distribution({})

        assert result["total_portfolio_value"] == 0
        assert result["token_count"] == 0
        assert result["distribution"] == []
        assert "error" not in result

    def test_zero_total_value_has_zero_percentages(self, analytics):
        state = {"acc": {"conn": [{"token": "XYZ", "units": 1.0, "price": 0.0, "value": 0.0}]}}
        result = analytics.get_portfolio_distribution(state)

        assert result["total_portfolio_value"] == 0
        assert result["distribution"][0]["percentage"] == 0

    def test_error_path_returns_error_shape(self, analytics):
        result = analytics.get_portfolio_distribution(None)

        assert result["total_portfolio_value"] == 0
        assert result["token_count"] == 0
        assert result["distribution"] == []
        assert result["account_filter"] == "all_accounts"
        assert "error" in result


class TestAccountDistribution:
    def test_totals_and_percentages(self, analytics, accounts_state):
        result = analytics.get_account_distribution(accounts_state)

        assert result["total_portfolio_value"] == 50000.0
        assert result["account_count"] == 2
        by_account = {d["account"]: d for d in result["distribution"]}
        assert by_account["master_account"]["total_value"] == 35000.0
        assert by_account["master_account"]["percentage"] == 70.0
        assert by_account["sub_account"]["total_value"] == 15000.0
        assert by_account["sub_account"]["percentage"] == 30.0

    def test_connector_percentages_relative_to_total(self, analytics, accounts_state):
        result = analytics.get_account_distribution(accounts_state)
        master = next(d for d in result["distribution"] if d["account"] == "master_account")

        assert master["connectors"]["binance"] == {"value": 30000.0, "percentage": 60.0}
        assert master["connectors"]["kraken"] == {"value": 5000.0, "percentage": 10.0}

    def test_response_shape(self, analytics, accounts_state):
        result = analytics.get_account_distribution(accounts_state)

        assert set(result.keys()) == {"total_portfolio_value", "account_count", "distribution"}
        entry = result["distribution"][0]
        assert set(entry.keys()) == {"account", "total_value", "percentage", "connectors"}
        connector_entry = next(iter(entry["connectors"].values()))
        assert set(connector_entry.keys()) == {"value", "percentage"}

    def test_sorted_by_value_descending(self, analytics, accounts_state):
        result = analytics.get_account_distribution(accounts_state)

        assert [d["account"] for d in result["distribution"]] == ["master_account", "sub_account"]

    def test_empty_state(self, analytics):
        result = analytics.get_account_distribution({})

        assert result == {"total_portfolio_value": 0, "account_count": 0, "distribution": []}

    def test_error_path_returns_error_shape(self, analytics):
        result = analytics.get_account_distribution(None)

        assert result["total_portfolio_value"] == 0
        assert result["account_count"] == 0
        assert result["distribution"] == []
        assert "error" in result
