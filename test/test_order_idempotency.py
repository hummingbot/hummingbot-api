"""A retried POST /trading/orders must place exactly one order (FEAT-002).

`place_trade` calls the connector's synchronous `buy`/`sell`, which mints a client_order_id
and returns before the exchange has acknowledged anything -- so the response says nothing
about whether the exchange received the request, and that is exactly the window in which a
client-side timeout fires and a retry lands. Without a caller-supplied key, the retry is
indistinguishable from a second, deliberate order.

An optional `Idempotency-Key` header closes that window: claim it in `request_idempotency`
(a unique column, so the claim's INSERT is the arbiter of a race rather than application
logic) before calling the connector, then replay the stored response on a repeat.

Most of these tests drive the real route against sync SQLite behind an async shim, like
`test_order_history_cursor.py` (aiosqlite is not installed here). The one exception is
`TestConcurrentClaims`: it needs the unique constraint to actually arbitrate a race between
two live database connections, which only a real transactional database proves, so it spins
up a disposable Postgres container and skips itself when Docker is not available.
"""
import asyncio
import json
import secrets
import shutil
import socket
import subprocess
import time
from contextlib import asynccontextmanager, closing
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from database import AsyncDatabaseManager
from database.models import Order, RequestIdempotency
from database.repositories.idempotency_repository import IdempotencyRepository
from deps import get_accounts_service, get_database_manager
from routers import trading

ROUTE = "POST /trading/orders"

ORDER_BODY = {
    "account_name": "master_account",
    "connector_name": "binance",
    "trading_pair": "BTC-USDT",
    "trade_type": "BUY",
    "amount": "1.5",
    "order_type": "LIMIT",
    "price": "20000",
}


# ---------------------------------------------------------------------------------------
# Sync SQLite behind an async shim -- the repository only ever awaits execute/flush/get, so
# this runs the real SQL (including the UNIQUE index) against the real schema.
# ---------------------------------------------------------------------------------------

class _AsyncSessionAdapter:
    def __init__(self, session: Session):
        self._session = session

    async def execute(self, statement):
        return self._session.execute(statement)

    async def flush(self):
        self._session.flush()

    async def get(self, model, pk):
        return self._session.get(model, pk)

    async def commit(self):
        self._session.commit()

    async def rollback(self):
        self._session.rollback()

    async def close(self):
        self._session.close()


class _DbManager:
    """The one surface routers/trading.py and AccountsService need from AsyncDatabaseManager."""

    def __init__(self, engine):
        self._engine = engine

    @asynccontextmanager
    async def get_session_context(self):
        # expire_on_commit=False, matching AsyncDatabaseManager's real session factory: a row
        # returned from one session_context call is read again after that context has closed.
        session = Session(self._engine, expire_on_commit=False)
        adapter = _AsyncSessionAdapter(session)
        try:
            yield adapter
            await adapter.commit()
        except Exception:
            await adapter.rollback()
            raise
        finally:
            await adapter.close()


@pytest.fixture
def engine():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    RequestIdempotency.__table__.create(engine)
    Order.__table__.create(engine)
    try:
        yield engine
    finally:
        engine.dispose()


def _rows(engine) -> list:
    with Session(engine) as session:
        return session.execute(select(RequestIdempotency)).scalars().all()


def _client(engine, place_trade) -> TestClient:
    app = FastAPI()
    app.include_router(trading.router)
    app.dependency_overrides[get_accounts_service] = lambda: AsyncMock(place_trade=place_trade)
    app.dependency_overrides[get_database_manager] = lambda: _DbManager(engine)
    return TestClient(app)


def _place_trade_mock(order_id="hbot-order-1"):
    return AsyncMock(return_value=order_id)


# ---------------------------------------------------------------------------------------
# No header: unchanged behavior (criterion: "no Idempotency-Key behaves exactly as before")
# ---------------------------------------------------------------------------------------

