"""A controller must not pin itself to one package root (GW-45).

`bots/controllers/` is imported under two different roots: `controllers.*` inside a bot
container, and `bots.controllers.*` by hummingbot-api, which mounts the same tree one level
deeper. An absolute `from controllers.…` import resolves under the first and raises under
the second.

`lp_rebalancer/__init__.py` had one, and because it is the tree's only package-style
controller the damage was total rather than partial: `load_controller_config_class` tries
`…lp_rebalancer` and then `…lp_rebalancer.lp_rebalancer`, and the second has to import the
parent package first — so the broken `__init__` ran either way and both candidates failed.

    GET /controllers/generic/lp_rebalancer/config/template
    404 Controller configuration class for 'lp_rebalancer' not found

Which is the worse of the two failure modes: `GET /controllers/` still listed the
controller, so it advertised itself and was then unusable.
"""
import ast
from pathlib import Path

import pytest

CONTROLLERS = Path(__file__).resolve().parent.parent / "bots" / "controllers"


def _controller_modules():
    return sorted(CONTROLLERS.rglob("*.py"))


def _absolute_controller_imports(path: Path):
    """`from controllers.x import y` and `import controllers.x`, ignoring relative ones."""
    tree = ast.parse(path.read_text())
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            # node.level > 0 is a relative import, which is the correct form.
            if node.level == 0 and (node.module or "").split(".")[0] == "controllers":
                offenders.append(f"from {node.module} import ...")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] == "controllers":
                    offenders.append(f"import {alias.name}")
    return offenders


@pytest.mark.parametrize("path", _controller_modules(), ids=lambda p: str(p.name))
def test_no_controller_pins_itself_to_one_package_root(path):
    offenders = _absolute_controller_imports(path)

    assert offenders == [], (
        f"{path.relative_to(CONTROLLERS)} imports {offenders} absolutely. Under "
        "hummingbot-api this tree is bots.controllers.*, so a top-level `controllers` "
        "package does not exist and the import raises. Use a relative import."
    )


def test_the_sweep_actually_looked_at_something():
    """A glob that silently matches nothing would make every assertion above vacuous."""
    assert len(_controller_modules()) > 5


def test_the_package_style_controller_resolves_its_config_class():
    """lp_rebalancer is the tree's only package-style controller, and the reason the
    import style matters at all. Behavioural, because the import rule above is a proxy
    for this."""
    from utils.file_system import fs_util

    config_class = fs_util.load_controller_config_class("generic", "lp_rebalancer")

    assert config_class is not None, "lp_rebalancer resolves to no config class — /config/template 404s"
    assert config_class.__name__ == "LPRebalancerConfig"
