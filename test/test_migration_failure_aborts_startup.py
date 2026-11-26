"""An unexpected migration error must abort startup, not just log a warning (CORR-121).

`_run_migrations` used to swallow every per-column failure that was not already-exists:
it logged a `warning` and returned normally, so `create_tables` reported success and
`lifespan` let the API start serving traffic on a schema missing the column the ORM reads
-- a lock timeout, a permissions error, a disk-full ALTER all looked identical to a clean
boot. The column is read on the very next query, so the failure resurfaced as a runtime
500 instead of a refused startup.

The fix treats that failure the same way `create_tables` already treats every other schema
failure: log it and raise, naming the table and column so the operator knows which ALTER
to run by hand. The already-exists / duplicate-column case stays a quiet no-op, matched on
the driver's own exception via SQLAlchemy's `.orig` where available, so an ALTER whose
statement text happens to contain the phrase "already exists" is not misclassified.

Run with: pytest test/test_migration_failure_aborts_startup.py -v --asyncio-mode=auto
"""

from unittest.mock import AsyncMock

import pytest

from database.connection import AsyncDatabaseManager


class DuplicateColumnError(Exception):
    """Stands in for asyncpg.exceptions.DuplicateColumnError by name only."""


class _WrappedDriverError(Exception):
    """Stands in for SQLAlchemy's DBAPIError, which exposes the driver error via `.orig`."""

    def __init__(self, orig):
        super().__init__(str(orig))
        self.orig = orig


class _FakeResult:
    def fetchone(self):
        return None


class _FakeConnection:
    """Answers the column-existence SELECT, then raises on the ALTER that follows it."""

    def __init__(self, failure: Exception):
        self._failure = failure

    async def execute(self, statement, *args):
        if "information_schema" in str(statement):
            return _FakeResult()
        raise self._failure


def _manager():
    return AsyncDatabaseManager.__new__(AsyncDatabaseManager)


class TestAnUnexpectedFailureAbortsTheMigration:

    async def test_a_generic_failure_raises_instead_of_returning(self):
        conn = _FakeConnection(Exception("permission denied for table executors"))

        with pytest.raises(Exception):
            await _manager()._run_migrations(conn)

    async def test_the_raised_error_names_the_table_and_the_column(self):
        conn = _FakeConnection(Exception("permission denied for table executors"))

        with pytest.raises(Exception) as excinfo:
            await _manager()._run_migrations(conn)

        assert "executors" in str(excinfo.value)
        assert "controller_id" in str(excinfo.value)


class TestADuplicateColumnIsStillSwallowed:

    async def test_a_wrapped_asyncpg_duplicate_column_error_does_not_raise(self):
        failure = _WrappedDriverError(DuplicateColumnError('column "controller_id" already exists'))
        conn = _FakeConnection(failure)

        await _manager()._run_migrations(conn)  # must not raise

    async def test_a_plain_already_exists_message_does_not_raise(self):
        conn = _FakeConnection(Exception("column already exists"))

        await _manager()._run_migrations(conn)  # must not raise


class TestCreateTablesPropagatesTheFailure:

    class _FakeConn:
        async def run_sync(self, fn):
            return None

    class _FakeEngineCtx:
        def __init__(self, conn):
            self._conn = conn

        async def __aenter__(self):
            return self._conn

        async def __aexit__(self, *exc_info):
            return False

    class _FakeEngine:
        def __init__(self, conn):
            self._conn = conn

        def begin(self):
            return TestCreateTablesPropagatesTheFailure._FakeEngineCtx(self._conn)

    async def test_startup_aborts_rather_than_logging_and_returning(self):
        manager = _manager()
        manager.engine = self._FakeEngine(self._FakeConn())

        async def _boom(conn):
            raise RuntimeError("Migration failed for executors.controller_id: permission denied")

        manager._run_migrations = _boom
        manager._drop_hummingbot_tables = AsyncMock()

        with pytest.raises(RuntimeError, match="executors.controller_id"):
            await manager.create_tables()