def test_no_key_places_the_order_and_writes_no_idempotency_row(engine):
    place_trade = _place_trade_mock()
    client = _client(engine, place_trade)

    response = client.post("/trading/orders", json=ORDER_BODY)

    assert response.status_code == 201, response.text
    assert response.json()["order_id"] == "hbot-order-1"
    place_trade.assert_awaited_once()
    assert _rows(engine) == []


# ---------------------------------------------------------------------------------------
# The happy path: same key, same body, twice
# ---------------------------------------------------------------------------------------

def test_a_sequential_repeat_replays_instead_of_placing_again(engine):
    place_trade = _place_trade_mock()
    client = _client(engine, place_trade)
    headers = {"Idempotency-Key": "key-1"}

    first = client.post("/trading/orders", json=ORDER_BODY, headers=headers)
    second = client.post("/trading/orders", json=ORDER_BODY, headers=headers)

    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    assert first.content == second.content
    assert "Idempotency-Replayed" not in first.headers
    assert second.headers["Idempotency-Replayed"] == "true"
    place_trade.assert_awaited_once()

    row, = _rows(engine)
    assert row.state == "COMPLETED"
    assert row.response_status == 201


# ---------------------------------------------------------------------------------------
# Same key, different body
# ---------------------------------------------------------------------------------------

def test_reusing_a_key_with_a_different_body_is_422_and_does_not_leak_the_first_order(engine):
    place_trade = _place_trade_mock(order_id="hbot-order-first")
    client = _client(engine, place_trade)
    headers = {"Idempotency-Key": "key-1"}

    first = client.post("/trading/orders", json=ORDER_BODY, headers=headers)
    second = client.post(
        "/trading/orders", json={**ORDER_BODY, "amount": "99"}, headers=headers
    )

    assert first.status_code == 201, first.text
    assert second.status_code == 422, second.text
    assert "hbot-order-first" not in second.text
    place_trade.assert_awaited_once()


# ---------------------------------------------------------------------------------------
# A claim still IN_FLIGHT must not be placed again
# ---------------------------------------------------------------------------------------

def test_a_claim_still_in_flight_returns_409_with_retry_after_and_is_not_placed_again(engine):
    place_trade = _place_trade_mock()
    client = _client(engine, place_trade)
    key = "key-in-flight"
    request_hash = trading._idempotency_request_hash(trading.TradeRequest(**ORDER_BODY))

    with Session(engine) as session:
        session.add(RequestIdempotency(
            idempotency_key=key, route=ROUTE, request_hash=request_hash, state="IN_FLIGHT",
        ))
        session.commit()

    response = client.post("/trading/orders", json=ORDER_BODY, headers={"Idempotency-Key": key})

    assert response.status_code == 409, response.text
    assert response.headers["Retry-After"]
    place_trade.assert_not_awaited()


# ---------------------------------------------------------------------------------------
# A placement that raises is recorded FAILED, and replayed (not retried) on retry
# ---------------------------------------------------------------------------------------

def test_a_failed_placement_is_recorded_and_replayed_without_retrying(engine):
    place_trade = AsyncMock(side_effect=trading.HTTPException(status_code=400, detail="trading rule violation"))
    client = _client(engine, place_trade)
    headers = {"Idempotency-Key": "key-failed"}

    first = client.post("/trading/orders", json=ORDER_BODY, headers=headers)
    second = client.post("/trading/orders", json=ORDER_BODY, headers=headers)

    assert first.status_code == 400, first.text
    assert first.json()["detail"] == "trading rule violation"
    assert second.status_code == 400, second.text
    assert second.json()["detail"] == "trading rule violation"
    assert second.headers["Idempotency-Replayed"] == "true"
    place_trade.assert_awaited_once()

    row, = _rows(engine)
    assert row.state == "FAILED"
    assert row.response_status == 400


# ---------------------------------------------------------------------------------------
# The repository directly: sweep and prune (the periodic cleanup pass)
# ---------------------------------------------------------------------------------------

