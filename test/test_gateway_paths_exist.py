"""Every Gateway path this client calls must exist in Gateway's OpenAPI spec.

The client is hand-written against an API defined elsewhere, so a route rename in
Gateway is invisible here until a call 404s at runtime — in production, mid-trade.
That has happened repeatedly: /trading/swap/* split into
/trading/{router,clmm,amm}/*-swap, clmm quote-position became quote-liquidity, amm
add-liquidity became add, and per-connector fetch-pools moved under /trading/clmm.
Each was found by reading Gateway's source by hand.

This asserts the paths instead, against a vendored copy of Gateway's OpenAPI spec.

The copy is vendored rather than read from a sibling checkout so the check runs in CI,
where no gateway repo exists — and so that adopting a Gateway change is a reviewable
diff of this file, showing exactly which routes moved. Refresh it deliberately:

    cd ../gateway && pnpm generate:openapi
    cp ../gateway/openapi.json gateway-openapi.json

A failure here means one of two things: the client is stale and should follow the spec,
or the spec is stale and should be refreshed. Point GATEWAY_OPENAPI at a live spec to
check against an unmerged Gateway branch without touching the vendored copy.
"""
import json
import os
import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
SPEC_PATH = Path(os.environ.get("GATEWAY_OPENAPI", _REPO_ROOT / "gateway-openapi.json"))
CLIENT_PATH = _REPO_ROOT / "services" / "gateway_client.py"

# Path literals passed to _request(). Both plain and f-strings, since the trading
# routes interpolate the type ("trading/{trading_type}/quote-swap").
_REQUEST_CALL = re.compile(r'_request\(\s*"[A-Z]+"\s*,\s*f?"([^"]+)"')

# Interpolated segments stand for a value chosen at runtime; each is expanded to the
# values it can take, so the check stays exact rather than pattern-matching.
_SEGMENT_VALUES = {
    "{trading_type}": ["router", "clmm", "amm"],
}


def _spec_paths() -> set:
    spec = json.loads(SPEC_PATH.read_text())
    return set(spec.get("paths", {}))


def _normalise(path: str) -> list:
    """Client path -> the spec paths it can resolve to."""
    candidates = [f"/{path.lstrip('/')}"]
    for token, values in _SEGMENT_VALUES.items():
        if any(token in c for c in candidates):
            candidates = [c.replace(token, v) for c in candidates for v in values]
    # Any remaining {placeholder} is a resource id; the spec names it differently
    # (e.g. {address} vs {token_address}), so compare by segment shape.
    return candidates


def _shape(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "{}", path.split("?")[0].rstrip("/"))


def _client_paths() -> set:
    return set(_REQUEST_CALL.findall(CLIENT_PATH.read_text()))


def test_the_vendored_spec_is_present():
    """The spec ships with the repo, so a missing one is a broken checkout, not a skip."""
    assert SPEC_PATH.exists(), (
        f"No Gateway OpenAPI spec at {SPEC_PATH}. It is vendored so this check runs in CI; "
        "restore it with `cp ../gateway/openapi.json gateway-openapi.json`."
    )


def test_every_called_path_exists_in_the_spec():
    spec_shapes = {_shape(p) for p in _spec_paths()}
    missing = []
    for called in sorted(_client_paths()):
        for candidate in _normalise(called):
            if _shape(candidate) not in spec_shapes:
                missing.append(candidate)
    assert not missing, (
        "GatewayClient calls paths Gateway does not serve:\n  "
        + "\n  ".join(missing)
        + f"\n\nSpec: {SPEC_PATH} ({len(spec_shapes)} paths). "
        "Regenerate it with `pnpm generate:openapi` in the gateway repo if it is stale."
    )


def test_the_spec_actually_loaded():
    """Guard the guard: a spec that parsed to nothing would pass the check vacuously."""
    paths = _spec_paths()
    assert len(paths) > 20, f"Only {len(paths)} paths in {SPEC_PATH} — is it truncated?"
    assert any(p.startswith("/trading/") for p in paths), "No /trading routes in the spec"


def test_client_paths_were_found():
    """Guard the guard: a regex that matched nothing would also pass vacuously."""
    called = _client_paths()
    assert len(called) > 20, f"Only {len(called)} path literals found in {CLIENT_PATH}"
