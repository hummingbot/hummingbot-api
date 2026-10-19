"""A pair whose first fills are sells must not 500 /archived-bots/{db}/performance.

The reader keeps the running average buy price per pair, and stands in NA for it
where nothing has been bought yet so it never divides by zero. Standing in
pd.NA makes the column object dtype, and the realized-PnL ratio then divides on
Python floats: a pair that opens with a sell (a short, a perpetual) has an
average buy price of 0 on its first rows and raised "float division by zero".
Spot bots that buy first never hit it.

Run with: pytest test/test_archived_performance_sell_first.py -v
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


@pytest.fixture
def short_first_db(tmp_path):
    """Sold 4 at 2.00, then bought 1 back at 1.50: three are left short."""
    path = tmp_path / "short_first.sqlite"
    connection = sqlite3.connect(path)
    connection.execute(TRADE_FILL_SCHEMA)
    connection.executemany(
        "INSERT INTO TradeFill VALUES ('conf.yml', 'v2', 'gate_io', 'BBB-USDT', 'BBB', 'USDT', ?, ?, ?, "
        "'LIMIT', ?, ?, 1, '{}', 0, ?, 'NIL')",
        [
            (1_000, "o-1", "SELL", 2_000_000, 4_000_000, "t-1"),
            (2_000, "o-2", "BUY", 1_500_000, 1_000_000, "t-2"),
        ],
    )
    connection.commit()
    connection.close()
    return str(path)


def test_a_pair_that_opens_with_a_sell_is_valued_rather_than_raising(short_first_db):
    performance = HummingbotDatabase(short_first_db).calculate_trade_based_performance()

    assert len(performance) == 2
    # Nothing has been bought back yet, so nothing is realized and the short is flat at its own price.
    assert performance["realized_trade_pnl_quote"].iloc[0] == pytest.approx(0.0)
    assert performance["unrealized_trade_pnl_quote"].iloc[0] == pytest.approx(0.0)
    # Bought 1 back at 1.50 against 4 sold at 2.00: (2.00 - 1.50) realized on one,
    # and the 3 still short are 0.50 in profit each at 1.50.
    assert performance["realized_trade_pnl_quote"].iloc[1] == pytest.approx(0.50)
    assert performance["unrealized_trade_pnl_quote"].iloc[1] == pytest.approx(1.50)


@pytest.mark.asyncio
async def test_the_performance_route_answers_a_pair_that_opens_with_a_sell(monkeypatch, short_first_db):
    import routers.archived_bots as archived_bots

    monkeypatch.setattr(archived_bots, "_validate_db_path", lambda db_path: short_first_db)

    response = await archived_bots.get_database_performance("archived/short_first.sqlite")

    assert response["summary"]["final_realized_pnl_quote"] == pytest.approx(0.50)
    assert response["summary"]["final_unrealized_pnl_quote"] == pytest.approx(1.50)
