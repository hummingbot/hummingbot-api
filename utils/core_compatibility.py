"""Refuse to start against a hummingbot core that predates what this API reads.

``environment.yml`` installs ``hummingbot`` unpinned from PyPI, because the changes this
version depends on are not in a release yet. That makes the install order load-bearing:
build the image before the core ships and the API comes up against a core without the
fields it reads.

Nothing complains when that happens. ``_persist_executor_completed`` reads the executor's
figures inside a broad ``except Exception`` that logs at DEBUG and substitutes zeros, so a
missing attribute does not fail — it books every executor's pnl, fees, filled amount and
volume as 0 and says nothing. A whole deployment of silently zeroed accounting is a worse
outcome than not booting, so this check turns that into a startup error naming the field.

Drop a name from here once the release carrying it is the oldest one this API supports.
"""
from typing import List, Tuple

# (import path, attribute, what it is for) — each one a field this API reads off the
# core and cannot substitute.
REQUIRED_CORE_SURFACE: List[Tuple[str, str, str]] = [
    # Empty on purpose. This guard exists for a core field this API reads that a released
    # hummingbot may not carry yet; there is no such field right now. volume_traded_quote
    # was the last one, and it is gone: filled_amount_quote means the volume traded on
    # every executor, including LP, so there is nothing extra to require.
]


def _resolve(path: str):
    module_path, _, name = path.partition(":")
    module = __import__(module_path, fromlist=[name])
    return getattr(module, name)


def require_core_surface() -> None:
    """Raise if the installed hummingbot is missing anything this API reads."""
    missing = []
    for path, attribute, purpose in REQUIRED_CORE_SURFACE:
        try:
            owner = _resolve(path)
        except (ImportError, AttributeError) as e:
            missing.append(f"  - {path} could not be imported ({e})")
            continue
        fields = getattr(owner, "model_fields", None)
        present = attribute in fields if fields is not None else hasattr(owner, attribute)
        if not present:
            missing.append(f"  - {path}.{attribute} — {purpose}")

    if missing:
        import hummingbot

        raise RuntimeError(
            "The installed hummingbot core is older than this API requires. Missing:\n"
            + "\n".join(missing)
            + f"\n\nInstalled hummingbot: {getattr(hummingbot, '__version__', 'unknown')}. "
            "Install a core that carries these fields (see environment.yml) and rebuild. "
            "Starting anyway would record every executor's pnl, fees and volume as zero."
        )
