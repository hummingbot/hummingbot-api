import logging
from typing import Any, Dict, List, Mapping, Optional

# Create module-specific logger
logger = logging.getLogger(__name__)


def shared_wallet_connectors(connector_balances: Mapping[str, Any],
                             account_groups: Optional[Dict[str, Optional[str]]] = None) -> Dict[str, str]:
    """
    Connectors of one account that read a wallet another connector has already reported.

    Exchanges with a unified account (e.g. Bitget UTA) report the same wallet through both the spot connector
    ("bitget") and the perpetual connector ("bitget_perpetual"), each in full. Summing both counts the money
    twice. Two connectors share a wallet only when the connectors themselves say so, by reporting the same
    exchange-side account id (ConnectorBase.account_group_id, derived from the credentials' account and its
    mode). Balances are never compared: two separate wallets holding the same amounts stay separate.

    Within a group the first connector with a non-empty balance list is kept, so a connector whose refresh
    failed (stored as []) never hides the wallet that another connector of the group did report.

    Args:
        connector_balances: {connector_name: [token_info, ...]} for the connectors being summed, in order. Pass
            the already filtered selection, so that a connector that was not selected can never stand in for a
            selected one.
        account_groups: {connector_name: account id or None}. None or a missing connector means that
            connector's balance stands on its own.

    Returns:
        {duplicate connector name: name of the connector of the same wallet that is counted}
    """
    if not account_groups:
        return {}
    members_by_group: Dict[str, List[str]] = {}
    for connector_name in connector_balances:
        group_id = account_groups.get(connector_name)
        if group_id is not None:
            members_by_group.setdefault(group_id, []).append(connector_name)
    duplicates: Dict[str, str] = {}
    for members in members_by_group.values():
        primary = next((name for name in members if connector_balances[name]), members[0])
        for connector_name in members:
            if connector_name != primary:
                duplicates[connector_name] = primary
    return duplicates


