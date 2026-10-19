"""The API hook must not pretend an old OKX perpetual reader started, and must stay quiet elsewhere."""

import logging

import pytest

from services.unified_connector_service import UnifiedConnectorService

HOOK_LOGGER = "services.unified_connector_service"
MISSING = "OKX perpetual has no ensure_funding_price_streams"
OLD_INFO = "No OKX funding stream hook"
STARTED = "Starting OKX funding streams"


def service() -> UnifiedConnectorService:
    return UnifiedConnectorService.__new__(UnifiedConnectorService)


class Named:
    def __init__(self, name):
        self.name = name


class OkxPerpetualDerivative:
    """Same class name as the client connector. name is unusable."""

    @property
    def name(self):
        raise RuntimeError("name is not available")


class Book:
    @property
    def snapshot(self):
        return ([("1", "1")], [("2", "1")])


class Tracker:
    def __init__(self):
        self.order_books = {"DOGE-USDT": Book()}


def warnings(caplog):
    return [record.getMessage() for record in caplog.records if record.levelno >= logging.WARNING]


def infos(caplog):
    return [record.getMessage() for record in caplog.records if record.levelno == logging.INFO]


@pytest.mark.parametrize("name", ["binance", "okx"])
async def test_a_connector_that_does_not_need_the_reader_stays_silent(caplog, name):
    caplog.set_level(logging.INFO, logger=HOOK_LOGGER)
    connector = Named(name)

    await service()._ensure_okx_funding_streams(connector)

    assert caplog.records == []
    assert not hasattr(connector, "_okx_funding_hook_missing_warned")


async def test_old_okx_perpetual_warns_once_that_the_queue_will_not_be_read(caplog):
    caplog.set_level(logging.INFO, logger=HOOK_LOGGER)
    connector = Named("okx_perpetual")
    hook = service()

    await hook._ensure_okx_funding_streams(connector)
    await hook._ensure_okx_funding_streams(connector)

    assert warnings(caplog) == [
        "OKX perpetual has no ensure_funding_price_streams; "
        "the order book can start, but the mark-price queue will not be read"
    ]
    assert OLD_INFO not in "".join(infos(caplog))
    assert STARTED not in "".join(infos(caplog))
    assert connector._okx_funding_hook_missing_warned is True


async def test_a_new_okx_perpetual_object_warns_again(caplog):
    caplog.set_level(logging.WARNING, logger=HOOK_LOGGER)
    hook = service()

    await hook._ensure_okx_funding_streams(Named("okx_perpetual"))
    await hook._ensure_okx_funding_streams(Named("okx_perpetual"))

    assert len(warnings(caplog)) == 2
    assert all(MISSING in message for message in warnings(caplog))


async def test_okx_perpetual_class_warns_when_name_cannot_be_read(caplog):
    caplog.set_level(logging.WARNING, logger=HOOK_LOGGER)
    connector = OkxPerpetualDerivative()

    await service()._ensure_okx_funding_streams(connector)

    assert len(warnings(caplog)) == 1
    assert MISSING in warnings(caplog)[0]
    assert connector._okx_funding_hook_missing_warned is True


async def test_a_broken_name_on_another_class_does_not_fail_the_call(caplog):
    caplog.set_level(logging.INFO, logger=HOOK_LOGGER)

    class Other:
        @property
        def name(self):
            raise RuntimeError("name is not available")

    await service()._ensure_okx_funding_streams(Other())

    assert caplog.records == []


async def test_existing_method_is_awaited_once(caplog):
    caplog.set_level(logging.INFO, logger=HOOK_LOGGER)

    class Ready(Named):
        def __init__(self):
            super().__init__("okx_perpetual")
            self.calls = 0

        async def ensure_funding_price_streams(self):
            self.calls += 1

    connector = Ready()
    await service()._ensure_okx_funding_streams(connector)

    assert connector.calls == 1
    assert warnings(caplog) == []
    assert any(STARTED in message for message in infos(caplog))


async def test_a_failing_reader_stays_inside_the_hook(caplog):
    caplog.set_level(logging.WARNING, logger=HOOK_LOGGER)

    class Broken(Named):
        def __init__(self):
            super().__init__("okx_perpetual")

        async def ensure_funding_price_streams(self):
            raise RuntimeError("reader failed")

    await service()._ensure_okx_funding_streams(Broken())

    assert len(warnings(caplog)) == 1
    assert "Could not start OKX funding price streams" in warnings(caplog)[0]
    assert "reader failed" in warnings(caplog)[0]


async def test_an_already_open_book_still_succeeds_when_the_reader_is_missing(caplog):
    caplog.set_level(logging.INFO, logger=HOOK_LOGGER)
    connector = Named("okx_perpetual")
    connector.order_book_tracker = Tracker()
    hook = service()

    def best_connector(connector_name, account_name=None):
        return connector

    hook.get_best_connector_for_market = best_connector

    assert await hook.initialize_order_book("okx_perpetual", "DOGE-USDT") is True
    assert any("Order book for DOGE-USDT already initialized" in message for message in infos(caplog))
    assert any(MISSING in message for message in warnings(caplog))
    assert OLD_INFO not in " ".join(record.getMessage() for record in caplog.records)
