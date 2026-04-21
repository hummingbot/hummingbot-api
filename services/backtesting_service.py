"""
BacktestingService manages background backtesting tasks.
Stores task state and results in memory for polling.
"""
import asyncio
import logging
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional

from hummingbot.strategy_v2.backtesting.backtesting_engine_base import BacktestingEngineBase

from config import settings

logger = logging.getLogger(__name__)


class BacktestTaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class BacktestTask:
    def __init__(self, task_id: str, config: dict):
        self.task_id = task_id
        self.config = config
        self.status = BacktestTaskStatus.PENDING
        self.created_at = datetime.now(timezone.utc)
        self.started_at: Optional[datetime] = None
        self.completed_at: Optional[datetime] = None
        self.result: Optional[Dict[str, Any]] = None
        self.error: Optional[str] = None
        self._asyncio_task: Optional[asyncio.Task] = None

    def to_dict(self, include_result: bool = True) -> dict:
        data = {
            "task_id": self.task_id,
            "status": self.status.value,
            "config": self.config,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "error": self.error,
        }
        if include_result and self.result is not None:
            data["result"] = self.result
        return data


class BacktestingService:
    def __init__(self, max_tasks: int = 50):
        self._tasks: Dict[str, BacktestTask] = {}
        self._engine = BacktestingEngineBase()
        self._max_tasks = max_tasks

    @property
    def tasks(self) -> Dict[str, BacktestTask]:
        return self._tasks

    def submit_task(self, config: dict) -> BacktestTask:
        """Submit a new backtesting task to run in the background."""
        self._cleanup_old_tasks()
        task_id = str(uuid.uuid4())[:8]
        task = BacktestTask(task_id=task_id, config=config)
        self._tasks[task_id] = task
        task._asyncio_task = asyncio.create_task(self._run_task(task))
        logger.info(f"Backtesting task {task_id} submitted")
        return task

    def get_task(self, task_id: str) -> Optional[BacktestTask]:
        return self._tasks.get(task_id)

    def cancel_task(self, task_id: str) -> bool:
        """Cancel a running task or remove a completed one."""
        task = self._tasks.get(task_id)
        if task is None:
            return False
        if task._asyncio_task and not task._asyncio_task.done():
            task._asyncio_task.cancel()
            task.status = BacktestTaskStatus.CANCELLED
            task.completed_at = datetime.now(timezone.utc)
        del self._tasks[task_id]
        return True

    def list_tasks(self) -> list:
        """List all tasks (without full results for brevity)."""
        return [t.to_dict(include_result=False) for t in self._tasks.values()]

    async def run_backtest_sync(self, config: dict) -> dict:
        """Run a backtest synchronously (returns full result directly)."""
        return await self._execute_backtest(config)

    async def _run_task(self, task: BacktestTask):
        """Background coroutine that executes the backtest."""
        task.status = BacktestTaskStatus.RUNNING
        task.started_at = datetime.now(timezone.utc)
        try:
            task.result = await self._execute_backtest(task.config)
            task.status = BacktestTaskStatus.COMPLETED
            logger.info(f"Backtesting task {task.task_id} completed")
        except asyncio.CancelledError:
            task.status = BacktestTaskStatus.CANCELLED
            logger.info(f"Backtesting task {task.task_id} cancelled")
        except Exception as e:
            task.status = BacktestTaskStatus.FAILED
            task.error = str(e)
            logger.error(f"Backtesting task {task.task_id} failed: {e}")
        finally:
            task.completed_at = datetime.now(timezone.utc)

    async def _execute_backtest(self, config: dict) -> dict:
        """Core backtest execution logic shared by sync and async modes."""
        if isinstance(config["config"], str):
            controller_config = self._engine.get_controller_config_instance_from_yml(
                config_path=config["config"],
                controllers_conf_dir_path=settings.app.controllers_path,
                controllers_module=settings.app.controllers_module
            )
        else:
            controller_config = self._engine.get_controller_config_instance_from_dict(
                config_data=config["config"],
                controllers_module=settings.app.controllers_module
            )
        backtesting_results = await self._engine.run_backtesting(
            controller_config=controller_config,
            trade_cost=config.get("trade_cost", 0.0006),
            start=int(config["start_time"]),
            end=int(config["end_time"]),
            backtesting_resolution=config.get("backtesting_resolution", "1m"),
        )
        processed_data = backtesting_results["processed_data"]["features"].fillna(0)
        executors_info = [e.to_dict() for e in backtesting_results["executors"]]
        results = backtesting_results["results"]
        results["sharpe_ratio"] = results["sharpe_ratio"] if results["sharpe_ratio"] is not None else 0

        # Serialize position holds
        position_holds = []
        for ph in backtesting_results.get("position_holds", []):
            position_holds.append({
                "connector_name": ph.connector_name,
                "trading_pair": ph.trading_pair,
                "buy_amount_base": float(ph.buy_amount_base),
                "buy_amount_quote": float(ph.buy_amount_quote),
                "sell_amount_base": float(ph.sell_amount_base),
                "sell_amount_quote": float(ph.sell_amount_quote),
                "net_amount_base": float(ph.net_amount_base),
                "cum_fees_quote": float(ph.cum_fees_quote),
                "volume_traded_quote": float(ph.volume_traded_quote),
                "is_closed": ph.is_closed,
                "n_executors": len(ph.source_executor_ids),
            })

        return {
            "executors": executors_info,
            "processed_data": processed_data.to_dict(),
            "results": results,
            "position_holds": position_holds,
            "position_held_timeseries": backtesting_results.get("position_held_timeseries", []),
            "pnl_timeseries": backtesting_results.get("pnl_timeseries", []),
        }

    def _cleanup_old_tasks(self):
        """Remove oldest completed/failed tasks if we exceed max_tasks."""
        if len(self._tasks) < self._max_tasks:
            return
        completed = [
            (tid, t) for tid, t in self._tasks.items()
            if t.status in (BacktestTaskStatus.COMPLETED, BacktestTaskStatus.FAILED, BacktestTaskStatus.CANCELLED)
        ]
        completed.sort(key=lambda x: x[1].completed_at or x[1].created_at)
        while len(self._tasks) >= self._max_tasks and completed:
            tid, _ = completed.pop(0)
            del self._tasks[tid]
