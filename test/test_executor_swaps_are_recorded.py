"""A swap an executor made must reach gateway_swaps (GW-42).

`gateway_swaps` is written by hummingbot-api's own /gateway/swap/* routes. An executor
holds its connector through the wheel and talks to Gateway directly, so hummingbot-api
never saw the call and had nothing to record. Two MARKET swaps ran through order_executor
on 2026-08-21 at 00:13 and 00:15 UTC, both CONFIRMED on chain and both reconciling exactly
against the wallet, and neither is in the table:

    newest row in gateway_swaps   2026-08-20T18:37:24   (a hand-driven DOGE-1 sell)
    executor swaps                2026-08-21T00:13, 00:15
    SOL-USDC rows                 9, newest 2026-08-20T01:35 -- all hand-driven

The table was not wrong, it was silently partial. POST /gateway/swaps/search and the swap
summary described only swaps made by hand, with no marker saying so, so a caller reading
"9 SOL-USDC swaps" had no way to learn the recent ones were missing.
"""
from contextlib import asynccontextmanager
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("hummingbot")

from services.executor_service import ExecutorService  # noqa: E402

SIGNATURE = "5xLmQ5s5xZ9jTqk3Y8bNvW2pR7cH4dF6gJ1kM3nP9qS8tU4vX6yZ2aB5cD7eF9gH1jK3lM5nP7qR9sT"

# The BUY leg of the live 2026-08-21 round trip: 0.010000000 SOL in for 0.878444 USDC.
A_LIVE_BUY = {
    "transaction_hash": SIGNATURE,
    "swap_provider": "jupiter/router",
    "wallet_address": "82SggYRE2Vo4jN4a2pk3aQ4SET4ctafZJGbowmCqyHx5",
    "side": "BUY",
    "executed_amount_base": Decimal("0.01"),
    "average_executed_price": Decimal("87.8444"),
    "slippage_pct": Decimal("0.05"),
}


class _Repo:
    def __init__(self, existing=None):
        self.created = []
        self._existing = existing

    async def get_swap_by_tx_hash(self, transaction_hash):
        return self._existing

    async def create_swap(self, swap_data):
        self.created.append(swap_data)
        return MagicMock()


def _service(repo, custom_info, metadata=None):
    service = ExecutorService.__new__(ExecutorService)
    service._executor_metadata = {"e-1": metadata if metadata is not None else {
        "executor_type": "order_executor",
        "connector_name": "solana-mainnet-beta",
        "trading_pair": "SOL-USDC",
    }}

    db_manager = MagicMock()

    @asynccontextmanager
    async def session_context():
        yield MagicMock()

    db_manager.get_session_context = session_context
    service.db_manager = db_manager

    executor = MagicMock()
    executor.get_custom_info.return_value = custom_info

    import services.executor_service as module
    module.GatewaySwapRepository = lambda _session: repo
    return service, executor


@pytest.fixture(autouse=True)
def _restore_repository():
    import services.executor_service as module
    original = module.GatewaySwapRepository
    yield
    module.GatewaySwapRepository = original


@pytest.mark.asyncio
async def test_a_live_buy_is_recorded():
    repo = _Repo()
    service, executor = _service(repo, dict(A_LIVE_BUY))

    await service._record_executor_swap("e-1", executor)

    assert len(repo.created) == 1
    row = repo.created[0]
    assert row["transaction_hash"] == SIGNATURE
    assert row["connector"] == "jupiter"      # bare DEX, as the routes write it
    assert row["network"] == "solana-mainnet-beta"
    assert row["trading_pair"] == "SOL-USDC"
    assert row["side"] == "BUY"
    assert row["status"] == "CONFIRMED"
    assert row["gas_token"] == "SOL"


@pytest.mark.asyncio
async def test_a_buy_spends_quote_to_receive_base():
    """Direction is the thing a swap row gets wrong most easily."""
    repo = _Repo()
    service, executor = _service(repo, dict(A_LIVE_BUY))

    await service._record_executor_swap("e-1", executor)

    row = repo.created[0]
    assert row["output_amount"] == Decimal("0.01")            # base received
    assert row["input_amount"] == Decimal("0.878444")         # quote spent
    assert row["price"] == Decimal("87.8444")


