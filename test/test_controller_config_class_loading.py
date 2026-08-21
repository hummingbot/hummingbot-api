"""
Tests for resolving a controller's concrete config class.

Regression: a controller module imports its own base class, so the base is a member of the
module namespace too -- and every base except ControllerConfigBase is itself a strict
subclass of ControllerConfigBase. The old check ("subclass of a base, but not that same
base") therefore accepted the *sibling* bases, and because inspect.getmembers() returns
members sorted by name, whichever name sorted first won.

That silently resolved every controller whose config class sorts after its base:
supertrend_v1 and macd_bb_v1 -> DirectionalTradingControllerConfigBase, pmm_simple and
pmm_dynamic -> MarketMakingControllerConfigBase. (The report also named ema_trend_v1,
which is not a controller in this repo -- it is left out here so nobody adds it back to
a fixture list.) Since the bases set
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
from pydantic import ValidationError

from models import ControllerType
from utils.file_system import fs_util

BASE_CLASSES = {
    ControllerConfigBase.__name__,
    DirectionalTradingControllerConfigBase.__name__,
    MarketMakingControllerConfigBase.__name__,
}


def _discover_controllers():
    """Every controller actually present, the same way /controllers/ enumerates them.

    This used to be a hardcoded list, which drifted: it named ema_trend_v1, a controller
    that is not in this repo, so two tests failed on a checkout for a reason that had
    nothing to do with the resolver they were testing. The bug being covered is a
    property of the resolution mechanism, not of any one controller, so ask the
    filesystem instead and cover whatever is there.
    """
    discovered = []
    for controller_type in ControllerType:
        type_path = f"controllers/{controller_type.value}"
        try:
            files = fs_util.list_files(type_path)
            folders = fs_util.list_folders(type_path)
        except FileNotFoundError:
            continue
        discovered.extend(
            (controller_type.value, f[:-3])
            for f in files
            if f.endswith(".py") and f != "__init__.py"
        )
        # Package-style: a folder holding a same-named module.
        discovered.extend(
            (controller_type.value, folder)
            for folder in folders
            if folder != "__pycache__"
            and f"{folder}.py" in (fs_util.list_files(f"{type_path}/{folder}") or [])
        )
    return sorted(discovered)


CONTROLLERS = _discover_controllers()


def _sorts_after_its_base(config_class) -> bool:
    """Whether getmembers()' name ordering would have put a base ahead of this class."""
    bases = [b.__name__ for b in config_class.__mro__[1:] if b.__name__ in BASE_CLASSES]
    return any(config_class.__name__ > base for base in bases)


def test_there_are_controllers_to_check():
    """Guard the parametrisation: an empty discovery would pass everything vacuously."""
    assert CONTROLLERS, "no controllers discovered under bots/controllers/"


@pytest.mark.parametrize("controller_type,controller_name", CONTROLLERS)
def test_resolves_the_concrete_config_class(controller_type, controller_name):
    config_class = fs_util.load_controller_config_class(controller_type, controller_name)

    assert config_class is not None, f"{controller_name} did not resolve to any config class"
    assert config_class.__name__ not in BASE_CLASSES, (
        f"{controller_name} resolved to the base class {config_class.__name__}; "
        "controller-specific fields would be rejected as 'Extra inputs are not permitted'"
    )


def test_the_ordering_trap_is_still_represented():
    """The bug only ever bit controllers whose config name sorts after its base.

    If a repo ever held none of those, every assertion above would still pass while
    covering nothing, so say so out loud rather than going quietly vacuous.
    """
    trapped = [
        name for controller_type, name in CONTROLLERS
        if (cls := fs_util.load_controller_config_class(controller_type, name)) is not None
        and _sorts_after_its_base(cls)
    ]
    assert trapped, (
        "no controller here has a config class that sorts after its base, so nothing "
        "in this file exercises the name-ordering bug any more"
    )


def _own_fields(config_class):
    """The controller's own fields -- the ones a base class rejects as extra."""
    base = next(b for b in config_class.__mro__[1:] if b.__name__ in BASE_CLASSES)
    return set(config_class.model_fields) - set(base.model_fields)


@pytest.mark.parametrize("controller_type,controller_name", CONTROLLERS)
def test_the_resolved_class_declares_the_controllers_own_fields(controller_type, controller_name):
    """A base declares none of them, which is what made resolving to one fatal."""
    config_class = fs_util.load_controller_config_class(controller_type, controller_name)

    if config_class.__name__ == "PMMSimpleConfig":
        # Genuinely adds nothing to MarketMakingControllerConfigBase, so it has no own
        # field that could have been rejected. Named rather than skipped by a rule, so a
        # controller that loses its fields by accident still fails here.
        assert not _own_fields(config_class)
        return

    assert _own_fields(config_class), (
        f"{controller_name} resolved to {config_class.__name__}, which declares nothing "
        "its base does not -- that is what a base class looks like"
    )


@pytest.mark.parametrize(
    "base",
    [DirectionalTradingControllerConfigBase, MarketMakingControllerConfigBase],
)
def test_a_base_class_rejects_controller_specific_fields(base):
    """Why resolving to a base was fatal rather than merely wrong.

    The bases set extra="forbid", so /config/validate answered "Extra inputs are not
    permitted" for every controller-specific field and /config/template advertised only
    base fields. This pins the mechanism: if a base ever stopped forbidding extras, the
    resolution tests above would still pass while the bug they cover became invisible.
    """
    with pytest.raises(ValidationError) as excinfo:
        base(
            id="pinned",
            connector_name="binance_perpetual",
            trading_pair="ETH-USDT",
            a_controller_specific_field=5,
        )

    assert "a_controller_specific_field" in str(excinfo.value)
    assert "Extra inputs are not permitted" in str(excinfo.value)


def test_unknown_controller_still_returns_none():
    assert fs_util.load_controller_config_class("directional_trading", "no_such_controller") is None
