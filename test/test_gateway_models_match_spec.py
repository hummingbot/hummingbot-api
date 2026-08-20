"""The Gateway field names this service depends on must exist in Gateway's OpenAPI spec.

`test_gateway_paths_exist` pins the routes; this pins what travels over them. A field
rename in Gateway is invisible here until a response arrives with the old key missing —
and because every reader is a `.get()`, nothing raises. It silently reads `None`, which
is how a renamed fee field becomes a recorded fee of zero rather than an error.

Three checks, each guarding a different half of the wire:

- The vendored `models/gateway_generated.py` is a faithful mirror of the spec's schemas,
  so the pins below compare against Gateway's actual shapes rather than a stale memory
  of them.
- Every field the passthrough models declare exists in the Gateway schema they are
  built from. Those models are constructed by splatting a Gateway response
  (`Model(**result)`), so a field Gateway does not send is dead on arrival.
- Every camelCase key `services/gateway_client.py` writes or reads appears in the spec.
  This covers the routes still built by hand — /wallet, /config, /tokens, /pools.
- Every keyword the client passes to a generated request model is a field of it. The
  /trading methods build their payloads from those models, so their names are checked by
  construction — except that pydantic drops an unknown keyword, which on an optional
  field would send the request without it, in silence.

Refresh the spec and models together when adopting a Gateway change:

    cd ../gateway && pnpm generate:openapi
    cp ../gateway/openapi.json gateway-openapi.json
    make gateway-models
"""
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
SPEC_PATH = _REPO_ROOT / "gateway-openapi.json"
GENERATED_PATH = _REPO_ROOT / "models" / "gateway_generated.py"
CLIENT_PATH = _REPO_ROOT / "services" / "gateway_client.py"

# Models built by splatting a Gateway response, mapped to the Gateway schema that
# response conforms to. A model appears once per schema it is fed from: the create-pool
# and write-transaction responses are shared across the AMM and CLMM surfaces, and each
# of those schemas must independently satisfy it.
PASSTHROUGH_MODELS = [
    ("CLMMPoolInfoResponse", "PoolInfo"),
    ("CLMMPoolBin", "BinLiquidity"),
    ("CLMMQuotePositionResponse", "QuotePositionResponse"),
    ("AMMPoolInfoResponse", "AmmPoolInfo"),
    ("AMMPositionInfoResponse", "AmmPositionInfo"),
    ("AMMPositionDetail", "PositionDetail"),
    ("AMMQuoteLiquidityResponse", "QuoteLiquidityResponse"),
    ("AMMCreatePoolResponse", "CreatePoolResponse"),
    ("AMMCreatePoolResponse", "ClmmCreatePoolResponse"),
    ("AMMTransactionResponse", "AmmAddLiquidityResponse"),
    ("AMMTransactionResponse", "AmmRemoveLiquidityResponse"),
    ("AMMTransactionResponse", "AmmOpenPositionResponse"),
    ("AMMTransactionResponse", "AmmClosePositionResponse"),
]

# camelCase strings in the client that address Gateway's YAML config tree rather than an
# HTTP field — `/config` returns the config as-is, so these names are namespaced keys
# ("apiKeys.helius", "solana.defaultWallet") and no route schema declares them. Listed
# individually so a genuinely renamed wire field cannot hide behind the exemption.
CONFIG_TREE_KEYS = {"apiKeys", "defaultNetwork", "defaultWallet"}


def _spec() -> dict:
    return json.loads(SPEC_PATH.read_text())


def _schema_property_names(spec: dict) -> set:
    """Every property and query-parameter name anywhere in the spec."""
    names = set()

    def walk(node):
        if isinstance(node, list):
            for item in node:
                walk(item)
            return
        if not isinstance(node, dict):
            return
        for key, value in node.items():
            if key == "properties" and isinstance(value, dict):
                names.update(value)
            if key == "parameters" and isinstance(value, list):
                names.update(p["name"] for p in value if isinstance(p, dict) and "name" in p)
            walk(value)

    walk(spec)
    return names


