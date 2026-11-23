"""A controller config name with a version dot is a valid deployment entry (dashboard#281).

The dashboard saves every controller config as "<base>_<major>.<minor>", and the save route
accepts that; the deploy validator added for path traversal (SEC-044) then refused the same
name for its dot. A dot between two plain segments cannot form a separator or a traversal
sequence, so it is allowed there and nowhere else.

Run with: pytest test/test_config_name_version_dot.py -v
"""
import pytest

from models.bot_orchestration import validate_safe_config_name


@pytest.mark.parametrize("name", [
    "experiment-falcon_0.1",
    "experiment-falcon_0.1.yml",
    "xemm-binance-kucoin-btc_1.12",
    "bollinger_v1_2.0",
    "good",
    "good.yml",
])
def test_a_version_dot_between_segments_is_accepted(name):
    assert validate_safe_config_name(name, "controllers_config") == name


@pytest.mark.parametrize("name", [
    "..",
    "../../../secret.txt",
    "../../../../etc/hosts",
    "/etc/hosts",
    "subdir/good.yml",
    ".hidden",
    "name.",
    "a..b",
    "..yml",
    "",
])
def test_a_traversal_or_edge_dot_is_still_refused(name):
    with pytest.raises(ValueError, match="Invalid controllers_config"):
        validate_safe_config_name(name, "controllers_config")
