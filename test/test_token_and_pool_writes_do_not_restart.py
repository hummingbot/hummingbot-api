"""Token and pool writes never restart Gateway, and never tell a caller to.

Gateway reads its token and pool lists off disk on every request, so a write to either
is live the moment it lands. Verified against a running Gateway on 2026-08-25: a token
added through this API was returned by the very next lookup, and a pool added through it
was both listed and priced by /trading/clmm/pool-info before anything restarted.

Config is the opposite case and keeps its restart hint. Gateway reads config once, at
startup -- a chain binds its RPC connection in the constructor of a per-network
singleton, and a connector's settings are the fields of a module-level object evaluated
at import. Measured on the same Gateway: after setting jupiter.slippagePct to 5,
GET /config answered 5 while quotes went on applying 1.

Restarting for a token or a pool is not merely wasted. Gateway exits 0 to restart, so on
a container whose restart policy is not `always` it does not come back at all.

Run with: pytest test/test_token_and_pool_writes_do_not_restart.py -v
"""
import inspect
import re
from pathlib import Path

import pytest

from services.gateway_client import GatewayClient

ROUTER_SOURCE = (Path(__file__).resolve().parents[1] / "routers" / "gateway.py").read_text()

# The endpoints whose writes Gateway picks up without a bounce.
LIVE_WRITE_ENDPOINTS = [
    "add_network_token",
    "save_network_token",
    "delete_network_token",
    "add_network_pool",
    "save_network_pool",
    "delete_network_pool",
]

# The endpoints whose writes Gateway only reads at startup.
RESTART_ENDPOINTS = [
    "update_connector_config",
    "update_api_keys",
    "update_network_config",
]


def _endpoint_source(name: str) -> str:
    """The body of one endpoint, up to the next top-level def."""
    match = re.search(rf"^async def {name}\(.*?(?=\n@router\.|\nasync def |\Z)", ROUTER_SOURCE,
                      re.S | re.M)
    assert match, f"no endpoint named {name} in routers/gateway.py"
    return match.group(0)


class TestTheClientCannotRestartGateway:

    def test_the_gateway_client_has_no_restart_call_at_all(self):
        # Restarting is the container's business (POST /gateway/restart -> docker), not
        # something an HTTP write should trigger as a side effect.
        source = inspect.getsource(GatewayClient)
        assert "restart" not in source.lower().replace("api restart", "")

    @pytest.mark.parametrize("method", ["add_token", "delete_token", "add_pool", "delete_pool"])
    def test_token_and_pool_methods_only_call_their_own_route(self, method):
        source = inspect.getsource(getattr(GatewayClient, method))
        assert "restart" not in source.lower()


class TestTheEndpointsDoNotRestartOrSayTo:

    # Anything that would bounce Gateway, or hand the caller a reason to.
    RESTART_ACTIONS = ("restart_required", "gateway_service.restart", "post_restart",
                       "restart_endpoint", "/gateway/restart")

    @pytest.mark.parametrize("endpoint", LIVE_WRITE_ENDPOINTS)
    def test_no_restart_is_triggered(self, endpoint):
        source = _endpoint_source(endpoint)
        for action in self.RESTART_ACTIONS:
            assert action not in source, (
                f"{endpoint} carries {action!r}; token and pool writes are live on disk "
                f"and need no restart"
            )

    @pytest.mark.parametrize("endpoint", LIVE_WRITE_ENDPOINTS)
    def test_the_docstring_does_not_ask_for_one(self, endpoint):
        # The old note said "After adding a token, restart Gateway for changes to take
        # effect." It was not true, and an agent reading the OpenAPI docs acted on it.
        doc = _endpoint_source(endpoint).split('"""')[1] if '"""' in _endpoint_source(endpoint) else ""
        assert not re.search(r"restart Gateway for changes", doc), (
            f"{endpoint} still tells the caller to restart"
        )


class TestConfigKeepsItsRestartHint:

    @pytest.mark.parametrize("endpoint", RESTART_ENDPOINTS)
    def test_config_writes_still_tell_the_caller_to_restart(self, endpoint):
        # Dropping this would leave Gateway reporting a config it is not running on.
        source = _endpoint_source(endpoint)
        assert "restart" in source.lower(), (
            f"{endpoint} must still surface that a restart is required"
        )