class PortfolioAnalyticsService:
    """
    Pure portfolio-distribution math over account state data.

    This service performs no IO: it has no database, gateway or connector dependencies. It operates on plain
    account-state dictionaries shaped as {account_name: {connector_name: [token_info, ...]}} where each
    token_info dict contains at least "token", "units" and "value" keys. Callers may pass a live dict; the
    methods snapshot it before iterating so concurrent mutations cannot affect the calculation.
    """

    def get_portfolio_distribution(self,
                                   accounts_state: Dict[str, Dict[str, List[Dict[str, Any]]]],
                                   account_name: Optional[str] = None,
                                   connector_names: Optional[List[str]] = None,
                                   account_groups: Optional[Dict[str, Dict[str, Optional[str]]]] = None
                                   ) -> Dict[str, Any]:
        """
        Get portfolio distribution by tokens with percentages.

        Args:
            accounts_state: Account state data shaped as {account_name: {connector_name: [token_info, ...]}}
            account_name: Optional account name to filter by (None aggregates all accounts)
            connector_names: Optional connectors to keep (None keeps all). Applied before shared wallets are
                folded, so a selected connector is always counted even when it shares its wallet with one
                that was not selected.
            account_groups: Optional {account_name: {connector_name: account id}} naming the exchange-side
                account each connector reads; connectors of one account with the same id are counted once.
        """
        try:
            # Snapshot the live dict so concurrent mutations cannot affect the iteration
            accounts_state_snapshot = {account: dict(connectors) for account, connectors in accounts_state.items()}

            # Get accounts to process
            accounts_to_process = [account_name] if account_name else list(accounts_state_snapshot.keys())

            # Aggregate all tokens across accounts and connectors
            token_values = {}
            total_value = 0

            for acc_name in accounts_to_process:
                if acc_name in accounts_state_snapshot:
                    selected = {
                        connector_name: connector_data
                        for connector_name, connector_data in accounts_state_snapshot[acc_name].items()
                        if not connector_names or connector_name in connector_names
                    }
                    duplicates = shared_wallet_connectors(selected, (account_groups or {}).get(acc_name))
                    for connector_name, connector_data in selected.items():
                        if connector_name in duplicates:
                            continue
                        for token_info in connector_data:
                            token = token_info.get("token", "")
                            value = token_info.get("value", 0)

                            if token not in token_values:
                                token_values[token] = {
                                    "token": token,
                                    "total_value": 0,
                                    "total_units": 0,
                                    "accounts": {}
                                }

                            token_values[token]["total_value"] += value
                            token_values[token]["total_units"] += token_info.get("units", 0)
                            total_value += value

                            # Track by account
                            if acc_name not in token_values[token]["accounts"]:
                                token_values[token]["accounts"][acc_name] = {
                                    "value": 0,
                                    "units": 0,
                                    "connectors": {}
                                }

                            token_values[token]["accounts"][acc_name]["value"] += value
                            token_values[token]["accounts"][acc_name]["units"] += token_info.get("units", 0)

                            # Track by connector within account
                            if connector_name not in token_values[token]["accounts"][acc_name]["connectors"]:
                                token_values[token]["accounts"][acc_name]["connectors"][connector_name] = {
                                    "value": 0,
                                    "units": 0
                                }

                            connector_totals = token_values[token]["accounts"][acc_name]["connectors"][connector_name]
                            connector_totals["value"] += value
                            connector_totals["units"] += token_info.get("units", 0)

            # Calculate percentages
            distribution = []
            for token_data in token_values.values():
                percentage = (token_data["total_value"] / total_value * 100) if total_value > 0 else 0

                token_dist = {
                    "token": token_data["token"],
                    "total_value": round(token_data["total_value"], 6),
                    "total_units": token_data["total_units"],
                    "percentage": round(percentage, 4),
                    "accounts": {}
                }

                # Add account-level percentages
                for acc_name, acc_data in token_data["accounts"].items():
                    acc_percentage = (acc_data["value"] / total_value * 100) if total_value > 0 else 0
                    token_dist["accounts"][acc_name] = {
                        "value": round(acc_data["value"], 6),
                        "units": acc_data["units"],
                        "percentage": round(acc_percentage, 4),
                        "connectors": {}
                    }

                    # Add connector-level data
                    for conn_name, conn_data in acc_data["connectors"].items():
                        token_dist["accounts"][acc_name]["connectors"][conn_name] = {
                            "value": round(conn_data["value"], 6),
                            "units": conn_data["units"]
                        }

                distribution.append(token_dist)

            # Sort by value (descending)
            distribution.sort(key=lambda x: x["total_value"], reverse=True)

            return {
                "total_portfolio_value": round(total_value, 6),
                "token_count": len(distribution),
                "distribution": distribution,
                "account_filter": account_name if account_name else "all_accounts"
            }

        except Exception as e:
            logger.error(f"Error calculating portfolio distribution: {e}")
            return {
                "total_portfolio_value": 0,
                "token_count": 0,
                "distribution": [],
                "account_filter": account_name if account_name else "all_accounts",
                "error": str(e)
            }

    def get_account_distribution(self,
                                 accounts_state: Dict[str, Dict[str, List[Dict[str, Any]]]],
                                 account_groups: Optional[Dict[str, Dict[str, Optional[str]]]] = None
                                 ) -> Dict[str, Any]:
        """
        Get portfolio distribution by accounts with percentages.

        Args:
            accounts_state: Account state data shaped as {account_name: {connector_name: [token_info, ...]}}
            account_groups: Optional {account_name: {connector_name: account id}} naming the exchange-side
                account each connector reads; connectors of one account with the same id are counted once.
        """
        try:
            # Snapshot the live dict so concurrent mutations cannot affect the iteration
            accounts_state_snapshot = {account: dict(connectors) for account, connectors in accounts_state.items()}

            account_values = {}
            total_value = 0

            for acc_name, account_data in accounts_state_snapshot.items():
                account_value = 0
                connector_values = {}

                duplicates = shared_wallet_connectors(account_data, (account_groups or {}).get(acc_name))
                for connector_name, connector_data in account_data.items():
                    if connector_name in duplicates:
                        continue
                    connector_value = 0
                    for token_info in connector_data:
                        value = token_info.get("value", 0)
                        connector_value += value
                        account_value += value

                    connector_values[connector_name] = round(connector_value, 6)

                account_values[acc_name] = {
                    "total_value": round(account_value, 6),
                    "connectors": connector_values
                }
                total_value += account_value

            # Calculate percentages
            distribution = []
            for acc_name, acc_data in account_values.items():
                percentage = (acc_data["total_value"] / total_value * 100) if total_value > 0 else 0

                connector_dist = {}
                for conn_name, conn_value in acc_data["connectors"].items():
                    conn_percentage = (conn_value / total_value * 100) if total_value > 0 else 0
                    connector_dist[conn_name] = {
                        "value": conn_value,
                        "percentage": round(conn_percentage, 4)
                    }

                distribution.append({
                    "account": acc_name,
                    "total_value": acc_data["total_value"],
                    "percentage": round(percentage, 4),
                    "connectors": connector_dist
                })

            # Sort by value (descending)
            distribution.sort(key=lambda x: x["total_value"], reverse=True)

            return {
                "total_portfolio_value": round(total_value, 6),
                "account_count": len(distribution),
                "distribution": distribution
            }

        except Exception as e:
            logger.error(f"Error calculating account distribution: {e}")
            return {
                "total_portfolio_value": 0,
                "account_count": 0,
                "distribution": [],
                "error": str(e)
            }
