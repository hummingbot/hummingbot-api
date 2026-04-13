"""
Pydantic models for the portfolio router.

These models define the request/response schemas for portfolio-related endpoints.
"""

from typing import Dict, List, Optional, Any
from datetime import datetime
from pydantic import BaseModel, Field


class TokenBalance(BaseModel):
    """Token balance information"""
    token: str = Field(description="Token symbol")
    units: float = Field(description="Number of units held")
    price: float = Field(description="Current price per unit")
    value: float = Field(description="Total value (units * price)")
    available_units: float = Field(description="Available units (not locked in orders)")


class ConnectorBalances(BaseModel):
    """Balances for a specific connector"""
    connector_name: str = Field(description="Name of the connector")
    balances: List[TokenBalance] = Field(description="List of token balances")
    total_value: float = Field(description="Total value across all tokens")


class AccountPortfolioState(BaseModel):
    """Portfolio state for a single account"""
    account_name: str = Field(description="Name of the account")
    connectors: Dict[str, List[TokenBalance]] = Field(description="Balances by connector")
    total_value: float = Field(description="Total account value across all connectors")
    last_updated: Optional[datetime] = Field(default=None, description="Last update timestamp")


class PortfolioStateResponse(BaseModel):
    """Response for portfolio state endpoint"""
    accounts: Dict[str, Dict[str, List[Dict[str, Any]]]] = Field(
        description="Portfolio state by account and connector"
    )
    total_portfolio_value: Optional[float] = Field(default=None, description="Total value across all accounts")
    timestamp: datetime = Field(default_factory=datetime.utcnow, description="Response timestamp")


class TokenDistribution(BaseModel):
    """Token distribution information"""
    token: str = Field(description="Token symbol")
    total_value: float = Field(description="Total value of this token")
    total_units: float = Field(description="Total units of this token")
    percentage: float = Field(description="Percentage of total portfolio")
    accounts: Dict[str, Dict[str, Any]] = Field(
        description="Breakdown by account and connector"
    )


class PortfolioDistributionResponse(BaseModel):
    """Response for portfolio distribution endpoint"""
    total_portfolio_value: float = Field(description="Total portfolio value")
    token_count: int = Field(description="Number of unique tokens")
    distribution: List[TokenDistribution] = Field(description="Token distribution list")
    account_filter: str = Field(
        default="all_accounts",
        description="Applied account filter (all_accounts or specific accounts)"
    )


class AccountDistribution(BaseModel):
    """Account distribution information"""
    account: str = Field(description="Account name")
    total_value: float = Field(description="Total value in this account")
    percentage: float = Field(description="Percentage of total portfolio")
    connectors: Dict[str, Dict[str, float]] = Field(
        description="Value breakdown by connector"
    )


class AccountsDistributionResponse(BaseModel):
    """Response for accounts distribution endpoint"""
    total_portfolio_value: float = Field(description="Total portfolio value")
    account_count: int = Field(description="Number of accounts")
    distribution: List[AccountDistribution] = Field(description="Account distribution list")


class HistoricalPortfolioState(BaseModel):
    """Historical portfolio state entry"""
    timestamp: datetime = Field(description="State timestamp")
    state: Dict[str, Dict[str, List[Dict[str, Any]]]] = Field(
        description="Portfolio state snapshot"
    )
    total_value: Optional[float] = Field(default=None, description="Total value at this point")


class PortfolioHistoryFilters(BaseModel):
    """Filters applied to portfolio history query"""
    account_names: Optional[List[str]] = Field(default=None, description="Filtered account names")
    start_time: Optional[datetime] = Field(default=None, description="Start time filter")
    end_time: Optional[datetime] = Field(default=None, description="End time filter")