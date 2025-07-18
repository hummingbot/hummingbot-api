from sqlalchemy import (
    TIMESTAMP,
    Column,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship

Base = declarative_base()


class AccountState(Base):
    __tablename__ = "account_states"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False, index=True)
    account_name = Column(String, nullable=False, index=True)
    connector_name = Column(String, nullable=False, index=True)
    
    token_states = relationship("TokenState", back_populates="account_state", cascade="all, delete-orphan")


class TokenState(Base):
    __tablename__ = "token_states"

    id = Column(Integer, primary_key=True, index=True)
    account_state_id = Column(Integer, ForeignKey("account_states.id"), nullable=False)
    token = Column(String, nullable=False, index=True)
    units = Column(Numeric(precision=30, scale=18), nullable=False)
    price = Column(Numeric(precision=30, scale=18), nullable=False)
    value = Column(Numeric(precision=30, scale=18), nullable=False)
    available_units = Column(Numeric(precision=30, scale=18), nullable=False)
    
    account_state = relationship("AccountState", back_populates="token_states")


class Order(Base):
    __tablename__ = "orders"
    
    id = Column(Integer, primary_key=True, index=True)
    # Order identification
    client_order_id = Column(String, nullable=False, unique=True, index=True)
    exchange_order_id = Column(String, nullable=True, index=True)
    
    # Timestamps
    created_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False, index=True)
    updated_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    
    # Account and connector info
    account_name = Column(String, nullable=False, index=True)
    connector_name = Column(String, nullable=False, index=True)
    
    # Order details
    trading_pair = Column(String, nullable=False, index=True)
    trade_type = Column(String, nullable=False)  # BUY, SELL
    order_type = Column(String, nullable=False)  # LIMIT, MARKET, LIMIT_MAKER
    amount = Column(Numeric(precision=30, scale=18), nullable=False)
    price = Column(Numeric(precision=30, scale=18), nullable=True)  # Null for market orders
    
    # Order status and execution
    status = Column(String, nullable=False, default="SUBMITTED", index=True)  # SUBMITTED, OPEN, FILLED, CANCELLED, FAILED
    filled_amount = Column(Numeric(precision=30, scale=18), nullable=False, default=0)
    average_fill_price = Column(Numeric(precision=30, scale=18), nullable=True)
    
    # Fee information
    fee_paid = Column(Numeric(precision=30, scale=18), default=0, nullable=True)
    fee_currency = Column(String, nullable=True)
    
    # Additional metadata
    error_message = Column(Text, nullable=True)
    
    # Relationships for future enhancements
    trades = relationship("Trade", back_populates="order", cascade="all, delete-orphan")


class Trade(Base):
    __tablename__ = "trades"
    
    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    
    # Trade identification
    trade_id = Column(String, nullable=False, unique=True, index=True)
    
    # Timestamps
    timestamp = Column(TIMESTAMP(timezone=True), nullable=False, index=True)
    
    # Trade details
    trading_pair = Column(String, nullable=False, index=True)
    trade_type = Column(String, nullable=False)  # BUY, SELL
    amount = Column(Numeric(precision=30, scale=18), nullable=False)
    price = Column(Numeric(precision=30, scale=18), nullable=False)
    
    # Fee information
    fee_paid = Column(Numeric(precision=30, scale=18), nullable=False, default=0)
    fee_currency = Column(String, nullable=True)
    
    # Relationship
    order = relationship("Order", back_populates="trades")


