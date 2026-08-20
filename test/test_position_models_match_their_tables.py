"""Every field the gateway position code reads or writes must be a real column.

`position_to_dict` and `create_position` name columns as plain attributes, so a field that
does not exist fails only when the code path runs — and each fails differently. Reading a
missing attribute raises AttributeError, which surfaced as
`500 'GatewayAMMPosition' object has no attribute 'position_rent'` on
POST /gateway/amm/positions/search. Writing one raises TypeError inside
`GatewayAMMPosition(**position_data)`, where a broad `except` around the booking logs it
and moves on — so an opened position is simply never recorded, with no error to see.

Both came from the same commit, which added the two rent columns to the CLMM model and to
*both* repositories. The suite had no test touching the AMM `position_to_dict`, so it
stayed green.
"""
import inspect
import re

import pytest

from database.models import GatewayAMMPosition, GatewayCLMMPosition
from database.repositories.gateway_amm_repository import GatewayAMMRepository
from database.repositories.gateway_clmm_repository import GatewayCLMMRepository


def _columns(model) -> set:
    return {column.name for column in model.__table__.columns}


# `"key": num(position.attr)` and `"key": position.attr` — how position_to_dict reads.
_ATTRIBUTE_READ = re.compile(r"position\.(\w+)")


@pytest.mark.parametrize(
    "repository,model",
    [(GatewayAMMRepository, GatewayAMMPosition), (GatewayCLMMRepository, GatewayCLMMPosition)],
)
def test_position_to_dict_reads_only_real_columns(repository, model):
    source = inspect.getsource(repository.position_to_dict)
    read = set(_ATTRIBUTE_READ.findall(source))
    # Attributes that are genuinely methods or relationships, not columns, would go here.
    missing = sorted(name for name in read if name not in _columns(model))

    assert missing == [], (
        f"{repository.__name__}.position_to_dict reads {missing} but {model.__name__} has no such "
        f"column — an AttributeError at request time, not at import."
    )


@pytest.mark.parametrize(
    "repository,model",
    [(GatewayAMMRepository, GatewayAMMPosition), (GatewayCLMMRepository, GatewayCLMMPosition)],
)
def test_close_position_writes_only_real_columns(repository, model):
    source = inspect.getsource(repository.close_position)
    written = set(re.findall(r"position\.(\w+)\s*=", source))
    missing = sorted(name for name in written if name not in _columns(model))

    assert missing == [], f"{repository.__name__}.close_position assigns {missing}, absent from {model.__name__}"


def test_both_position_tables_carry_the_rent_columns():
    """A DAMM v2 position is an NFT with its own account, so it locks rent like a CLMM one.

    Pinned by name because the two tables drifted apart silently: the columns were added to
    one model and then used from both repositories.
    """
    for model in (GatewayAMMPosition, GatewayCLMMPosition):
        assert "position_rent" in _columns(model), model.__name__
        assert "position_rent_refunded" in _columns(model), model.__name__


def test_every_model_column_addition_has_a_migration():
    """create_all only creates missing tables, so a new column needs an ALTER to land.

    Without one the model and an existing database disagree, which is the same failure in
    a different disguise — the attribute exists in Python and the column does not in SQL.
    """
    from database import connection

    migration_source = inspect.getsource(connection.AsyncDatabaseManager._run_migrations)

    for model in (GatewayAMMPosition, GatewayCLMMPosition):
        for column in ("position_rent", "position_rent_refunded"):
            expected = f'"{model.__tablename__}", "{column}"'
            assert expected in migration_source, (
                f"{model.__tablename__}.{column} has no migration entry, so it will be missing "
                f"from any database created before it was added to the model."
            )
