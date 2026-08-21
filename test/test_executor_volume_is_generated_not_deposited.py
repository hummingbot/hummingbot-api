"""Volume is what an executor traded, not what it deposited.

`ExecutorRecord.filled_amount_quote` was summed as `volume_total_quote`. For every
executor that places orders that is the same number — the amount it filled IS the volume.
For an LP executor it is not: its filled amount is the capital it put up, and putting up
capital trades nothing. A position that deposited $100 and never saw a swap reported $100
of volume, and the round trip in and back out read as more.

The volume an LP position DOES generate is derived in the wheel, from the fees it earned
(fees are a fixed fraction of the flow that paid them). This side's job is to store that
figure and aggregate it, rather than reaching for the deposit.
"""
import inspect
import re
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

pytest.importorskip("hummingbot")

from database.connection import AsyncDatabaseManager  # noqa: E402
from database.models import ExecutorRecord  # noqa: E402
from database.repositories.executor_repository import ExecutorRepository  # noqa: E402
from services.executor_service import ExecutorService  # noqa: E402


def test_the_record_has_a_column_for_volume_separate_from_filled_amount():
    columns = {c.name for c in ExecutorRecord.__table__.columns}

    assert "volume_traded_quote" in columns
    # Both, not one renamed into the other: capital deployed is still a fact worth having.
    assert "filled_amount_quote" in columns


def test_every_aggregate_sums_volume_rather_than_the_filled_amount():
    """Three places summed the wrong column; a fourth added later would too."""
    source = inspect.getsource(ExecutorRepository.get_performance_report)
    summed = set(re.findall(r"func\.sum\(ExecutorRecord\.(\w+)\)", source))

    assert "volume_traded_quote" in summed
    assert "filled_amount_quote" not in summed, (
        "an aggregate is still summing the capital deployed and calling it volume"
    )


def _service_with(executor_info_fields):
    """An ExecutorService wired to one fake executor, with nothing else running."""
    service = ExecutorService.__new__(ExecutorService)
    service._executor_metadata = {"e-1": {"executor_type": "lp_executor"}}
    service._log_capture = MagicMock()
    service._log_capture.get_error_count.return_value = 0
    service._log_capture.get_last_error.return_value = None

    executor = MagicMock()
    info = MagicMock()
    dumped = {"custom_info": {}, **executor_info_fields}
    info.model_dump.return_value = dumped
    info.side = None
    executor.executor_info = info
    executor.status.name = "TERMINATED"
    executor.close_type = None
    executor.is_closed = True
    service._active_executors = {"e-1": executor}
    return service


def test_the_active_summary_counts_volume_generated_not_capital_deposited():
    """An LP position holding $200 of capital that has traded $2,500 through its range."""
    service = _service_with({"filled_amount_quote": 200.0, "volume_traded_quote": 2500.0})

    summary = service.get_summary()

    assert summary["total_volume_quote"] == 2500.0


def test_a_funded_position_that_traded_nothing_summarises_as_no_volume():
    service = _service_with({"filled_amount_quote": 200.0, "volume_traded_quote": 0.0})

    summary = service.get_summary()

    assert summary["total_volume_quote"] == 0.0


