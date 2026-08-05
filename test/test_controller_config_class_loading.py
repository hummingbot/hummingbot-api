"""
Tests for resolving a controller's concrete config class.

Regression: a controller module imports its own base class, so the base is a member of the
module namespace too -- and every base except ControllerConfigBase is itself a strict
subclass of ControllerConfigBase. The old check ("subclass of a base, but not that same
base") therefore accepted the *sibling* bases, and because inspect.getmembers() returns
members sorted by name, whichever name sorted first won.

That silently resolved every controller whose config class sorts after its base:
ema_trend_v1 and supertrend_v1 and macd_bb_v1 -> DirectionalTradingControllerConfigBase,
pmm_simple and pmm_dynamic -> MarketMakingControllerConfigBase. Since the bases set
extra="forbid", /config/validate then rejected every controller-specific field
(ema_fast, ema_slow, adx_period, ...) as "Extra inputs are not permitted", and
/config/template advertised only base fields. Controllers whose names happen to sort
first (bollinger_v1, dman_v3) worked, which is what made this look intermittent.

Run with: pytest test/test_controller_config_class_loading.py -v
"""
import pytest
from hummingbot.strategy_v2.controllers.controller_base import ControllerConfigBase
from hummingbot.strategy_v2.controllers.directional_trading_controller_base import DirectionalTradingControllerConfigBase
from hummingbot.strategy_v2.controllers.market_making_controller_base import MarketMakingControllerConfigBase

from utils.file_system import fs_util

BASE_CLASSES = {
    ControllerConfigBase.__name__,
    DirectionalTradingControllerConfigBase.__name__,
    MarketMakingControllerConfigBase.__name__,
}

# Controllers whose config class name sorts AFTER its own base class name -- the exact
# set that the name-ordering bug used to resolve to a base class.
SORTS_AFTER_ITS_BASE = [
    ("directional_trading", "ema_trend_v1", "EmaTrendV1Config"),
    ("directional_trading", "macd_bb_v1", "MACDBBV1ControllerConfig"),
    ("directional_trading", "supertrend_v1", "SuperTrendConfig"),
    ("market_making", "pmm_simple", "PMMSimpleConfig"),
    ("market_making", "pmm_dynamic", "PMMDynamicControllerConfig"),
]

# Controllers that sorted before their base and so worked even with the bug -- kept here
# so a future "fix" cannot regress them.
SORTS_BEFORE_ITS_BASE = [
    ("directional_trading", "bollinger_v1", "BollingerV1ControllerConfig"),
    ("directional_trading", "dman_v3", "DManV3ControllerConfig"),
]


@pytest.mark.parametrize(
    "controller_type,controller_name,expected",
    SORTS_AFTER_ITS_BASE + SORTS_BEFORE_ITS_BASE,
)
def test_resolves_the_concrete_config_class(controller_type, controller_name, expected):
    config_class = fs_util.load_controller_config_class(controller_type, controller_name)

    assert config_class is not None, f"{controller_name} did not resolve to any config class"
    assert config_class.__name__ not in BASE_CLASSES, (
        f"{controller_name} resolved to the base class {config_class.__name__}; "
        "controller-specific fields would be rejected as 'Extra inputs are not permitted'"
    )
    assert config_class.__name__ == expected


def test_controller_specific_fields_are_accepted_by_the_resolved_class():
    """The end-to-end symptom: /config/validate instantiates the resolved class."""
    config_class = fs_util.load_controller_config_class("directional_trading", "ema_trend_v1")

    config = config_class(
        id="ema_eth_5_55",
        controller_name="ema_trend_v1",
        controller_type="directional_trading",
        connector_name="binance_perpetual",
        trading_pair="ETH-USDT",
        interval="15m",
        ema_fast=5,
        ema_slow=55,
    )

    assert config.ema_fast == 5
    assert config.ema_slow == 55


def test_unknown_controller_still_returns_none():
    assert fs_util.load_controller_config_class("directional_trading", "no_such_controller") is None