class TestSweepAndPrune:
    @pytest.fixture
    def db_manager(self, engine):
        return _DbManager(engine)

    async def test_a_stale_in_flight_claim_is_swept_to_failed_and_then_replayed_not_reclaimed(self, db_manager, engine):
        key = "key-stale"
        async with db_manager.get_session_context() as session:
            owned, _ = await IdempotencyRepository(session).claim(key, ROUTE, "hash-1")
        assert owned

        # The sweep cutoff is passed in explicitly rather than read from a clock, so "older
        # than the bound" is simulated with a cutoff in the future -- no monkeypatching needed.
        future_cutoff = datetime.now(timezone.utc) + timedelta(hours=1)
        async with db_manager.get_session_context() as session:
            swept = await IdempotencyRepository(session).sweep_stale(future_cutoff)
        assert swept == 1

        async with db_manager.get_session_context() as session:
            row = await IdempotencyRepository(session).get_by_key(key)
        assert row.state == "FAILED"

        # A further claim attempt for the same key does not reclaim it -- it is answered FAILED.
        async with db_manager.get_session_context() as session:
            owned_again, existing = await IdempotencyRepository(session).claim(key, ROUTE, "hash-1")
        assert not owned_again
        assert existing.state == "FAILED"

    async def test_prune_deletes_old_claims_without_touching_the_order_they_refer_to(self, db_manager, engine):
        with Session(engine) as session:
            session.add(Order(
                client_order_id="hbot-order-1", account_name="master_account", connector_name="binance",
                trading_pair="BTC-USDT", trade_type="BUY", order_type="LIMIT", amount=1, price=20000,
            ))
            session.commit()

        async with db_manager.get_session_context() as session:
            owned, _ = await IdempotencyRepository(session).claim("key-old", ROUTE, "hash-1")
            assert owned
            await IdempotencyRepository(session).complete(
                "key-old", 201, json.dumps({"order_id": "hbot-order-1"})
            )

        future_cutoff = datetime.now(timezone.utc) + timedelta(hours=1)
        async with db_manager.get_session_context() as session:
            pruned = await IdempotencyRepository(session).prune(future_cutoff)
        assert pruned == 1
        assert _rows(engine) == []

        with Session(engine) as session:
            orders = session.execute(select(Order)).scalars().all()
        assert [o.client_order_id for o in orders] == ["hbot-order-1"]


# ---------------------------------------------------------------------------------------
# claim() retries when prune() deletes the conflicting row between the insert and the
# follow-up get_by_key -- without this, existing is None and the caller's
# existing.request_hash raises instead of answering the retry.
# ---------------------------------------------------------------------------------------

class TestClaimRetriesPastAVanishingRow:
    @pytest.fixture
    def db_manager(self, engine):
        return _DbManager(engine)

    async def test_claim_retries_and_reads_the_row_once_it_reappears(self, db_manager, engine, monkeypatch):
        key = "key-raced-by-prune"
        with Session(engine) as session:
            session.add(RequestIdempotency(
                idempotency_key=key, route=ROUTE, request_hash="hash-pre", state="IN_FLIGHT",
            ))
            session.commit()

        real_get_by_key = IdempotencyRepository.get_by_key
        calls = {"n": 0}

        async def flaky_get_by_key(self, key_arg):
            calls["n"] += 1
            if calls["n"] == 1:
                return None  # simulates prune() deleting the row in this window
            return await real_get_by_key(self, key_arg)

        monkeypatch.setattr(IdempotencyRepository, "get_by_key", flaky_get_by_key)

        async with db_manager.get_session_context() as session:
            owned, existing = await IdempotencyRepository(session).claim(key, ROUTE, "hash-new")

        assert not owned
        assert existing is not None
        assert existing.request_hash == "hash-pre"
        assert calls["n"] == 2

    async def test_claim_raises_instead_of_crashing_on_existing_none_when_the_row_never_reappears(
        self, db_manager, monkeypatch
    ):
        async def always_none(self, key_arg):
            return None

        monkeypatch.setattr(IdempotencyRepository, "get_by_key", always_none)

        async with db_manager.get_session_context() as session:
            repo = IdempotencyRepository(session)
            # Pre-claim the key directly so the insert keeps conflicting on every retry.
            await session.execute(
                RequestIdempotency.__table__.insert().values(
                    idempotency_key="key-always-vanishing", route=ROUTE,
                    request_hash="hash-pre", state="IN_FLIGHT",
                )
            )
            with pytest.raises(RuntimeError):
                await repo.claim("key-always-vanishing", ROUTE, "hash-new")


