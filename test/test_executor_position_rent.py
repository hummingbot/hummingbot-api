"""An LP executor's rent must reach the position table (GW-41).

`gateway_clmm_positions.position_rent` is written by hummingbot-api's OPEN route and
`position_rent_refunded` by its CLOSE route. An executor holds its position through the
wheel, talking to Gateway directly, so neither route runs: the poller discovers the
position and files it with both columns NULL, and the close leaves no refund behind
either. Live table before the fix:

    position     events                                  rent        refunded
    B7nHjtVByQ   DISCOVERED                              NULL        NULL      <- executor
    4G5GyCPi9U   CLOSE,COLLECT_FEES,DISCOVERED           NULL        0.0100572
    9RdCMFFvFU   ADD_LIQUIDITY,CLOSE,COLLECT_FEES,OPEN   0.0100572   0.0100572 <- routes

So the table answered a rent question correctly for the hand-driven path and not at all
for the recommended one, on a figure (~0.0100572 SOL per Orca position) that is larger
than the liquidity in a small position.
"""
from contextlib import asynccontextmanager
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytest.importorskip("hummingbot")

from database.repositories.gateway_clmm_repository import GatewayCLMMRepository  # noqa: E402
from services.executor_service import ExecutorService  # noqa: E402

ORCA_RENT = Decimal("0.0100572")


# --------------------------------------------------------------------------------------
# The repository: fill a NULL, never overwrite a measurement
# --------------------------------------------------------------------------------------

class _Session:
    """Just enough AsyncSession for record_position_rent: one lookup and a flush."""

    def __init__(self, position):
        self._position = position
        self.flushed = False

    async def execute(self, _statement):
        return SimpleNamespace(scalar_one_or_none=lambda: self._position)

    async def flush(self):
        self.flushed = True


def _position(rent=None, refunded=None):
    return SimpleNamespace(position_rent=rent, position_rent_refunded=refunded)


@pytest.mark.asyncio
async def test_it_fills_both_columns_when_the_row_has_neither():
    """The executor-lifecycle row: DISCOVERED, both columns NULL."""
    position = _position()
    repo = GatewayCLMMRepository(_Session(position))

    await repo.record_position_rent("B7nHjtVByQ", position_rent=ORCA_RENT, position_rent_refunded=ORCA_RENT)

    assert position.position_rent == ORCA_RENT
    assert position.position_rent_refunded == ORCA_RENT


@pytest.mark.asyncio
async def test_it_does_not_overwrite_a_figure_the_routes_already_recorded():
    """A route read its figure off the transaction that produced it. That one wins.

    Not a stylistic preference: it also makes the call idempotent, which matters because
    the control loop can reach the same position more than once.
    """
    from_the_transaction = Decimal("0.00337584")
    position = _position(rent=from_the_transaction, refunded=from_the_transaction)
    repo = GatewayCLMMRepository(_Session(position))

    await repo.record_position_rent("9RdCMFFvFU", position_rent=ORCA_RENT, position_rent_refunded=ORCA_RENT)

    assert position.position_rent == from_the_transaction
    assert position.position_rent_refunded == from_the_transaction


@pytest.mark.asyncio
async def test_it_fills_the_missing_half_of_a_partly_route_driven_position():
    """4G5GyCPi9U: discovered, then closed through the route. Refund known, rent not."""
    position = _position(refunded=ORCA_RENT)
    repo = GatewayCLMMRepository(_Session(position))

    await repo.record_position_rent("4G5GyCPi9U", position_rent=ORCA_RENT)

    assert position.position_rent == ORCA_RENT
    assert position.position_rent_refunded == ORCA_RENT


@pytest.mark.asyncio
async def test_a_row_that_does_not_exist_yet_is_not_an_error():
    """Discovery runs on its own schedule; the position may simply not be filed yet."""
    repo = GatewayCLMMRepository(_Session(None))

    assert await repo.record_position_rent("nothere", position_rent=ORCA_RENT) is None


# --------------------------------------------------------------------------------------
# The zero trap
# --------------------------------------------------------------------------------------

@pytest.mark.parametrize("value", [0, 0.0, "0", Decimal("0"), None, "", "not-a-number"])
def test_an_unmeasured_figure_is_never_stored(value):
    """The LP executor defaults both to 0.0, so zero means "never measured" far more
    often than "measured and empty" — a position still open has no refund yet, and an EVM
    CLMM has no rent at all. Storing the 0.0 is precisely GW-18's defect: a hardcoded zero
    is worse than a NULL, because nothing downstream can tell it from an observation.
    """
    assert ExecutorService._measured_rent({"position_rent": value}, "position_rent") is None


