"""
Unit tests for routers.gateway_extras.validate_extra_params — the shared guard
that keeps connector-specific extra_params from being silently dropped or
misparsed by Gateway's unified /trading routes.
"""
import pytest
from fastapi import HTTPException

from routers.gateway_extras import validate_extra_params

SPEC = {
    "approximateIfNoExactOut": ((bool,), {"jupiter", "dflow", "okx", "titan"}),
    "strategyType": ((int,), {"meteora"}),
    "configAddress": ((str,), {"meteora"}),
    "feeBps": ((int, float), {"meteora", "uniswap"}),
}


def _detail(exc_info):
    return exc_info.value.detail


def test_none_and_empty_pass():
    validate_extra_params(None, SPEC, "jupiter", "route")
    validate_extra_params({}, SPEC, "jupiter", "route")


def test_valid_params_pass():
    validate_extra_params({"approximateIfNoExactOut": False}, SPEC, "jupiter", "route")
    validate_extra_params({"strategyType": 0}, SPEC, "meteora", "route")
    validate_extra_params({"feeBps": 0.25}, SPEC, "uniswap", "route")


def test_typed_provider_validates_on_base_name():
    validate_extra_params({"approximateIfNoExactOut": True}, SPEC, "jupiter/router", "route")


def test_unknown_key_rejected():
    with pytest.raises(HTTPException) as exc:
        validate_extra_params({"gasPrice": 20}, SPEC, "uniswap", "the route")
    assert exc.value.status_code == 400
    assert "gasPrice" in _detail(exc)


def test_wrong_connector_rejected():
    """A key the connector would silently ignore must 400, not pass."""
    with pytest.raises(HTTPException) as exc:
        validate_extra_params({"strategyType": 0}, SPEC, "orca", "route")
    assert exc.value.status_code == 400
    assert "orca" in _detail(exc)


def test_wrong_connector_rejected_for_typed_provider():
    with pytest.raises(HTTPException) as exc:
        validate_extra_params({"approximateIfNoExactOut": True}, SPEC, "meteora/clmm", "route")
    assert exc.value.status_code == 400


def test_none_value_rejected():
    """A null value would reach Gateway as the string 'None' on GET paths."""
    with pytest.raises(HTTPException) as exc:
        validate_extra_params({"approximateIfNoExactOut": None}, SPEC, "jupiter", "route")
    assert exc.value.status_code == 400
    assert "bool" in _detail(exc)


def test_wrong_type_rejected():
    with pytest.raises(HTTPException) as exc:
        validate_extra_params({"approximateIfNoExactOut": "yes"}, SPEC, "jupiter", "route")
    assert exc.value.status_code == 400


def test_bool_does_not_pass_as_int():
    """bool subclasses int — True must not satisfy an int-typed key."""
    with pytest.raises(HTTPException) as exc:
        validate_extra_params({"strategyType": True}, SPEC, "meteora", "route")
    assert exc.value.status_code == 400
    assert "int" in _detail(exc)


def test_int_does_not_pass_as_bool():
    with pytest.raises(HTTPException) as exc:
        validate_extra_params({"approximateIfNoExactOut": 1}, SPEC, "jupiter", "route")
    assert exc.value.status_code == 400
