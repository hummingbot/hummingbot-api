"""
Shared validation for connector-specific extra_params forwarded to Gateway's
unified /trading routes.

Gateway destructures a fixed set of connector-specific keys from each request
body and silently ignores everything else, so hapi rejects loudly instead of
letting a typo'd key, a key sent to a connector that ignores it, or a value of
the wrong type get silently dropped or misparsed downstream.
"""
from typing import Any, Dict, Optional, Set, Tuple

from fastapi import HTTPException

# Spec entry: key -> (allowed value types, connectors that honor the key).
ExtraParamsSpec = Dict[str, Tuple[Tuple[type, ...], Set[str]]]


def validate_extra_params(
    extra_params: Optional[Dict[str, Any]],
    spec: ExtraParamsSpec,
    connector: str,
    route: str,
) -> None:
    """Reject extra_params Gateway's `route` would silently drop or misparse.

    Typed swap providers like 'jupiter/router' validate on their base name.
    """
    for key, value in (extra_params or {}).items():
        if key not in spec:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Unsupported extra_params key '{key}': Gateway's {route} honors "
                    f"only {sorted(spec)} and silently ignores everything else."
                ),
            )
        allowed_types, connectors = spec[key]
        if connector.split("/")[0] not in connectors:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"extra_params.{key} only applies to {sorted(connectors)}; "
                    f"'{connector}' would silently ignore it."
                ),
            )
        # bool subclasses int: check it explicitly so True never passes as an int.
        type_ok = (bool in allowed_types) if isinstance(value, bool) else isinstance(value, allowed_types)
        if not type_ok:
            expected = "/".join(t.__name__ for t in allowed_types)
            raise HTTPException(
                status_code=400,
                detail=f"extra_params.{key} must be {expected}, got {type(value).__name__} ({value!r}).",
            )