class PositionSnapshot(Base):
    __tablename__ = "position_snapshots"
    
    id = Column(Integer, primary_key=True, index=True)
    
    # Position identification
    account_name = Column(String, nullable=False, index=True)
    connector_name = Column(String, nullable=False, index=True)
    trading_pair = Column(String, nullable=False, index=True)
    
    # Timestamps
    timestamp = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False, index=True)
    
    # Real-time exchange data (from connector.account_positions)
    side = Column(String, nullable=False)  # LONG, SHORT
    exchange_size = Column(Numeric(precision=30, scale=18), nullable=False)  # Size from exchange
    entry_price = Column(Numeric(precision=30, scale=18), nullable=True)  # Average entry price
    mark_price = Column(Numeric(precision=30, scale=18), nullable=True)  # Current mark price
    
    # Real-time PnL data (can't be derived from trades alone)
    unrealized_pnl = Column(Numeric(precision=30, scale=18), nullable=True)  # From exchange
    percentage_pnl = Column(Numeric(precision=10, scale=6), nullable=True)  # PnL percentage
    
    # Leverage and margin info
    leverage = Column(Numeric(precision=10, scale=2), nullable=True)  # Position leverage
    initial_margin = Column(Numeric(precision=30, scale=18), nullable=True)  # Initial margin
    maintenance_margin = Column(Numeric(precision=30, scale=18), nullable=True)  # Maintenance margin
    
    # Fee tracking (exchange provides cumulative data)
    cumulative_funding_fees = Column(Numeric(precision=30, scale=18), nullable=False, default=0)  # Funding fees
    fee_currency = Column(String, nullable=True)  # Fee currency (usually USDT)
    
    # Reconciliation fields (calculated from our trade data)
    calculated_size = Column(Numeric(precision=30, scale=18), nullable=True)  # Size from our trades
    calculated_entry_price = Column(Numeric(precision=30, scale=18), nullable=True)  # Entry from our trades
    size_difference = Column(Numeric(precision=30, scale=18), nullable=True)  # Difference for reconciliation
    
    # Additional metadata
    exchange_position_id = Column(String, nullable=True, index=True)  # Exchange position ID
    is_reconciled = Column(String, nullable=False, default="PENDING")  # RECONCILED, MISMATCH, PENDING


class FundingPayment(Base):
    __tablename__ = "funding_payments"
    
    id = Column(Integer, primary_key=True, index=True)
    
    # Payment identification
    funding_payment_id = Column(String, nullable=False, unique=True, index=True)
    
    # Timestamps
    timestamp = Column(TIMESTAMP(timezone=True), nullable=False, index=True)
    
    # Account and connector info
    account_name = Column(String, nullable=False, index=True)
    connector_name = Column(String, nullable=False, index=True)
    
    # Funding details
    trading_pair = Column(String, nullable=False, index=True)
    funding_rate = Column(Numeric(precision=20, scale=18), nullable=False)  # Funding rate
    funding_payment = Column(Numeric(precision=30, scale=18), nullable=False)  # Payment amount
    fee_currency = Column(String, nullable=False)  # Payment currency (usually USDT)
    
    # Position association
    position_size = Column(Numeric(precision=30, scale=18), nullable=True)  # Position size at time of payment
    position_side = Column(String, nullable=True)  # LONG, SHORT
    
    # Additional metadata
    exchange_funding_id = Column(String, nullable=True, index=True)  # Exchange funding ID


class BotRun(Base):
    __tablename__ = "bot_runs"
    
    id = Column(Integer, primary_key=True, index=True)
    
    # Bot identification
    bot_name = Column(String, nullable=False, index=True)
    instance_name = Column(String, nullable=False, index=True)
    
    # Deployment info
    deployed_at = Column(TIMESTAMP(timezone=True), server_default=func.now(), nullable=False, index=True)
    strategy_type = Column(String, nullable=False, index=True)  # 'script' or 'controller'
    strategy_name = Column(String, nullable=False, index=True)
    config_name = Column(String, nullable=True, index=True)
    
    # Runtime tracking
    stopped_at = Column(TIMESTAMP(timezone=True), nullable=True, index=True)
    
    # Status tracking
    deployment_status = Column(String, nullable=False, default="DEPLOYED", index=True)  # DEPLOYED, FAILED, ARCHIVED
    run_status = Column(String, nullable=False, default="CREATED", index=True)  # CREATED, RUNNING, STOPPED, ERROR
    
    # Configuration and final state
    deployment_config = Column(Text, nullable=True)  # JSON of full deployment config
    final_status = Column(Text, nullable=True)  # JSON of final bot state, performance, etc.
    
    # Account info
    account_name = Column(String, nullable=False, index=True)
    
    # Metadata
    image_version = Column(String, nullable=True, index=True)
    error_message = Column(Text, nullable=True)


