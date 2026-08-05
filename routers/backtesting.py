from fastapi import APIRouter, Depends, HTTPException

from deps import get_backtesting_service
from models.backtesting import BacktestingConfig
from services.backtesting_service import BacktestingService

router = APIRouter(tags=["Backtesting"], prefix="/backtesting")


@router.post("/run")
async def run_backtesting(
    backtesting_config: BacktestingConfig,
    service: BacktestingService = Depends(get_backtesting_service),
):
    """Run a backtest synchronously. Returns results directly (may timeout for long backtests)."""
    try:
        return await service.run_backtest_sync(backtesting_config.model_dump())
    except Exception as e:
        return {"error": str(e)}


@router.post("/tasks")
async def create_backtest_task(
    backtesting_config: BacktestingConfig,
    service: BacktestingService = Depends(get_backtesting_service),
):
    """Submit a backtest as a background task. Returns task ID for polling."""
    task = service.submit_task(backtesting_config.model_dump())
    return {"task_id": task.task_id, "status": task.status.value}


@router.get("/tasks")
async def list_backtest_tasks(
    service: BacktestingService = Depends(get_backtesting_service),
):
    """List all backtest tasks with their status (results excluded for brevity)."""
    return service.list_tasks()


@router.get("/tasks/{task_id}")
async def get_backtest_task(
    task_id: str,
    service: BacktestingService = Depends(get_backtesting_service),
):
    """Get a backtest task by ID, including results if completed."""
    payload = service.get_task_payload(task_id)
    if payload is None:
        # A reaped task is reported as 410 rather than 404: it did exist and its result is
        # permanently gone, which a caller cannot infer from "not found".
        if service.was_reaped(task_id):
            raise HTTPException(
                status_code=410,
                detail=f"Task {task_id} completed but its result was reaped to honour the retention limit",
            )
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    return payload


@router.delete("/tasks/{task_id}")
async def delete_backtest_task(
    task_id: str,
    service: BacktestingService = Depends(get_backtesting_service),
):
    """Cancel a running task or remove a completed one."""
    if not service.cancel_task(task_id):
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    return {"status": "deleted", "task_id": task_id}