@pytest.mark.asyncio
async def test_a_sell_is_the_mirror_image():
    repo = _Repo()
    service, executor = _service(repo, {**A_LIVE_BUY, "side": "SELL"})

    await service._record_executor_swap("e-1", executor)

    row = repo.created[0]
    assert row["input_amount"] == Decimal("0.01")             # base sent
    assert row["output_amount"] == Decimal("0.878444")        # quote received


@pytest.mark.asyncio
async def test_it_records_the_tolerance_the_swap_actually_used():
    """Not config.slippage_pct: after a widening the two differ, and the one that was
    paid for is the live one."""
    repo = _Repo()
    service, executor = _service(repo, {**A_LIVE_BUY, "slippage_pct": Decimal("1.25")})

    await service._record_executor_swap("e-1", executor)

    assert repo.created[0]["slippage_pct"] == Decimal("1.25")


@pytest.mark.asyncio
async def test_a_side_arriving_as_an_enum_string_still_reads_as_a_side():
    """custom_info carries whatever the executor put there; TradeType renders as
    'TradeType.BUY' rather than 'BUY'."""
    repo = _Repo()
    service, executor = _service(repo, {**A_LIVE_BUY, "side": "TradeType.BUY"})

    await service._record_executor_swap("e-1", executor)

    assert repo.created[0]["side"] == "BUY"


@pytest.mark.asyncio
async def test_recording_twice_does_not_duplicate_the_row():
    """The hash is unique in the table, so a second write would raise rather than no-op."""
    repo = _Repo(existing=MagicMock())
    service, executor = _service(repo, dict(A_LIVE_BUY))

    await service._record_executor_swap("e-1", executor)

    assert repo.created == []


@pytest.mark.asyncio
async def test_an_executor_that_is_not_a_gateway_swap_records_nothing():
    """A CEX order executor has no transaction hash and no swap provider."""
    repo = _Repo()
    service, executor = _service(repo, {
        "transaction_hash": None, "swap_provider": None, "side": "BUY",
        "executed_amount_base": Decimal("0.01"), "average_executed_price": Decimal("87"),
    })

    await service._record_executor_swap("e-1", executor)

    assert repo.created == []


@pytest.mark.asyncio
async def test_a_swap_with_no_realized_amounts_is_reported_not_recorded(caplog):
    """A row of zeroes would read as a swap that moved nothing, which is not what
    happened — the amounts are simply unknown."""
    repo = _Repo()
    service, executor = _service(repo, {
        **A_LIVE_BUY, "executed_amount_base": Decimal("0"), "average_executed_price": Decimal("0"),
    })

    with caplog.at_level("WARNING"):
        await service._record_executor_swap("e-1", executor)

    assert repo.created == []
    assert SIGNATURE in caplog.text


@pytest.mark.asyncio
async def test_a_missing_pair_is_reported_not_guessed(caplog):
    repo = _Repo()
    service, executor = _service(repo, dict(A_LIVE_BUY), metadata={
        "executor_type": "order_executor", "connector_name": "solana-mainnet-beta",
        "trading_pair": "",
    })

    with caplog.at_level("WARNING"):
        await service._record_executor_swap("e-1", executor)

    assert repo.created == []
    assert SIGNATURE in caplog.text


@pytest.mark.asyncio
async def test_completion_records_the_swap():
    """The wiring, without which none of the above ever runs."""
    repo = _Repo()
    service, executor = _service(repo, dict(A_LIVE_BUY))
    executor.close_type = None
    service._active_executors = {"e-1": executor}
    service._lp_position_addresses = {}
    service._lp_rent_recorded = set()
    service._lp_rent_retry_after = {}
    service._persist_executor_completed = AsyncMock()
    service._log_capture = MagicMock()

    await service._handle_executor_completion("e-1")

    assert len(repo.created) == 1
    assert repo.created[0]["transaction_hash"] == SIGNATURE
