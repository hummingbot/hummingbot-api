from typing import Dict, Union
from pydantic import BaseModel


class BacktestingConfig(BaseModel):
    start_time: int = 1735689600  # 2025-01-01 00:00:00
    end_time: int = 1738368000  # 2025-02-01 00:00:00
    backtesting_resolution: str = "1m"
    trade_cost: float = 0.0006
    config: Union[Dict, str]