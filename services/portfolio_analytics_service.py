import logging
from typing import Any, Dict, List, Optional

# Create module-specific logger
logger = logging.getLogger(__name__)


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
                                   account_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Get portfolio distribution by tokens with percentages.

        Args:
            accounts_state: Account state data shaped as {account_name: {connector_name: [token_info, ...]}}
            account_name: Optional account name to filter by (None aggregates all accounts)
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
                    for connector_name, connector_data in accounts_state_snapshot[acc_name].items():
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

    def get_account_distribution(self, accounts_state: Dict[str, Dict[str, List[Dict[str, Any]]]]) -> Dict[str, Any]:
        """
        Get portfolio distribution by accounts with percentages.

        Args:
            accounts_state: Account state data shaped as {account_name: {connector_name: [token_info, ...]}}
        """
        try:
            # Snapshot the live dict so concurrent mutations cannot affect the iteration
            accounts_state_snapshot = {account: dict(connectors) for account, connectors in accounts_state.items()}

            account_values = {}
            total_value = 0

            for acc_name, account_data in accounts_state_snapshot.items():
                account_value = 0
                connector_values = {}

                for connector_name, connector_data in account_data.items():
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