def test_the_generated_models_match_the_vendored_spec():
    """Regenerating must reproduce the committed file exactly.

    The models are vendored so they can be imported without a build step and reviewed as
    a diff. That only holds while the committed copy is what the spec produces — a
    hand-edit, or a spec refreshed without rerunning the generator, and the pins below
    start comparing against something Gateway never described.
    """
    result = subprocess.run(
        [
            sys.executable, "-m", "datamodel_code_generator",
            "--input", str(SPEC_PATH),
            "--input-file-type", "openapi",
            "--openapi-scopes", "schemas",
            "--output", "/dev/stdout",
            "--output-model-type", "pydantic_v2.BaseModel",
            "--snake-case-field",
            "--target-python-version", "3.12",
            "--disable-timestamp",
            # Keeps `connector`/`network` as plain strings rather than baking Gateway's
            # current roster into this service. See the Makefile.
            "--ignore-enum-constraints",
            "--formatters", "black",
            "--formatters", "isort",
            "--custom-file-header", GENERATED_PATH.read_text().split("\n\n")[0],
        ],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, f"datamodel-codegen failed:\n{result.stderr}"
    assert result.stdout == GENERATED_PATH.read_text(), (
        f"{GENERATED_PATH.name} is not what {SPEC_PATH.name} generates. "
        "Run `make gateway-models` and commit the result."
    )


def _wire_names(model) -> set:
    """Field names as they travel on the wire — the alias where one is set."""
    return {(field.alias or name) for name, field in model.model_fields.items()}


@pytest.mark.parametrize("model_name,schema_name", PASSTHROUGH_MODELS)
def test_passthrough_models_only_declare_fields_gateway_sends(model_name, schema_name):
    from models import gateway_generated, gateway_trading

    declared = _wire_names(getattr(gateway_trading, model_name))
    sent = _wire_names(getattr(gateway_generated, schema_name))
    missing = sorted(declared - sent)
    assert not missing, (
        f"{model_name} declares {missing}, which Gateway's {schema_name} does not send. "
        f"Because {model_name} is built as {model_name}(**gateway_response), those fields "
        "read as their defaults forever rather than raising. Follow the rename or drop them."
    )
    # The reverse is deliberate, not a defect: Gateway sends fields this service has no
    # use for, and Pydantic drops them.


def test_every_wire_key_the_client_uses_exists_in_the_spec():
    """Guards the request side, which the passthrough models above never touch.

    The client hand-writes camelCase keys into query params and JSON bodies. GET
    parameters are the reason this cannot be replaced by a generated request model:
    the spec carries them as `parameters`, not as a schema.
    """
    used = set(re.findall(r'"([a-z][a-zA-Z0-9]*[A-Z][a-zA-Z0-9]*)"', CLIENT_PATH.read_text()))
    unknown = sorted(used - _schema_property_names(_spec()) - CONFIG_TREE_KEYS)
    assert not unknown, (
        "GatewayClient sends or reads keys Gateway's spec does not declare:\n  "
        + "\n  ".join(unknown)
        + f"\n\nSpec: {SPEC_PATH.name}. Either the client is stale and should follow the "
        "rename, or the key addresses Gateway's config tree and belongs in CONFIG_TREE_KEYS."
    )


# `ModelNameRequest(\n    key=..., ...)` — the shape every converted call site uses.
_MODEL_CALL = re.compile(r"\b([A-Z][A-Za-z0-9]*Request)\(\s*\n((?:\s+\w+=.*\n)+)")
_MODEL_KWARG = re.compile(r"^\s+(\w+)=", re.M)


def _model_calls() -> list:
    return [
        (m.group(1), _MODEL_KWARG.findall(m.group(2)))
        for m in _MODEL_CALL.finditer(CLIENT_PATH.read_text())
    ]


def test_every_kwarg_is_a_field_of_the_model_it_names():
    """Pydantic ignores an unknown keyword.

    On a required field that still fails loudly — the real one goes missing — but
    `slippagePc=1` for `slippagePct` would be dropped in silence, and the request would
    go out without the slippage the caller asked for.
    """
    from models import gateway_generated

    unknown = []
    for model_name, kwargs in _model_calls():
        model = getattr(gateway_generated, model_name, None)
        assert model is not None, f"{model_name} is not a generated model"
        fields = {(f.alias or n) for n, f in model.model_fields.items()} | set(model.model_fields)
        unknown += [f"{model_name}.{k}" for k in kwargs if k not in fields]

    assert not unknown, (
        "GatewayClient passes keywords no generated model declares:\n  "
        + "\n  ".join(sorted(unknown))
        + "\n\nPydantic drops an unknown keyword, so an optional one goes missing in "
        "silence. Follow the rename, or correct the spelling."
    )


def test_the_checks_above_are_not_vacuous():
    """A truncated spec or a regex matching nothing would pass every check silently."""
    spec = _spec()
    assert len(spec["components"]["schemas"]) > 50, "components.schemas looks truncated"
    assert len(_schema_property_names(spec)) > 100, "Found almost no property names in the spec"
    assert len(_model_calls()) > 10, (
        f"Only {len(_model_calls())} model constructions found in the client — has the regex "
        "gone stale, or did the /trading methods go back to hand-written dicts?"
    )
