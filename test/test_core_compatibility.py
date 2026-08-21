"""A core older than this API must stop the boot, not quietly zero the books.

``environment.yml`` installs hummingbot unpinned because the fields this API reads are
not in a release yet, so a machine can very reasonably end up with an older core. The
read path in ``_persist_executor_completed`` wraps every figure in one broad
``except Exception`` that logs at DEBUG and substitutes ``Decimal("0")`` — so on an old
core the API does not fail, it books every executor's pnl, fees, filled amount and
volume as zero and never says why. That is the failure these tests exist to prevent.
"""
from types import SimpleNamespace

import pytest

pytest.importorskip("hummingbot")

from utils.core_compatibility import REQUIRED_CORE_SURFACE, require_core_surface  # noqa: E402


def test_the_installed_core_carries_everything_this_api_reads():
    """Guards the real dependency: fails if the environment resolves an old core."""
    require_core_surface()


def test_the_list_names_the_field_the_volume_work_added():
    checked = {(path, attribute) for path, attribute, _ in REQUIRED_CORE_SURFACE}
    assert (
        "hummingbot.strategy_v2.models.executors_info:ExecutorInfo",
        "volume_traded_quote",
    ) in checked


def test_a_missing_field_is_a_startup_error_naming_it(monkeypatch):
    """The whole point: loud, and specific enough to act on."""
    monkeypatch.setattr(
        "utils.core_compatibility.REQUIRED_CORE_SURFACE",
        [("hummingbot.strategy_v2.models.executors_info:ExecutorInfo", "not_a_field", "a field from the future")],
    )

    with pytest.raises(RuntimeError) as excinfo:
        require_core_surface()

    message = str(excinfo.value)
    assert "not_a_field" in message
    assert "older than this API requires" in message


def test_an_unimportable_target_is_reported_rather_than_raised_raw(monkeypatch):
    monkeypatch.setattr(
        "utils.core_compatibility.REQUIRED_CORE_SURFACE",
        [("hummingbot.strategy_v2.models.nope:Gone", "anything", "a module that is not there")],
    )

    with pytest.raises(RuntimeError) as excinfo:
        require_core_surface()

    assert "could not be imported" in str(excinfo.value)


def test_a_plain_class_is_checked_by_attribute_not_model_fields(monkeypatch):
    """ExecutorBase is not a pydantic model, so `model_fields` must not be assumed."""
    monkeypatch.setattr(
        "utils.core_compatibility._resolve",
        lambda path: SimpleNamespace(volume_traded_quote=property(lambda self: None)),
    )
    monkeypatch.setattr(
        "utils.core_compatibility.REQUIRED_CORE_SURFACE",
        [("anything:AtAll", "volume_traded_quote", "present as an attribute")],
    )

    require_core_surface()
