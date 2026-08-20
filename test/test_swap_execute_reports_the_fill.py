"""A swap execute response has to say what the swap did, not what was asked for.

`SwapExecuteResponse.amount` is the request echoed back, and its description said
"Amount swapped". On a live BUY of 1000 DOGE-1 that delivered 951.682904159 it answered
1000. The fill was not unavailable — the same call writes input_amount, output_amount and
price to the swap history two functions away — so the only way to learn what a swap did
was to execute it, discard the answer, and search the history by transaction hash.

An executor that trusts that field to reconcile its position is reconciling against its
own intent.
"""
from decimal import Decimal

import pytest

from models import SwapExecuteResponse


def test_the_fill_fields_exist_and_are_optional():
    # Optional because a submitted-not-confirmed swap has no fill yet. The alternative —
    # echoing the request into them — is the defect they exist to end.
    for field in ("input_amount", "output_amount", "price"):
        assert field in SwapExecuteResponse.model_fields, f"{field} missing from SwapExecuteResponse"
        assert SwapExecuteResponse.model_fields[field].default is None


def test_amount_is_documented_as_the_request_not_the_fill():
    described = SwapExecuteResponse.model_fields["amount"].description
    assert "REQUESTED" in described
    assert "not the fill" in described


def test_a_confirmed_swap_carries_what_moved():
    # The live BUY: 0.001491559 SOL paid for 951.682904159 DOGE-1, against a request
    # for 1000.
    response = SwapExecuteResponse(
        transaction_hash="fTCcM4qr",
        trading_pair="DOGE-1-SOL",
        side="BUY",
        amount=Decimal("1000"),
        input_amount=Decimal("0.001491559"),
        output_amount=Decimal("951.682904159"),
        price=Decimal("1.567285693041e-06"),
        status="confirmed",
    )

    assert response.amount == Decimal("1000")
    assert response.output_amount == Decimal("951.682904159")
    # The gap between the two is the whole point: the order was silently resized, and
    # nothing in the old response said so.
    assert response.output_amount != response.amount


def test_a_submitted_swap_reports_no_fill_rather_than_a_guess():
    response = SwapExecuteResponse(
        transaction_hash="pending",
        trading_pair="SOL-USDC",
        side="SELL",
        amount=Decimal("0.01"),
        status="submitted",
    )

    assert response.input_amount is None
    assert response.output_amount is None
    assert response.price is None


@pytest.mark.parametrize("field", ["input_amount", "output_amount", "price"])
def test_the_router_populates_each_field_only_when_the_fill_is_known(field):
    # The handler guards all three on `fill_known`, set from Gateway's confirmed `data`
    # block. Pinning it here keeps the two halves — model and handler — from drifting.
    source = open("routers/gateway_swap.py").read()
    value = "price" if field == "price" else field

    assert f"{field}={value} if fill_known else None" in source