def test_a_real_reading_survives_the_float_it_arrives_as():
    assert ExecutorService._measured_rent({"position_rent": 0.0100572}, "position_rent") == ORCA_RENT


# --------------------------------------------------------------------------------------
# The service: getting the figures from a live executor to the row
# --------------------------------------------------------------------------------------

class _RecordingRepo:
    """Captures record_position_rent calls; stands in for the whole DB layer."""

    def __init__(self, found=True):
        self.calls = []
        self._found = found

    async def record_position_rent(self, position_address, position_rent=None, position_rent_refunded=None):
        self.calls.append((position_address, position_rent, position_rent_refunded))
        return object() if self._found else None


def _service(repo):
    service = ExecutorService.__new__(ExecutorService)
    service._lp_position_addresses = {}
    service._lp_rent_recorded = set()
    service._lp_rent_retry_after = {}

    db_manager = MagicMock()

    @asynccontextmanager
    async def session_context():
        yield MagicMock()

    db_manager.get_session_context = session_context
    service.db_manager = db_manager

    import services.executor_service as module
    module.GatewayCLMMRepository = lambda _session: repo
    return service


@pytest.fixture(autouse=True)
def _restore_repository():
    import services.executor_service as module
    original = module.GatewayCLMMRepository
    yield
    module.GatewayCLMMRepository = original


def _executor(**custom_info):
    executor = MagicMock()
    executor.get_custom_info = MagicMock(return_value=custom_info)
    return executor


@pytest.mark.asyncio
async def test_a_live_executors_locked_rent_reaches_the_row():
    repo = _RecordingRepo()
    service = _service(repo)

    await service._record_lp_position_rent(
        "e-1", _executor(position_address="B7nHjtVByQ", position_rent=0.0100572, position_rent_refunded=0.0)
    )

    assert repo.calls == [("B7nHjtVByQ", ORCA_RENT, None)]
    # Recorded, so the control loop stops asking.
    assert "e-1" in service._lp_rent_recorded


@pytest.mark.asyncio
async def test_the_refund_is_filed_under_the_address_the_close_cleared():
    """A successful close sets position_address to None BEFORE the executor terminates,
    and the refund is only known at that point. Without the address remembered while the
    executor was live there is nothing to file the refund under — which is the second
    half of GW-41, not a detail.
    """
    repo = _RecordingRepo()
    service = _service(repo)

    live = _executor(position_address="B7nHjtVByQ", position_rent=0.0100572, position_rent_refunded=0.0)
    await service._record_lp_position_rent("e-1", live)

    closed = _executor(position_address=None, position_rent=0.0100572, position_rent_refunded=0.0100572)
    await service._record_lp_position_rent("e-1", closed)

    assert repo.calls[-1] == ("B7nHjtVByQ", ORCA_RENT, ORCA_RENT)


@pytest.mark.asyncio
async def test_an_executor_that_never_opened_writes_nothing():
    repo = _RecordingRepo()
    service = _service(repo)

    await service._record_lp_position_rent(
        "e-1", _executor(position_address=None, position_rent=0.0, position_rent_refunded=0.0)
    )

    assert repo.calls == []


@pytest.mark.asyncio
async def test_an_evm_position_with_no_rent_concept_writes_nothing():
    """uniswap has a position and no rent. NULL is the right answer, not 0.0."""
    repo = _RecordingRepo()
    service = _service(repo)

    await service._record_lp_position_rent(
        "e-1", _executor(position_address="0xabc", position_rent=0.0, position_rent_refunded=0.0)
    )

    assert repo.calls == []


@pytest.mark.asyncio
async def test_a_missing_row_backs_off_instead_of_querying_every_tick():
    """The control loop ticks at 1 Hz and discovery files the row about once a minute."""
    repo = _RecordingRepo(found=False)
    service = _service(repo)

    await service._record_lp_position_rent(
        "e-1", _executor(position_address="B7nHjtVByQ", position_rent=0.0100572)
    )

    assert repo.calls == [("B7nHjtVByQ", ORCA_RENT, None)]
    assert "e-1" not in service._lp_rent_recorded
    assert service._lp_rent_retry_after["e-1"] > 0


@pytest.mark.asyncio
async def test_a_refund_with_nowhere_to_go_is_reported(caplog):
    """A position the poller never discovered: the refund is final and unrecordable, so
    it has to be said out loud rather than dropped.
    """
    repo = _RecordingRepo(found=False)
    service = _service(repo)
    service._lp_position_addresses["e-1"] = "B7nHjtVByQ"

    with caplog.at_level("WARNING"):
        await service._record_lp_position_rent(
            "e-1", _executor(position_address=None, position_rent_refunded=0.0100572)
        )

    assert "B7nHjtVByQ" in caplog.text
    assert "0.0100572" in caplog.text


