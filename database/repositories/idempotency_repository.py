import logging
from datetime import datetime, timezone
from typing import Optional, Tuple

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import RequestIdempotency

logger = logging.getLogger(__name__)


class IdempotencyRepository:
    """Claims an Idempotency-Key for a write route and replays its response on a repeat.

    `claim` uses Postgres' `INSERT ... ON CONFLICT (idempotency_key) DO NOTHING`: the insert
    itself is the arbiter between two concurrent requests racing for the same key, which a
    check-then-insert on any database cannot settle atomically. Postgres-specific, consistent
    with the rest of this repo defaulting to postgresql+asyncpg (config.py, docker-compose.yml).
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    async def claim(self, key: str, route: str, request_hash: str) -> Tuple[bool, Optional[RequestIdempotency]]:
        """Try to own `key`.

        Returns `(True, the new row)` when this call wins the claim, or `(False, the existing
        row)` when another request already owns it -- the caller decides from `existing`
        whether to replay, reject (different body) or ask the caller to retry (still in flight).
        """
        stmt = (
            pg_insert(RequestIdempotency)
            .values(idempotency_key=key, route=route, request_hash=request_hash, state="IN_FLIGHT")
            .on_conflict_do_nothing(index_elements=["idempotency_key"])
            .returning(RequestIdempotency.id)
        )
        result = await self.session.execute(stmt)
        row_id = result.scalar_one_or_none()
        if row_id is not None:
            await self.session.flush()
            claimed = await self.session.get(RequestIdempotency, row_id)
            return True, claimed
        return False, await self.get_by_key(key)

    async def get_by_key(self, key: str) -> Optional[RequestIdempotency]:
        """The row for `key`, or None if it was never claimed (or has since been pruned)."""
        result = await self.session.execute(
            select(RequestIdempotency).where(RequestIdempotency.idempotency_key == key)
        )
        return result.scalar_one_or_none()

    async def complete(self, key: str, response_status: int, response_body: str) -> None:
        """Settle a claim this call owns as COMPLETED, storing the response to replay."""
        await self._settle(key, "COMPLETED", response_status, response_body)

    async def fail(self, key: str, response_status: int, response_body: str) -> None:
        """Settle a claim this call owns as FAILED, storing the error to replay."""
        await self._settle(key, "FAILED", response_status, response_body)

    async def _settle(self, key: str, state: str, response_status: int, response_body: str) -> None:
        await self.session.execute(
            update(RequestIdempotency)
            .where(RequestIdempotency.idempotency_key == key)
            .values(
                state=state,
                response_status=response_status,
                response_body=response_body,
                completed_at=datetime.now(timezone.utc),
            )
        )
        await self.session.flush()

    async def sweep_stale(self, older_than: datetime) -> int:
        """Fail every IN_FLIGHT claim created before `older_than`.

        A process that dies between claiming a key and completing it leaves an IN_FLIGHT row
        that would otherwise answer every retry with a 409 forever. Swept to FAILED instead, so
        a retry past the timeout is told the placement failed (safe: the alternative is placing
        a second order) rather than wedged on a claim nothing will ever complete.
        """
        result = await self.session.execute(
            update(RequestIdempotency)
            .where(RequestIdempotency.state == "IN_FLIGHT", RequestIdempotency.created_at < older_than)
            .values(
                state="FAILED",
                response_status=500,
                response_body='{"detail": "Request timed out before it completed"}',
                completed_at=datetime.now(timezone.utc),
            )
        )
        return result.rowcount or 0

    async def prune(self, older_than: datetime) -> int:
        """Delete claims (any state) created before `older_than`.

        Deleting an idempotency claim never touches the order it placed -- `orders` rows have
        their own lifecycle and this table only ever shadows them for the retry window.
        """
        result = await self.session.execute(
            delete(RequestIdempotency).where(RequestIdempotency.created_at < older_than)
        )
        return result.rowcount or 0