@pytest.mark.asyncio
async def test_completion_persists_the_volume_the_executor_reported():
    """The figure the wheel derived has to reach the row it is later summed from."""
    from contextlib import asynccontextmanager

    recorded = {}

    class _Repo:
        def __init__(self, _session):
            pass

        async def update_executor(self, **kwargs):
            recorded.update(kwargs)

    service = ExecutorService.__new__(ExecutorService)
    service._executor_metadata = {"e-1": {"executor_type": "lp_executor"}}
    service._log_capture = MagicMock()
    service._log_capture.get_error_count.return_value = 0

    db_manager = MagicMock()

    @asynccontextmanager
    async def session_context():
        yield MagicMock()

    db_manager.get_session_context = session_context
    service.db_manager = db_manager

    executor = MagicMock()
    executor.status.name = "TERMINATED"
    executor.close_type = None
    executor.get_custom_info.return_value = {}
    info = MagicMock()
    info.net_pnl_quote = Decimal("3")
    info.net_pnl_pct = Decimal("0.01")
    info.cum_fees_quote = Decimal("1")
    info.filled_amount_quote = Decimal("200")
    info.volume_traded_quote = Decimal("2500")
    executor.executor_info = info

    import services.executor_service as module
    original = module.ExecutorRepository
    module.ExecutorRepository = _Repo
    try:
        await service._persist_executor_completed("e-1", executor)
    finally:
        module.ExecutorRepository = original

    assert recorded["volume_traded_quote"] == Decimal("2500")
    # Capital deployed still stored, under its own name.
    assert recorded["filled_amount_quote"] == Decimal("200")


def test_update_executor_accepts_it():
    parameters = inspect.signature(ExecutorRepository.update_executor).parameters

    assert "volume_traded_quote" in parameters


class TestTheMigration:
    """create_all only creates MISSING tables, so an existing database gains a column
    only through the migration list. Without the entry the column exists in the model,
    every write names it, and every one of them fails against a real deployment."""

    def _entry(self):
        source = inspect.getsource(AsyncDatabaseManager._run_migrations)
        match = re.search(
            r'\(\s*"executors",\s*"volume_traded_quote",\s*\((.*?)\),\s*\),', source, re.DOTALL
        )
        assert match, "no migration adds volume_traded_quote to executors"
        return match.group(1)

    def test_it_adds_the_column(self):
        assert "ALTER TABLE executors ADD COLUMN volume_traded_quote" in self._entry()

    def test_it_backfills_the_executors_whose_filled_amount_was_their_volume(self):
        """For an order-placing executor the two are the same number by definition, so
        history stays intact rather than resetting to zero."""
        entry = self._entry()

        assert "UPDATE executors SET volume_traded_quote = filled_amount_quote" in entry

    def test_it_leaves_lp_rows_at_zero_rather_than_backfilling_the_deposit(self):
        """The one thing the backfill must NOT do. A historical LP position's real volume
        is unrecoverable — its fees were never stored — and copying the deposit across
        would re-enter exactly the number this change exists to remove, now looking
        migrated and deliberate."""
        entry = self._entry()

        assert "executor_type <> 'lp_executor'" in entry

    def test_a_multi_statement_migration_runs_every_statement(self):
        source = inspect.getsource(AsyncDatabaseManager._run_migrations)

        assert "for statement in ((sql,) if isinstance(sql, str) else sql)" in source, (
            "the runner executes a single string, so the backfill beside the ALTER never runs"
        )


def test_a_completed_executors_api_row_carries_the_volume():
    """What the API returns for a completed executor, read back from its row."""
    service = ExecutorService.__new__(ExecutorService)

    record = MagicMock(
        executor_id="e-1", executor_type="lp_executor", account_name="master_account",
        connector_name="solana-mainnet-beta", trading_pair="SOL-USDC", status="TERMINATED",
        close_type="EARLY_STOP", controller_id="main", error_log=None, config=None,
        final_state=None, created_at=None, closed_at=None,
        net_pnl_quote=Decimal("3"), net_pnl_pct=Decimal("0.01"), cum_fees_quote=Decimal("1"),
        filled_amount_quote=Decimal("200"), volume_traded_quote=Decimal("2500"),
    )

    row = service._format_db_record(record)

    assert row["volume_traded_quote"] == 2500.0
    assert row["filled_amount_quote"] == 200.0


def test_decimal_precision_matches_the_filled_amount_column():
    """Same scale, because they measure the same kind of quantity."""
    volume = ExecutorRecord.__table__.columns["volume_traded_quote"].type
    filled = ExecutorRecord.__table__.columns["filled_amount_quote"].type

    assert (volume.precision, volume.scale) == (filled.precision, filled.scale)
    assert Decimal(10) ** -volume.scale > 0
