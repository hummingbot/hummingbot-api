import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .models import Base

logger = logging.getLogger(__name__)


class AsyncDatabaseManager:
    def __init__(self, database_url: str):
        # Convert postgresql:// to postgresql+asyncpg:// for async support
        if database_url.startswith("postgresql://"):
            database_url = database_url.replace("postgresql://", "postgresql+asyncpg://")
            
        self.engine = create_async_engine(
            database_url,
            # Connection pool settings for async
            pool_size=5,
            max_overflow=10,
            pool_timeout=30,
            pool_recycle=1800,  # Recycle connections after 30 minutes
            pool_pre_ping=True,  # Test connections before using them
            # Engine settings
            echo=False,  # Set to True for SQL query logging
            echo_pool=False,  # Set to True for connection pool logging
            # Connection arguments for asyncpg
            connect_args={
                "server_settings": {"application_name": "hummingbot-api"},
                "command_timeout": 60,
            }
        )
        self.async_session = async_sessionmaker(
            self.engine, 
            class_=AsyncSession, 
            expire_on_commit=False
        )
        
    async def create_tables(self):
        """Create all tables defined in the models."""
        try:
            async with self.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

                # Run lightweight migrations for existing tables
                await self._run_migrations(conn)

                # Drop Hummingbot's native tables since we use our custom orders/trades tables
                await self._drop_hummingbot_tables(conn)

            logger.info("Database tables created successfully")
        except Exception as e:
            logger.error(f"Failed to create database tables: {e}")
            raise
    
    async def _run_migrations(self, conn):
        """Run lightweight schema migrations for existing tables."""
        migrations = [
            # Add controller_id to executors table (default "main" for existing rows)
            (
                "executors", "controller_id",
                "ALTER TABLE executors ADD COLUMN controller_id TEXT NOT NULL DEFAULT 'main'"
            ),
        ]
        for table, column, sql in migrations:
            try:
                # Check if column already exists
                result = await conn.execute(text(
                    "SELECT column_name FROM information_schema.columns "
                    f"WHERE table_name = '{table}' AND column_name = '{column}'"
                ))
                if result.fetchone() is None:
                    await conn.execute(text(sql))
                    logger.info(f"Migration: added {column} to {table}")
            except Exception as e:
                logger.debug(f"Migration check for {table}.{column}: {e}")

    async def _drop_hummingbot_tables(self, conn):
        """Drop Hummingbot's native database tables since we use custom ones."""
        hummingbot_tables = [
            "hummingbot_orders",
            "hummingbot_trade_fills", 
            "hummingbot_order_status"
        ]
        
        for table_name in hummingbot_tables:
            try:
                await conn.execute(text(f"DROP TABLE IF EXISTS {table_name}"))
                logger.info(f"Dropped Hummingbot table: {table_name}")
            except Exception as e:
                logger.debug(f"Could not drop table {table_name}: {e}")  # Use debug since table might not exist
            
    async def close(self):
        """Close all database connections."""
        await self.engine.dispose()
        logger.info("Database connections closed")
        
    def get_session(self) -> AsyncSession:
        """Get a new database session."""
        return self.async_session()
    
    @asynccontextmanager
    async def get_session_context(self) -> AsyncGenerator[AsyncSession, None]:
        """
        Get a database session with automatic error handling and cleanup.
        
        Usage:
            async with db_manager.get_session_context() as session:
                # Use session here
        """
        async with self.async_session() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.close()
    
    async def health_check(self) -> bool:
        """
        Check if the database connection is healthy.
        
        Returns:
            bool: True if connection is healthy, False otherwise.
        """
        try:
            async with self.engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            return True
        except Exception as e:
            logger.error(f"Database health check failed: {e}")
            return False