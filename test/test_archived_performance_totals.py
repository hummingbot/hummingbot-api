"""/archived-bots/{db}/performance must total every pair, with every fee.

The summary read the last row of the frame and nothing else. That row belongs to
whichever pair filled last, so a bot trading two pairs reported one pair's
figures as the bot's. Within a pair, a row's net PnL took off only that fill's own
fee rather than the fees paid so far, so a bot that pays fees reported a PnL
better than the cash it actually made. A bot that trades one pair and pays no
fee (a stablecoin volume bot, say) was unaffected, which is how it went unnoticed.

The numbers below are worked by hand from the fills, and cross-checked against
cash flow: sells - buys + open position * last price - fees.

Run with: pytest test/test_archived_performance_totals.py -v
"""
import sqlite3

import pytest

pytest.importorskip("pandas")

from utils.hummingbot_database_reader import HummingbotDatabase  # noqa: E402

TRADE_FILL_SCHEMA = """
CREATE TABLE TradeFill (
    config_file_path VARCHAR, strategy VARCHAR, market VARCHAR, symbol VARCHAR,
    base_asset VARCHAR, quote_asset VARCHAR, timestamp BIGINT, order_id VARCHAR,
    trade_type VARCHAR, order_type VARCHAR, price BIGINT, amount BIGINT,
    leverage INTEGER, trade_fee VARCHAR, trade_fee_in_quote BIGINT,
    exchange_trade_id VARCHAR, position VARCHAR
)
"""

# (timestamp, pair, side, price, amount, fee in quote). The fills interleave, and
# the last one belongs to BBB, so a reader of the last row alone sees BBB only.
FILLS = [
    (1_000, "AAA-USDT", "BUY", 1.00, 10, 0.10),
    (2_000, "BBB-USDT", "SELL", 2.00, 4, 0.08),
    (3_000, "AAA-USDT", "SELL", 1.20, 5, 0.06),
    (4_000, "BBB-USDT", "BUY", 1.50, 1, 0.03),
]

# AAA: bought 10 at 1.00, sold 5 at 1.20, so 5 are left long, marked at 1.20.
#   realized (1.20 - 1.00) * 5 = 1.00, unrealized (1.20 - 1.00) * 5 = 1.00, fees 0.16.
#   Cash check: 6.00 - 10.00 + 5 * 1.20 - 0.16 = 1.84.
# BBB: sold 4 at 2.00, bought 1 at 1.50, so 3 are left short, marked at 1.50.
#   realized (2.00 - 1.50) * 1 = 0.50, unrealized (2.00 - 1.50) * 3 = 1.50, fees 0.11.
#   Cash check: 8.00 - 1.50 - 3 * 1.50 - 0.11 = 1.89.
AAA_NET, BBB_NET = 1.84, 1.89


@pytest.fixture
def two_pair_db(tmp_path):
    path = tmp_path / "two_pair.sqlite"
    connection = sqlite3.connect(path)
    connection.execute(TRADE_FILL_SCHEMA)
    for i, (timestamp, pair, side, price, amount, fee) in enumerate(FILLS):
        connection.execute(
            "INSERT INTO TradeFill VALUES ('conf.yml', 'v2', 'gate_io', ?, ?, 'USDT', ?, ?, ?, 'LIMIT', "
            "?, ?, 1, '{}', ?, ?, 'NIL')",
            (pair, pair.split("-")[0], timestamp, f"o-{i}", side, round(price * 1e6),
             round(amount * 1e6), round(fee * 1e6), f"t-{i}"),
        )
    connection.commit()
    connection.close()
    return str(path)


class TestNetPnlPerPair:
    def test_a_rows_net_pnl_carries_every_fee_paid_so_far(self, two_pair_db):
        performance = HummingbotDatabase(two_pair_db).calculate_trade_based_performance()

        last = performance.groupby("trading_pair").tail(1).set_index("trading_pair")
        assert last.loc["AAA-USDT", "net_pnl_quote"] == pytest.approx(AAA_NET)
        assert last.loc["BBB-USDT", "net_pnl_quote"] == pytest.approx(BBB_NET)


class TestThePerformanceRouteTotalsEveryPair:
    @pytest.fixture(autouse=True)
    def _resolve_to_the_fixture(self, monkeypatch, two_pair_db):
        import routers.archived_bots as archived_bots

        monkeypatch.setattr(archived_bots, "_validate_db_path", lambda db_path: two_pair_db)

    @pytest.mark.asyncio
    async def test_the_summary_adds_the_pairs_up(self):
        from routers.archived_bots import get_database_performance

        summary = (await get_database_performance("archived/two_pair.sqlite"))["summary"]

        assert summary["final_net_pnl_quote"] == pytest.approx(AAA_NET + BBB_NET)
        assert summary["final_realized_pnl_quote"] == pytest.approx(1.00 + 0.50)
        assert summary["final_unrealized_pnl_quote"] == pytest.approx(1.00 + 1.50)
        assert summary["total_fees_quote"] == pytest.approx(0.16 + 0.11)
        # 10 * 1.00 + 5 * 1.20 for AAA, 4 * 2.00 + 1 * 1.50 for BBB.
        assert summary["total_volume_quote"] == pytest.approx(16.0 + 9.5)

    @pytest.mark.asyncio
    async def test_the_summary_matches_cash_flow(self):
        """Independent of the method under test: what the account would show."""
        from routers.archived_bots import get_database_performance

        cash = 0.0
        position = {}
        last_price = {}
        for _, pair, side, price, amount, fee in FILLS:
            sign = 1 if side == "BUY" else -1
            cash -= sign * price * amount + fee
            position[pair] = position.get(pair, 0) + sign * amount
            last_price[pair] = price
        equity_change = cash + sum(position[p] * last_price[p] for p in position)

        summary = (await get_database_performance("archived/two_pair.sqlite"))["summary"]

        assert summary["final_net_pnl_quote"] == pytest.approx(equity_change)

    @pytest.mark.asyncio
    async def test_every_pair_is_listed_with_its_own_figures(self):
        from routers.archived_bots import get_database_performance

        by_pair = {
            row["trading_pair"]: row
            for row in (await get_database_performance("archived/two_pair.sqlite"))["summary"]["by_pair"]
        }

        assert by_pair["AAA-USDT"]["net_pnl_quote"] == pytest.approx(AAA_NET)
        assert by_pair["AAA-USDT"]["net_position"] == pytest.approx(5.0)
        assert by_pair["BBB-USDT"]["net_pnl_quote"] == pytest.approx(BBB_NET)
        assert by_pair["BBB-USDT"]["net_position"] == pytest.approx(-3.0)
