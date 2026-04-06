"""Initial schema — init_db.sql 기반 베이스라인

이 마이그레이션은 기존 init_db.sql과 동일한 스키마를 Alembic으로 관리하기 위한
베이스라인이다. 이미 init_db.sql로 생성된 DB에서는 `alembic stamp head`로
현재 상태를 마킹만 하면 된다.

신규 DB에서는 `alembic upgrade head`로 전체 스키마를 생성한다.

Revision ID: 001
Create Date: 2026-04-07
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """init_db.sql에 대응하는 스키마 생성"""

    # TimescaleDB 확장
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE")

    # ── market_ohlcv (TimescaleDB Hypertable) ──
    op.create_table(
        "market_ohlcv",
        sa.Column("time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ticker", sa.String(20), nullable=False),
        sa.Column("market", sa.String(10), nullable=False),
        sa.Column("open", sa.Numeric(18, 4), nullable=False),
        sa.Column("high", sa.Numeric(18, 4), nullable=False),
        sa.Column("low", sa.Numeric(18, 4), nullable=False),
        sa.Column("close", sa.Numeric(18, 4), nullable=False),
        sa.Column("volume", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("interval", sa.String(10), nullable=False, server_default="1d"),
        sa.PrimaryKeyConstraint("time", "ticker", "interval"),
    )
    op.execute("SELECT create_hypertable('market_ohlcv', 'time', if_not_exists => TRUE)")
    op.create_index("idx_ohlcv_ticker_time", "market_ohlcv", ["ticker", sa.text("time DESC")])
    op.create_index("idx_ohlcv_market", "market_ohlcv", ["market", sa.text("time DESC")])

    # ── economic_indicators (TimescaleDB Hypertable) ──
    op.create_table(
        "economic_indicators",
        sa.Column("time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("indicator_code", sa.String(50), nullable=False),
        sa.Column("indicator_name", sa.String(200), nullable=False),
        sa.Column("value", sa.Numeric(18, 6)),
        sa.Column("country", sa.String(5), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.PrimaryKeyConstraint("time", "indicator_code"),
    )
    op.execute("SELECT create_hypertable('economic_indicators', 'time', if_not_exists => TRUE)")

    # ── financial_statements ──
    op.create_table(
        "financial_statements",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("ticker", sa.String(20), nullable=False),
        sa.Column("market", sa.String(10), nullable=False),
        sa.Column("report_date", sa.Date, nullable=False),
        sa.Column("period_type", sa.String(10), nullable=False),
        sa.Column("revenue", sa.Numeric(18, 2)),
        sa.Column("operating_income", sa.Numeric(18, 2)),
        sa.Column("net_income", sa.Numeric(18, 2)),
        sa.Column("total_assets", sa.Numeric(18, 2)),
        sa.Column("total_liabilities", sa.Numeric(18, 2)),
        sa.Column("total_equity", sa.Numeric(18, 2)),
        sa.Column("eps", sa.Numeric(18, 4)),
        sa.Column("bps", sa.Numeric(18, 4)),
        sa.Column("dps", sa.Numeric(18, 4)),
        sa.Column("dividend_yield", sa.Numeric(8, 4)),
        sa.Column(
            "accounting_standard",
            sa.String(10),
            nullable=False,
            server_default="K-IFRS",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.UniqueConstraint("ticker", "report_date", "period_type"),
    )
    op.create_index(
        "idx_fin_ticker",
        "financial_statements",
        ["ticker", sa.text("report_date DESC")],
    )

    # ── exchange_rates (TimescaleDB Hypertable) ──
    op.create_table(
        "exchange_rates",
        sa.Column("time", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "currency_pair",
            sa.String(10),
            nullable=False,
            server_default="USD/KRW",
        ),
        sa.Column("rate", sa.Numeric(12, 4), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.PrimaryKeyConstraint("time", "currency_pair"),
    )
    op.execute("SELECT create_hypertable('exchange_rates', 'time', if_not_exists => TRUE)")

    # ── user_profiles ──
    op.create_table(
        "user_profiles",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "investment_types",
            sa.ARRAY(sa.Text),
            nullable=False,
            server_default="{STOCK,ETF}",
        ),
        sa.Column(
            "risk_profile",
            sa.String(20),
            nullable=False,
            server_default="BALANCED",
        ),
        sa.Column("seed_amount", sa.Numeric(18, 2), nullable=False),
        sa.Column(
            "investment_goal",
            sa.String(30),
            nullable=False,
            server_default="WEALTH_GROWTH",
        ),
        sa.Column(
            "investment_style",
            sa.String(20),
            nullable=False,
            server_default="DISCRETIONARY",
        ),
        sa.Column(
            "loss_tolerance",
            sa.Numeric(5, 4),
            nullable=False,
            server_default="-0.10",
        ),
        sa.Column("sector_filter", sa.ARRAY(sa.Text)),
        sa.Column("designated_tickers", sa.ARRAY(sa.Text)),
        sa.Column(
            "rebalancing_frequency",
            sa.String(20),
            nullable=False,
            server_default="MONTHLY",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )

    # ── universe ──
    op.create_table(
        "universe",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("ticker", sa.String(20), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("market", sa.String(10), nullable=False),
        sa.Column("country", sa.String(5), nullable=False),
        sa.Column("asset_type", sa.String(10), nullable=False),
        sa.Column("sector", sa.String(100)),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("excluded_reason", sa.String(200)),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.UniqueConstraint("ticker", "market"),
    )
    op.create_index("idx_universe_active", "universe", ["is_active", "market"])

    # ── portfolio_holdings ──
    op.create_table(
        "portfolio_holdings",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("ticker", sa.String(20), nullable=False),
        sa.Column("market", sa.String(10), nullable=False),
        sa.Column("quantity", sa.Integer, nullable=False, server_default="0"),
        sa.Column("avg_price", sa.Numeric(18, 4), nullable=False),
        sa.Column("current_price", sa.Numeric(18, 4)),
        sa.Column("target_weight", sa.Numeric(5, 4)),
        sa.Column("actual_weight", sa.Numeric(5, 4)),
        sa.Column("unrealized_pnl", sa.Numeric(18, 2)),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.UniqueConstraint("ticker", "market"),
    )

    # ── portfolio_snapshots (TimescaleDB Hypertable) ──
    op.create_table(
        "portfolio_snapshots",
        sa.Column("time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("total_value", sa.Numeric(18, 2), nullable=False),
        sa.Column("invested_amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("cash_balance", sa.Numeric(18, 2), nullable=False),
        sa.Column("daily_return", sa.Numeric(10, 6)),
        sa.Column("cumulative_return", sa.Numeric(10, 6)),
        sa.Column("benchmark_return", sa.Numeric(10, 6)),
        sa.Column("holdings_json", sa.JSON),
        sa.PrimaryKeyConstraint("time"),
    )
    op.execute("SELECT create_hypertable('portfolio_snapshots', 'time', if_not_exists => TRUE)")

    # ── orders ──
    op.create_table(
        "orders",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("order_id", sa.String(50), unique=True),
        sa.Column("ticker", sa.String(20), nullable=False),
        sa.Column("market", sa.String(10), nullable=False),
        sa.Column("side", sa.String(10), nullable=False),
        sa.Column("order_type", sa.String(10), nullable=False),
        sa.Column("quantity", sa.Integer, nullable=False),
        sa.Column("price", sa.Numeric(18, 4)),
        sa.Column("filled_quantity", sa.Integer, nullable=False, server_default="0"),
        sa.Column("filled_price", sa.Numeric(18, 4)),
        sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column(
            "trigger_type",
            sa.String(20),
            nullable=False,
            server_default="MANUAL",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("filled_at", sa.DateTime(timezone=True)),
        sa.Column("error_message", sa.Text),
    )
    op.create_index("idx_orders_status", "orders", ["status", sa.text("created_at DESC")])
    op.create_index("idx_orders_ticker", "orders", ["ticker", sa.text("created_at DESC")])

    # ── alerts ──
    op.create_table(
        "alerts",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("alert_type", sa.String(30), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("message", sa.Text, nullable=False),
        sa.Column("is_read", sa.Boolean, nullable=False, server_default="false"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index("idx_alerts_created", "alerts", [sa.text("created_at DESC")])
    op.create_index("idx_alerts_unread", "alerts", ["is_read", sa.text("created_at DESC")])

    # ── audit_logs (TimescaleDB Hypertable) ──
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer, autoincrement=True),
        sa.Column(
            "time",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("action_type", sa.String(50), nullable=False),
        sa.Column("module", sa.String(50), nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("before_state", sa.JSON),
        sa.Column("after_state", sa.JSON),
        sa.Column("metadata", sa.JSON),
        sa.PrimaryKeyConstraint("id", "time"),
    )
    op.execute("SELECT create_hypertable('audit_logs', 'time', if_not_exists => TRUE)")

    # ── business_calendars ──
    op.create_table(
        "business_calendars",
        sa.Column("date", sa.Date, nullable=False),
        sa.Column("market", sa.String(10), nullable=False),
        sa.Column("is_business_day", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("holiday_name", sa.String(100)),
        sa.PrimaryKeyConstraint("date", "market"),
    )

    # ── backtest_results ──
    op.create_table(
        "backtest_results",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("strategy_name", sa.String(100), nullable=False),
        sa.Column("start_date", sa.Date, nullable=False),
        sa.Column("end_date", sa.Date, nullable=False),
        sa.Column("cagr", sa.Numeric(8, 4)),
        sa.Column("mdd", sa.Numeric(8, 4)),
        sa.Column("sharpe_ratio", sa.Numeric(8, 4)),
        sa.Column("sortino_ratio", sa.Numeric(8, 4)),
        sa.Column("calmar_ratio", sa.Numeric(8, 4)),
        sa.Column("win_rate", sa.Numeric(8, 4)),
        sa.Column("profit_factor", sa.Numeric(8, 4)),
        sa.Column("total_trades", sa.Integer),
        sa.Column("config_json", sa.JSON),
        sa.Column("equity_curve", sa.JSON),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )

    # ── strategy_weights ──
    op.create_table(
        "strategy_weights",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("strategy_type", sa.String(30), nullable=False),
        sa.Column("weight", sa.Numeric(5, 4), nullable=False, server_default="0.0"),
        sa.Column("risk_profile", sa.String(20), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.UniqueConstraint("strategy_type", "risk_profile"),
    )
    # 초기 앙상블 가중치
    op.execute(
        """
        INSERT INTO strategy_weights (strategy_type, weight, risk_profile) VALUES
            ('FACTOR', 0.25, 'BALANCED'),
            ('MEAN_REVERSION', 0.10, 'BALANCED'),
            ('TREND_FOLLOWING', 0.20, 'BALANCED'),
            ('RISK_PARITY', 0.20, 'BALANCED'),
            ('ML_SIGNAL', 0.15, 'BALANCED'),
            ('SENTIMENT', 0.10, 'BALANCED')
        ON CONFLICT DO NOTHING
        """
    )

    # ── sentiment_scores (TimescaleDB Hypertable) ──
    op.create_table(
        "sentiment_scores",
        sa.Column("id", sa.Integer, autoincrement=True),
        sa.Column(
            "time",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("ticker", sa.String(20), nullable=False),
        sa.Column("score", sa.Numeric(5, 4), nullable=False),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=False, server_default="0.0"),
        sa.Column("summary", sa.Text),
        sa.Column("positive_factors", sa.JSON, server_default="'[]'"),
        sa.Column("negative_factors", sa.JSON, server_default="'[]'"),
        sa.Column("news_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("model_used", sa.String(50), nullable=False),
        sa.PrimaryKeyConstraint("id", "time"),
    )
    op.execute("SELECT create_hypertable('sentiment_scores', 'time', if_not_exists => TRUE)")
    op.create_index(
        "idx_sentiment_ticker",
        "sentiment_scores",
        ["ticker", sa.text("time DESC")],
    )

    # ── investment_opinions (TimescaleDB Hypertable) ──
    op.create_table(
        "investment_opinions",
        sa.Column("id", sa.Integer, autoincrement=True),
        sa.Column(
            "time",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("ticker", sa.String(20)),
        sa.Column(
            "opinion_type",
            sa.String(20),
            nullable=False,
            server_default="STOCK",
        ),
        sa.Column("action", sa.String(20), nullable=False),
        sa.Column(
            "conviction",
            sa.Numeric(5, 4),
            nullable=False,
            server_default="0.0",
        ),
        sa.Column("target_weight", sa.Numeric(5, 4)),
        sa.Column("reasoning", sa.Text, nullable=False),
        sa.Column("market_context", sa.Text),
        sa.Column("risk_factors", sa.JSON, server_default="'[]'"),
        sa.Column("model_used", sa.String(50), nullable=False),
        sa.PrimaryKeyConstraint("id", "time"),
    )
    op.execute("SELECT create_hypertable('investment_opinions', 'time', if_not_exists => TRUE)")
    op.create_index(
        "idx_opinion_ticker",
        "investment_opinions",
        ["ticker", sa.text("time DESC")],
    )

    # ── ensemble_signals (TimescaleDB Hypertable) ──
    op.create_table(
        "ensemble_signals",
        sa.Column("id", sa.Integer, autoincrement=True),
        sa.Column(
            "time",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("ticker", sa.String(20), nullable=False),
        sa.Column("final_signal", sa.Numeric(5, 4), nullable=False),
        sa.Column(
            "final_confidence",
            sa.Numeric(5, 4),
            nullable=False,
            server_default="0.0",
        ),
        sa.Column("component_signals", sa.JSON, nullable=False),
        sa.Column("weights_used", sa.JSON, nullable=False),
        sa.Column("risk_profile", sa.String(20), nullable=False),
        sa.PrimaryKeyConstraint("id", "time"),
    )
    op.execute("SELECT create_hypertable('ensemble_signals', 'time', if_not_exists => TRUE)")
    op.create_index(
        "idx_ensemble_ticker",
        "ensemble_signals",
        ["ticker", sa.text("time DESC")],
    )

    # ── weight_update_history ──
    op.create_table(
        "weight_update_history",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("risk_profile", sa.String(20), nullable=False),
        sa.Column("old_weights", sa.JSON, nullable=False),
        sa.Column("new_weights", sa.JSON, nullable=False),
        sa.Column("method", sa.String(30), nullable=False),
        sa.Column("performance_metrics", sa.JSON),
        sa.Column("reason", sa.Text),
    )


def downgrade() -> None:
    """전체 스키마 롤백 (역순 삭제)"""
    tables = [
        "weight_update_history",
        "ensemble_signals",
        "investment_opinions",
        "sentiment_scores",
        "strategy_weights",
        "backtest_results",
        "business_calendars",
        "audit_logs",
        "alerts",
        "orders",
        "portfolio_snapshots",
        "portfolio_holdings",
        "universe",
        "user_profiles",
        "exchange_rates",
        "financial_statements",
        "economic_indicators",
        "market_ohlcv",
    ]
    for table in tables:
        op.drop_table(table)
