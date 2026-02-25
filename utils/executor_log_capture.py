"""
Executor log capture via in-memory ring buffer.

Uses Python's contextvars to attribute log records to specific executor instances,
even though executors share class-level loggers. When executor.start() creates an
asyncio Task, the Task inherits the current context - so a ContextVar set before
start() persists for that executor's entire lifetime.
"""
import logging
import traceback
from collections import deque
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Dict, List, Optional

# ContextVar that identifies which executor is running in the current async task.
# Set before executor.start() so the spawned Task inherits it.
current_executor_id: ContextVar[Optional[str]] = ContextVar("current_executor_id", default=None)


class ExecutorLogHandler(logging.Handler):
    """
    Custom logging handler that routes log records to per-executor ring buffers.

    Reads current_executor_id from contextvars to determine which executor
    produced the log record. Unattributed records go to a global buffer.
    """

    def __init__(self, capture: "ExecutorLogCapture"):
        super().__init__()
        self._capture = capture

    def emit(self, record: logging.LogRecord):
        try:
            entry = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "level": record.levelname,
                "message": self.format(record),
            }

            if record.exc_info and record.exc_info[1] is not None:
                entry["exc_info"] = "".join(traceback.format_exception(*record.exc_info))

            executor_id = current_executor_id.get()
            if executor_id is not None:
                self._capture._append_log(executor_id, entry)
            else:
                self._capture._append_global(entry)
        except Exception:
            self.handleError(record)


class ExecutorLogCapture:
    """
    Singleton-style class that manages per-executor log ring buffers.

    Usage:
        capture = ExecutorLogCapture()
        capture.install()  # attaches handler to executor loggers

        # Before executor.start():
        token = current_executor_id.set(executor_id)
        executor.start()
        current_executor_id.reset(token)

        # Later:
        logs = capture.get_logs(executor_id)
    """

    def __init__(self, per_executor_max: int = 50, global_max: int = 200):
        self._per_executor_max = per_executor_max
        self._global_max = global_max
        self._logs: Dict[str, deque] = {}
        self._global_logs: deque = deque(maxlen=global_max)
        self._handler: Optional[ExecutorLogHandler] = None

    def install(self):
        """Attach the log handler to the hummingbot executor logger hierarchy."""
        if self._handler is not None:
            return

        self._handler = ExecutorLogHandler(self)
        self._handler.setLevel(logging.INFO)
        formatter = logging.Formatter("%(name)s - %(message)s")
        self._handler.setFormatter(formatter)

        # Attach to the parent logger for all executors
        logger = logging.getLogger("hummingbot.strategy_v2.executors")
        logger.setLevel(logging.INFO)
        logger.addHandler(self._handler)

    def uninstall(self):
        """Remove the log handler."""
        if self._handler is None:
            return

        logger = logging.getLogger("hummingbot.strategy_v2.executors")
        logger.removeHandler(self._handler)
        self._handler = None

    def _append_log(self, executor_id: str, entry: dict):
        if executor_id not in self._logs:
            self._logs[executor_id] = deque(maxlen=self._per_executor_max)
        self._logs[executor_id].append(entry)

    def _append_global(self, entry: dict):
        self._global_logs.append(entry)

    def get_logs(
        self,
        executor_id: str,
        level: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> List[dict]:
        """Get log entries for a specific executor."""
        buf = self._logs.get(executor_id)
        if buf is None:
            return []

        logs = list(buf)
        if level:
            level_upper = level.upper()
            logs = [e for e in logs if e["level"] == level_upper]
        if limit:
            logs = logs[-limit:]
        return logs

    def get_error_count(self, executor_id: str) -> int:
        """Get count of ERROR-level logs for an executor."""
        buf = self._logs.get(executor_id)
        if buf is None:
            return 0
        return sum(1 for e in buf if e["level"] == "ERROR")

    def get_last_error(self, executor_id: str) -> Optional[str]:
        """Get the most recent ERROR message for an executor, or None."""
        buf = self._logs.get(executor_id)
        if buf is None:
            return None
        for entry in reversed(buf):
            if entry["level"] == "ERROR":
                return entry["message"]
        return None

    def get_global_logs(self, level: Optional[str] = None) -> List[dict]:
        """Get unattributed (global) log entries."""
        logs = list(self._global_logs)
        if level:
            level_upper = level.upper()
            logs = [e for e in logs if e["level"] == level_upper]
        return logs

    def clear(self, executor_id: str):
        """Remove logs for a specific executor."""
        self._logs.pop(executor_id, None)
