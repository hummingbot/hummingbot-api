import asyncio
import os
import sys
from typing import List

from database.connection import AsyncDatabaseManager
from database.repositories.gateway_clmm_repository import GatewayCLMMRepository


POOL_ADDR = os.environ.get("CLMM_TOKENPOOL_ADDRESS", "0xA5067360b13Fc7A2685Dc82dcD1bF2B4B8D7868B")


async def main():
    # Load DATABASE_URL from .env or environment
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        # Try to load .env file in repo root
        env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
        env_path = os.path.abspath(env_path)
        if os.path.exists(env_path):
            with open(env_path, "r") as f:
                for line in f:
                    if line.strip().startswith("DATABASE_URL="):
                        database_url = line.strip().split("=", 1)[1]
                        break

    if not database_url:
        print("DATABASE_URL not found in environment or .env. Set DATABASE_URL to your Postgres URL.")
        sys.exit(2)

    print(f"Using DATABASE_URL={database_url}")

    db = AsyncDatabaseManager(database_url)

    try:
        healthy = await db.health_check()
        print(f"DB health: {healthy}")

        async with db.get_session_context() as session:
            repo = GatewayCLMMRepository(session)

            # Fetch recent positions (limit large)
            positions = await repo.get_positions(limit=1000)

            matches = [p for p in positions if p.pool_address and p.pool_address.lower() == POOL_ADDR.lower()]

            if not matches:
                print(f"No positions found in DB for pool {POOL_ADDR}")
                return

            print(f"Found {len(matches)} position(s) for pool {POOL_ADDR}:\n")

            for pos in matches:
                print("--- POSITION ---")
                print(f"position_address: {pos.position_address}")
                print(f"status: {pos.status}")
                print(f"wallet_address: {pos.wallet_address}")
                print(f"created_at: {pos.created_at}")
                print(f"closed_at: {pos.closed_at}")
                print(f"entry_price: {pos.entry_price}")
                print(f"base_fee_collected: {pos.base_fee_collected}")
                print(f"quote_fee_collected: {pos.quote_fee_collected}")
                print(f"base_fee_pending: {pos.base_fee_pending}")
                print(f"quote_fee_pending: {pos.quote_fee_pending}")
                print("")

                # Fetch events for this position
                events = await repo.get_position_events(pos.position_address, limit=100)
                print(f"  {len(events)} events for position {pos.position_address}")
                for ev in events:
                    print(f"    - {ev.timestamp} {ev.event_type} tx={ev.transaction_hash} status={ev.status} base_fee_collected={ev.base_fee_collected} quote_fee_collected={ev.quote_fee_collected} gas_fee={ev.gas_fee}")

    except Exception as e:
        print("Error querying database:", e)
        raise
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
