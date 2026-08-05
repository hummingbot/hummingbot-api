"""
Tests for backtest result archiving, retention and restart-survival.

The behaviour being pinned down: a finished backtest is ~98% bulk arrays, so the payload
is written to a gzipped file and only its metrics stay resident. Reads rehydrate from
disk, the retention limit counts results rather than bytes, and a reaped result is
reported as 410 rather than an ambiguous 404.

The repo has no async test setup, so coroutines are driven with asyncio.run().

Run with: pytest test/test_backtest_storage.py -v
"""
import asyncio
import gzip
import json

import numpy as np
import pytest

from services.backtesting_service import BacktestingService, BacktestTaskStatus

# Mirrors the real payload shape, including the two things that trip a naive json.dump:
# numpy scalars, and processed_data being keyed by float epoch seconds.
PAYLOAD = {
    "executors": [{"id": "e1", "net_pnl_quote": 1.5}],
    "processed_data": {"close_bt": {1785429000.0: np.float64(3.5), 1785429060.0: np.float64(3.6)}},
    "results": {"net_pnl": -0.0179, "total_executors": 74, "sharpe_ratio": np.float64(-0.9375)},
    "position_holds": [],
    "position_held_timeseries": [],
    "pnl_timeseries": [{"timestamp": 1785429000.0, "total_pnl": np.int64(0)}],
}


class StubService(BacktestingService):
    """BacktestingService with the engine replaced, so no market data is needed."""

    def __init__(self, tmp_path, max_results=3, fail=False):
        super().__init__(max_results=max_results, results_path=str(tmp_path))
        self._fail = fail

    async def _execute_backtest(self, config):
        await asyncio.sleep(0)
        if self._fail:
            raise RuntimeError("no candles for ETH-USDT")
        return json.loads(json.dumps(PAYLOAD, default=lambda o: o.item()))


def _config(tag="c1"):
    return {"start_time": 1, "end_time": 2, "config": {"id": tag, "controller_name": "ema_trend_v1"}}


async def _submit_and_wait(service, tag="c1"):
    task = service.submit_task(_config(tag))
    await task._asyncio_task
    return task


def test_result_is_archived_and_only_metrics_stay_resident(tmp_path):
    async def scenario():
        service = StubService(tmp_path)
        task = await _submit_and_wait(service)

        assert task.status == BacktestTaskStatus.COMPLETED
        assert task.archived
        # The bulk is gone from memory; metrics remain.
        assert set(task.result) == {"results"}
        assert task.result["results"]["net_pnl"] == -0.0179

        archive = tmp_path / f"{task.task_id}.json.gz"
        assert archive.exists()
        with gzip.open(archive, "rt", encoding="utf-8") as fh:
            stored = json.load(fh)
        assert "processed_data" in stored and "pnl_timeseries" in stored
        return service, task

    service, task = asyncio.run(scenario())

    # A read rehydrates the whole payload, so the HTTP contract is unchanged.
    payload = service.get_task_payload(task.task_id)
    assert set(payload["result"]) == set(PAYLOAD)
    # json coerces float keys to strings exactly as FastAPI already did on the wire.
    assert payload["result"]["processed_data"]["close_bt"]["1785429000.0"] == 3.5
    assert payload["result"]["pnl_timeseries"][0]["total_pnl"] == 0


def test_archive_is_much_smaller_than_the_payload(tmp_path):
    async def scenario():
        service = StubService(tmp_path)
        return await _submit_and_wait(service)

    task = asyncio.run(scenario())
    raw = len(json.dumps(PAYLOAD, default=lambda o: o.item()))
    assert (tmp_path / f"{task.task_id}.json.gz").stat().st_size < raw


def test_oldest_results_are_reaped_beyond_the_limit(tmp_path):
    async def scenario():
        service = StubService(tmp_path, max_results=3)
        tasks = [await _submit_and_wait(service, f"c{i}") for i in range(5)]
        return service, tasks

    service, tasks = asyncio.run(scenario())

    assert len(service.tasks) == 3, "retention limit not enforced"
    dropped, kept = tasks[:2], tasks[2:]

    for task in dropped:
        assert task.task_id not in service.tasks
        assert not (tmp_path / f"{task.task_id}.json.gz").exists(), "archive left behind"
        assert service.was_reaped(task.task_id), "reaped id must stay distinguishable from a typo"
        assert service.get_task_payload(task.task_id) is None
    for task in kept:
        assert service.get_task_payload(task.task_id)["result"]["results"]["net_pnl"] == -0.0179


def test_results_survive_a_restart(tmp_path):
    async def scenario():
        service = StubService(tmp_path)
        return await _submit_and_wait(service)

    task = asyncio.run(scenario())

    # A fresh service over the same directory: what a container restart looks like.
    restarted = BacktestingService(max_results=3, results_path=str(tmp_path))

    assert task.task_id in restarted.tasks
    restored = restarted.tasks[task.task_id]
    assert restored.status == BacktestTaskStatus.COMPLETED
    assert restored.config["config"]["id"] == "c1"

    payload = restarted.get_task_payload(task.task_id)
    assert payload["result"]["results"]["total_executors"] == 74
    assert "processed_data" in payload["result"], "bulk payload should still be readable"


def test_failed_task_keeps_its_error_and_writes_no_archive(tmp_path):
    async def scenario():
        service = StubService(tmp_path, fail=True)
        return service, await _submit_and_wait(service)

    service, task = asyncio.run(scenario())

    assert task.status == BacktestTaskStatus.FAILED
    assert "no candles" in task.error
    assert not (tmp_path / f"{task.task_id}.json.gz").exists()
    assert service.get_task_payload(task.task_id)["error"] == "no candles for ETH-USDT"


def test_result_is_kept_in_memory_when_archiving_fails(tmp_path):
    """Losing a result is worse than holding it, so a write failure must not discard it."""

    class Unwritable(StubService):
        def _archive_path(self, task_id):
            return tmp_path / "missing-dir" / f"{task_id}.json.gz"

    async def scenario():
        service = Unwritable(tmp_path)
        return service, await _submit_and_wait(service)

    service, task = asyncio.run(scenario())

    assert not task.archived
    assert set(task.result) == set(PAYLOAD), "full result should have been retained in memory"
    assert "processed_data" in service.get_task_payload(task.task_id)["result"]


def test_deleting_a_task_removes_its_archive(tmp_path):
    async def scenario():
        service = StubService(tmp_path)
        return service, await _submit_and_wait(service)

    service, task = asyncio.run(scenario())

    assert service.cancel_task(task.task_id) is True
    assert not (tmp_path / f"{task.task_id}.json.gz").exists()
    # Deleted on request, not reaped -- the caller knows why it is gone.
    assert not service.was_reaped(task.task_id)
    assert service.cancel_task(task.task_id) is False


def test_sync_run_stores_nothing(tmp_path):
    async def scenario():
        service = StubService(tmp_path)
        result = await service.run_backtest_sync(_config())
        return service, result

    service, result = asyncio.run(scenario())

    assert "processed_data" in result
    assert service.tasks == {}
    assert list(tmp_path.glob("*.json.gz")) == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