# ---------------------------------------------------------------------------------------
# A real Postgres: two concurrent requests for the same key must place exactly one order.
# ---------------------------------------------------------------------------------------

def _free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_until_ready(dsn: str, timeout: float) -> None:
    import asyncpg

    plain_dsn = dsn.replace("postgresql+asyncpg://", "postgresql://")

    async def _ping():
        conn = await asyncpg.connect(plain_dsn, timeout=2)
        await conn.close()

    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        try:
            asyncio.run(_ping())
            return
        except Exception as e:
            last_error = e
            time.sleep(0.5)
    raise RuntimeError(f"Postgres container never became ready: {last_error}")


@pytest.fixture(scope="module")
def postgres_dsn():
    """A disposable Postgres container -- the only database where a unique-constraint race
    between two live connections can actually be observed rather than assumed."""
    if shutil.which("docker") is None:
        pytest.skip("docker is not available: the concurrency test needs a real Postgres")

    port = _free_port()
    name = f"feat002-idempotency-{secrets.token_hex(4)}"
    try:
        subprocess.run(
            ["docker", "run", "--rm", "-d", "--name", name, "-p", f"127.0.0.1:{port}:5432",
             "-e", "POSTGRES_PASSWORD=test", "-e", "POSTGRES_DB=test", "postgres:16"],
            check=True, capture_output=True, text=True, timeout=60,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        pytest.skip(f"could not start a Postgres container for the concurrency test: {e}")

    dsn = f"postgresql+asyncpg://postgres:test@127.0.0.1:{port}/test"
    try:
        _wait_until_ready(dsn, timeout=30.0)
        yield dsn
    finally:
        subprocess.run(["docker", "stop", name], capture_output=True, timeout=30)


@pytest.fixture
async def real_db_manager(postgres_dsn):
    manager = AsyncDatabaseManager(postgres_dsn)
    async with manager.engine.begin() as conn:
        await conn.run_sync(RequestIdempotency.__table__.create, checkfirst=True)
    try:
        yield manager
    finally:
        async with manager.engine.begin() as conn:
            await conn.run_sync(RequestIdempotency.__table__.drop, checkfirst=True)
        await manager.close()


class TestConcurrentClaims:
    """Deterministic, not a repeated-runs-pass-under-load check: `claimed` fires the instant
    the winning request's claim is committed, so the second request's claim attempt is only
    ever sent while the first is provably still IN_FLIGHT -- never raced on timing."""

    async def test_two_concurrent_requests_place_exactly_one_order(self, real_db_manager):
        import httpx

        claimed = asyncio.Event()
        release = asyncio.Event()
        calls = []

        async def place_trade(**kwargs):
            calls.append(kwargs)
            claimed.set()
            await release.wait()
            return "hbot-order-1"

        app = FastAPI()
        app.include_router(trading.router)
        app.dependency_overrides[get_accounts_service] = lambda: AsyncMock(place_trade=place_trade)
        app.dependency_overrides[get_database_manager] = lambda: real_db_manager

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            headers = {"Idempotency-Key": "concurrent-key"}

            first_task = asyncio.create_task(
                client.post("/trading/orders", json=ORDER_BODY, headers=headers)
            )
            await asyncio.wait_for(claimed.wait(), timeout=5.0)

            second = await client.post("/trading/orders", json=ORDER_BODY, headers=headers)
            release.set()
            first = await first_task

        assert len(calls) == 1, "the connector must be called exactly once"
        statuses = sorted([first.status_code, second.status_code])
        assert statuses == [201, 409], (first.status_code, second.status_code, first.text, second.text)