# --------------------------------------------------------------------------------------
# The wiring: the control loop is what makes any of the above run
# --------------------------------------------------------------------------------------

async def _one_control_loop_tick(service):
    """Run the loop body exactly once."""
    service._is_running = True

    def stop_after_this_tick():
        service._is_running = False

    service._trading_service = MagicMock()
    service._trading_service.update_all_timestamps = MagicMock(side_effect=stop_after_this_tick)
    service.update_interval = 0
    await service._control_loop()


@pytest.mark.asyncio
async def test_the_control_loop_records_a_live_lp_executor():
    repo = _RecordingRepo()
    service = _service(repo)
    service._active_executors = {
        "e-1": _executor(position_address="B7nHjtVByQ", position_rent=0.0100572)
    }
    service._active_executors["e-1"].is_closed = False
    service._executor_metadata = {"e-1": {"executor_type": "lp_executor"}}

    await _one_control_loop_tick(service)

    assert repo.calls == [("B7nHjtVByQ", ORCA_RENT, None)]


@pytest.mark.asyncio
async def test_the_control_loop_leaves_other_executor_types_alone():
    """Only lp_executor owns an on-chain position account with rent locked in it."""
    repo = _RecordingRepo()
    service = _service(repo)
    service._active_executors = {"e-1": _executor(position_address="B7nHjtVByQ", position_rent=0.0100572)}
    service._active_executors["e-1"].is_closed = False
    service._executor_metadata = {"e-1": {"executor_type": "position_executor"}}

    await _one_control_loop_tick(service)

    assert repo.calls == []


@pytest.mark.asyncio
async def test_the_control_loop_stops_asking_once_the_rent_is_stored():
    """Otherwise this is a database round trip per executor per tick, forever."""
    repo = _RecordingRepo()
    service = _service(repo)
    service._active_executors = {"e-1": _executor(position_address="B7nHjtVByQ", position_rent=0.0100572)}
    service._active_executors["e-1"].is_closed = False
    service._executor_metadata = {"e-1": {"executor_type": "lp_executor"}}

    await _one_control_loop_tick(service)
    await _one_control_loop_tick(service)

    assert len(repo.calls) == 1


@pytest.mark.asyncio
async def test_completion_records_the_refund_the_close_produced():
    """The refund exists only after the close confirms, which is after the last control
    loop tick that could see it — so completion has to record it, or the second half of
    GW-41 stays open even with the first half fixed.
    """
    from unittest.mock import AsyncMock

    repo = _RecordingRepo()
    service = _service(repo)
    service._lp_position_addresses["e-1"] = "B7nHjtVByQ"

    # A close that succeeded: position_address cleared, refund known.
    executor = _executor(position_address=None, position_rent=0.0100572, position_rent_refunded=0.0100572)
    executor.close_type = None
    service._active_executors = {"e-1": executor}
    service._executor_metadata = {"e-1": {"executor_type": "lp_executor"}}
    service._persist_executor_completed = AsyncMock()
    service._log_capture = MagicMock()

    await service._handle_executor_completion("e-1")

    assert repo.calls == [("B7nHjtVByQ", ORCA_RENT, ORCA_RENT)]
    # And the per-executor bookkeeping does not outlive the executor.
    assert "e-1" not in service._lp_position_addresses
    assert "e-1" not in service._lp_rent_retry_after


@pytest.mark.asyncio
async def test_the_loop_survives_an_executor_appearing_while_it_awaits(caplog):
    """Recording rent awaits a database round trip, and create_executor runs in a request
    task that can add to _active_executors while the loop is suspended. Iterating the live
    dict raises "dictionary changed size during iteration" — and the loop's own broad
    except swallows it into a log line, so the tick reports success while having skipped
    completion handling for every executor after the one that raced.
    """
    repo = _RecordingRepo()
    service = _service(repo)

    live = _executor(position_address="B7nHjtVByQ", position_rent=0.0100572)
    live.is_closed = False
    # A second executor so the iterator has to advance after the racing await.
    other = _executor()
    other.is_closed = False
    service._active_executors = {"e-1": live, "e-2": other}
    service._executor_metadata = {
        "e-1": {"executor_type": "lp_executor"},
        "e-2": {"executor_type": "position_executor"},
    }

    async def record_and_race(*_args):
        service._active_executors["e-3"] = _executor()
        service._lp_rent_recorded.add("e-1")

    service._record_lp_position_rent = record_and_race

    with caplog.at_level("ERROR"):
        await _one_control_loop_tick(service)

    assert "changed size during iteration" not in caplog.text
