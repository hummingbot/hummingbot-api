"""
Bot client defaults router.

Configures deployed bots, not the API. The API prices through its own ticker pool (see
MarketDataService), but every deployed bot still runs hummingbot's RateOracle, configured
from the conf_client.yml copied out of its credentials profile at deploy time. These
endpoints read and persist that configuration so bots are not stuck with whatever source
the shipped template defaults to.

This router is the only writer of a credentials profile's conf_client.yml, which is why
rate_limits_share_pct lives here rather than behind an endpoint of its own: a second
writer of the same file would have to repeat this validation and would silently skip the
live-quote-token switch below. The set it writes is closed and small -- rate_oracle_source,
global_token, rate_limits_share_pct -- so no caller can reach the mqtt_bridge or gateway
credentials that share the file.
"""

import yaml
from fastapi import APIRouter, Depends, HTTPException, Query
from hummingbot.core.rate_oracle.rate_oracle import RATE_ORACLE_SOURCES

from deps import get_market_data_service
from models.bot_rate_oracle import (
    GlobalTokenConfig,
    RateOracleConfig,
    RateOracleConfigResponse,
    RateOracleConfigUpdateRequest,
    RateOracleConfigUpdateResponse,
    RateOracleSourceConfig,
)
from services.accounts_service import validate_safe_name
from services.market_data_service import MarketDataService
from utils.file_system import FileSystemUtil

router = APIRouter(tags=["Bot Orchestration"], prefix="/bot-orchestration/rate-oracle")

DEFAULT_ACCOUNT = "master_account"


def _conf_client_path(account_name: str) -> str:
    """Path to an account's conf_client.yml, relative to the FileSystemUtil base path ("bots")."""
    validate_safe_name(account_name, "account name")
    return f"credentials/{account_name}/conf_client.yml"


def _read_conf_client(account_name: str) -> dict:
    path = _conf_client_path(account_name)
    try:
        data = FileSystemUtil().read_yaml_file(path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Configuration file not found: {path}")
    except yaml.YAMLError:
        raise HTTPException(status_code=500, detail=f"Configuration file is not valid YAML: {path}")
    # A hand-edited file can hold anything at the top level, including a YAML list or a
    # bare scalar; read as empty rather than handing callers something they can't `.get()`.
    return data if isinstance(data, dict) else {}


def _clean_str(value, default: str) -> str:
    """``value`` as a stripped string, or ``default`` for anything else.

    conf_client.yml is user-editable and this only reads it back, so a blank, wrong-typed
    or missing value must read as the default rather than failing the whole GET -- same
    principle as _share_pct below.
    """
    return value.strip() if isinstance(value, str) and value.strip() else default


def _share_pct(config_data: dict) -> float:
    """rate_limits_share_pct as a number, falling back to hummingbot's own default.

    The key is absent from older templates, and a hand-edited file can hold anything, so
    an unreadable value reads as the default rather than failing the whole GET -- the
    caller is asking what bots will run with, and hummingbot itself would fall back the
    same way.
    """
    try:
        value = float(config_data.get("rate_limits_share_pct"))
    except (TypeError, ValueError):
        return 100.0
    return value if 0 < value <= 100 else 100.0


def _to_config(account_name: str, config_data: dict) -> RateOracleConfig:
    rate_oracle_source = config_data.get("rate_oracle_source")
    if not isinstance(rate_oracle_source, dict):
        rate_oracle_source = {}
    global_token = config_data.get("global_token")
    if not isinstance(global_token, dict):
        global_token = {}
    return RateOracleConfig(
        account_name=account_name,
        rate_oracle_source=RateOracleSourceConfig(
            name=_clean_str(rate_oracle_source.get("name"), "gate_io")
        ),
        global_token=GlobalTokenConfig(
            global_token_name=_clean_str(global_token.get("global_token_name"), "USDT"),
            global_token_symbol=_clean_str(global_token.get("global_token_symbol"), "$"),
        ),
        rate_limits_share_pct=_share_pct(config_data),
    )


@router.get("/sources", response_model=list[str])
async def get_bot_rate_oracle_sources():
    """List the rate oracle sources a bot can be configured with. Does not affect the API's own pricing."""
    return list(RATE_ORACLE_SOURCES.keys())


@router.get("/config", response_model=RateOracleConfigResponse)
async def get_bot_rate_oracle_config(account_name: str = Query(DEFAULT_ACCOUNT)):
    """
    Get the client defaults that bots deployed with this credentials profile will use.

    Args:
        account_name: Credentials profile whose conf_client.yml to read (default master_account)
    """
    config = _to_config(account_name, _read_conf_client(account_name))
    return RateOracleConfigResponse(**config.model_dump(), available_sources=list(RATE_ORACLE_SOURCES.keys()))


@router.put("/config", response_model=RateOracleConfigUpdateResponse)
async def update_bot_rate_oracle_config(
    update_request: RateOracleConfigUpdateRequest,
    account_name: str = Query(DEFAULT_ACCOUNT),
    market_data_service: MarketDataService = Depends(get_market_data_service),
):
    """
    Update the bot rate oracle source, global token and/or rate-limit share in an account's
    conf_client.yml.

    Bots pick the change up on their next deploy (running bots keep their copied config).
    Changing master_account's global token also switches the API's own valuation quote token.

    Args:
        update_request: Configuration updates to apply
        account_name: Credentials profile whose conf_client.yml to update (default master_account)
    """
    config_data = _read_conf_client(account_name)
    changes_made = []

    if update_request.rate_oracle_source is not None:
        source_name = update_request.rate_oracle_source.name
        if source_name not in RATE_ORACLE_SOURCES:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid rate oracle source: {source_name}. "
                       f"Available sources: {list(RATE_ORACLE_SOURCES.keys())}"
            )
        if not isinstance(config_data.get("rate_oracle_source"), dict):
            config_data["rate_oracle_source"] = {}
        config_data["rate_oracle_source"]["name"] = source_name
        changes_made.append(f"rate_oracle_source updated to {source_name}")

    if update_request.global_token is not None:
        if not isinstance(config_data.get("global_token"), dict):
            config_data["global_token"] = {}
        global_token = config_data["global_token"]
        token_name = update_request.global_token.global_token_name
        if token_name is not None:
            global_token["global_token_name"] = token_name
            changes_made.append(f"global_token_name updated to {token_name}")
        token_symbol = update_request.global_token.global_token_symbol
        if token_symbol is not None:
            global_token["global_token_symbol"] = token_symbol
            changes_made.append(f"global_token_symbol updated to {token_symbol}")

    if update_request.rate_limits_share_pct is not None:
        # Written as a float so the file keeps the shape hummingbot's own writer produces;
        # the 0 < pct <= 100 bound is enforced by the request model, which mirrors
        # ClientConfigMap's, so a value the bot would reject never reaches the file.
        share_pct = float(update_request.rate_limits_share_pct)
        config_data["rate_limits_share_pct"] = share_pct
        changes_made.append(f"rate_limits_share_pct updated to {share_pct}")

    if changes_made:
        FileSystemUtil().dump_dict_to_yaml(_conf_client_path(account_name), config_data)

    # Only switch the API's live quote token once the file is written, so a failed write
    # can't leave the API and future bots disagreeing on the token.
    token_name = update_request.global_token and update_request.global_token.global_token_name
    if token_name is not None and account_name == DEFAULT_ACCOUNT:
        market_data_service.quote_token = token_name

    return RateOracleConfigUpdateResponse(
        success=True,
        message="; ".join(changes_made) if changes_made else "No changes made",
        config=_to_config(account_name, config_data),
    )
