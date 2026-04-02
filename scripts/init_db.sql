-- ══════════════════════════════════════
-- AQTS Database Initialization Script
-- TimescaleDB 확장 활성화 및 스키마 생성
-- ══════════════════════════════════════

-- TimescaleDB 확장 활성화
CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;

-- ══════════════════════════════════════
-- 시세 데이터 (TimescaleDB Hypertable)
-- ══════════════════════════════════════
CREATE TABLE IF NOT EXISTS market_ohlcv (
    time        TIMESTAMPTZ NOT NULL,
    ticker      VARCHAR(20) NOT NULL,
    market      VARCHAR(10) NOT NULL,
    open        NUMERIC(18, 4) NOT NULL,
    high        NUMERIC(18, 4) NOT NULL,
    low         NUMERIC(18, 4) NOT NULL,
    close       NUMERIC(18, 4) NOT NULL,
    volume      BIGINT NOT NULL DEFAULT 0,
    interval    VARCHAR(10) NOT NULL DEFAULT '1d',
    PRIMARY KEY (time, ticker, interval)
);

SELECT create_hypertable('market_ohlcv', 'time', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_ohlcv_ticker_time ON market_ohlcv (ticker, time DESC);
CREATE INDEX IF NOT EXISTS idx_ohlcv_market ON market_ohlcv (market, time DESC);

-- ══════════════════════════════════════
-- 경제지표 데이터
-- ══════════════════════════════════════
CREATE TABLE IF NOT EXISTS economic_indicators (
    time            TIMESTAMPTZ NOT NULL,
    indicator_code  VARCHAR(50) NOT NULL,
    indicator_name  VARCHAR(200) NOT NULL,
    value           NUMERIC(18, 6),
    country         VARCHAR(5) NOT NULL,
    source          VARCHAR(20) NOT NULL,
    PRIMARY KEY (time, indicator_code)
);

SELECT create_hypertable('economic_indicators', 'time', if_not_exists => TRUE);

-- ══════════════════════════════════════
-- 재무제표 데이터
-- ══════════════════════════════════════
CREATE TABLE IF NOT EXISTS financial_statements (
    id              SERIAL,
    ticker          VARCHAR(20) NOT NULL,
    market          VARCHAR(10) NOT NULL,
    report_date     DATE NOT NULL,
    period_type     VARCHAR(10) NOT NULL,
    revenue         NUMERIC(18, 2),
    operating_income NUMERIC(18, 2),
    net_income      NUMERIC(18, 2),
    total_assets    NUMERIC(18, 2),
    total_liabilities NUMERIC(18, 2),
    total_equity    NUMERIC(18, 2),
    eps             NUMERIC(18, 4),
    bps             NUMERIC(18, 4),
    dps             NUMERIC(18, 4),
    dividend_yield  NUMERIC(8, 4),
    accounting_standard VARCHAR(10) NOT NULL DEFAULT 'K-IFRS',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (id),
    UNIQUE (ticker, report_date, period_type)
);

CREATE INDEX IF NOT EXISTS idx_fin_ticker ON financial_statements (ticker, report_date DESC);

-- ══════════════════════════════════════
-- 환율 데이터
-- ══════════════════════════════════════
CREATE TABLE IF NOT EXISTS exchange_rates (
    time            TIMESTAMPTZ NOT NULL,
    currency_pair   VARCHAR(10) NOT NULL DEFAULT 'USD/KRW',
    rate            NUMERIC(12, 4) NOT NULL,
    source          VARCHAR(20) NOT NULL,
    PRIMARY KEY (time, currency_pair)
);

SELECT create_hypertable('exchange_rates', 'time', if_not_exists => TRUE);

-- ══════════════════════════════════════
-- 사용자 프로필
-- ══════════════════════════════════════
CREATE TABLE IF NOT EXISTS user_profiles (
    id              SERIAL PRIMARY KEY,
    investment_types TEXT[] NOT NULL DEFAULT '{STOCK,ETF}',
    risk_profile    VARCHAR(20) NOT NULL DEFAULT 'BALANCED',
    seed_amount     NUMERIC(18, 2) NOT NULL,
    investment_goal VARCHAR(30) NOT NULL DEFAULT 'WEALTH_GROWTH',
    investment_style VARCHAR(20) NOT NULL DEFAULT 'DISCRETIONARY',
    loss_tolerance  NUMERIC(5, 4) NOT NULL DEFAULT -0.10,
    sector_filter   TEXT[],
    designated_tickers TEXT[],
    rebalancing_frequency VARCHAR(20) NOT NULL DEFAULT 'MONTHLY',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ══════════════════════════════════════
-- 유니버스 관리
-- ══════════════════════════════════════
CREATE TABLE IF NOT EXISTS universe (
    id              SERIAL PRIMARY KEY,
    ticker          VARCHAR(20) NOT NULL,
    name            VARCHAR(200) NOT NULL,
    market          VARCHAR(10) NOT NULL,
    country         VARCHAR(5) NOT NULL,
    asset_type      VARCHAR(10) NOT NULL,
    sector          VARCHAR(100),
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    excluded_reason VARCHAR(200),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (ticker, market)
);

CREATE INDEX IF NOT EXISTS idx_universe_active ON universe (is_active, market);

-- ══════════════════════════════════════
-- 포트폴리오 현황
-- ══════════════════════════════════════
CREATE TABLE IF NOT EXISTS portfolio_holdings (
    id              SERIAL PRIMARY KEY,
    ticker          VARCHAR(20) NOT NULL,
    market          VARCHAR(10) NOT NULL,
    quantity        INTEGER NOT NULL DEFAULT 0,
    avg_price       NUMERIC(18, 4) NOT NULL,
    current_price   NUMERIC(18, 4),
    target_weight   NUMERIC(5, 4),
    actual_weight   NUMERIC(5, 4),
    unrealized_pnl  NUMERIC(18, 2),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (ticker, market)
);

-- ══════════════════════════════════════
-- 포트폴리오 이력 (일별 스냅샷)
-- ══════════════════════════════════════
CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    time                TIMESTAMPTZ NOT NULL,
    total_value         NUMERIC(18, 2) NOT NULL,
    invested_amount     NUMERIC(18, 2) NOT NULL,
    cash_balance        NUMERIC(18, 2) NOT NULL,
    daily_return        NUMERIC(10, 6),
    cumulative_return   NUMERIC(10, 6),
    benchmark_return    NUMERIC(10, 6),
    holdings_json       JSONB,
    PRIMARY KEY (time)
);

SELECT create_hypertable('portfolio_snapshots', 'time', if_not_exists => TRUE);

-- ══════════════════════════════════════
-- 주문 이력
-- ══════════════════════════════════════
CREATE TABLE IF NOT EXISTS orders (
    id              SERIAL PRIMARY KEY,
    order_id        VARCHAR(50) UNIQUE,
    ticker          VARCHAR(20) NOT NULL,
    market          VARCHAR(10) NOT NULL,
    side            VARCHAR(10) NOT NULL,
    order_type      VARCHAR(10) NOT NULL,
    quantity        INTEGER NOT NULL,
    price           NUMERIC(18, 4),
    filled_quantity INTEGER NOT NULL DEFAULT 0,
    filled_price    NUMERIC(18, 4),
    status          VARCHAR(20) NOT NULL DEFAULT 'PENDING',
    trigger_type    VARCHAR(20) NOT NULL DEFAULT 'MANUAL',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    filled_at       TIMESTAMPTZ,
    error_message   TEXT
);

CREATE INDEX IF NOT EXISTS idx_orders_status ON orders (status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_orders_ticker ON orders (ticker, created_at DESC);

-- ══════════════════════════════════════
-- 알림 이력
-- ══════════════════════════════════════
CREATE TABLE IF NOT EXISTS alerts (
    id              SERIAL PRIMARY KEY,
    alert_type      VARCHAR(30) NOT NULL,
    title           VARCHAR(200) NOT NULL,
    message         TEXT NOT NULL,
    is_read         BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_alerts_created ON alerts (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_unread ON alerts (is_read, created_at DESC);

-- ══════════════════════════════════════
-- 감사 로그 (Audit Trail)
-- ══════════════════════════════════════
CREATE TABLE IF NOT EXISTS audit_logs (
    id              SERIAL,
    time            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    action_type     VARCHAR(50) NOT NULL,
    module          VARCHAR(50) NOT NULL,
    description     TEXT NOT NULL,
    before_state    JSONB,
    after_state     JSONB,
    metadata        JSONB,
    PRIMARY KEY (id, time)
);

SELECT create_hypertable('audit_logs', 'time', if_not_exists => TRUE);

-- ══════════════════════════════════════
-- 영업일 캘린더
-- ══════════════════════════════════════
CREATE TABLE IF NOT EXISTS business_calendars (
    date            DATE NOT NULL,
    market          VARCHAR(10) NOT NULL,
    is_business_day BOOLEAN NOT NULL DEFAULT TRUE,
    holiday_name    VARCHAR(100),
    PRIMARY KEY (date, market)
);

-- ══════════════════════════════════════
-- 백테스트 결과
-- ══════════════════════════════════════
CREATE TABLE IF NOT EXISTS backtest_results (
    id              SERIAL PRIMARY KEY,
    strategy_name   VARCHAR(100) NOT NULL,
    start_date      DATE NOT NULL,
    end_date        DATE NOT NULL,
    cagr            NUMERIC(8, 4),
    mdd             NUMERIC(8, 4),
    sharpe_ratio    NUMERIC(8, 4),
    sortino_ratio   NUMERIC(8, 4),
    calmar_ratio    NUMERIC(8, 4),
    win_rate        NUMERIC(8, 4),
    profit_factor   NUMERIC(8, 4),
    total_trades    INTEGER,
    config_json     JSONB,
    equity_curve    JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ══════════════════════════════════════
-- 전략 가중치 (앙상블 가중치 관리)
-- ══════════════════════════════════════
CREATE TABLE IF NOT EXISTS strategy_weights (
    id              SERIAL PRIMARY KEY,
    strategy_type   VARCHAR(30) NOT NULL,
    weight          NUMERIC(5, 4) NOT NULL DEFAULT 0.0,
    risk_profile    VARCHAR(20) NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (strategy_type, risk_profile)
);

-- 초기 가중치 삽입
INSERT INTO strategy_weights (strategy_type, weight, risk_profile) VALUES
    ('FACTOR', 0.25, 'BALANCED'),
    ('MEAN_REVERSION', 0.10, 'BALANCED'),
    ('TREND_FOLLOWING', 0.20, 'BALANCED'),
    ('RISK_PARITY', 0.20, 'BALANCED'),
    ('ML_SIGNAL', 0.15, 'BALANCED'),
    ('SENTIMENT', 0.10, 'BALANCED')
ON CONFLICT DO NOTHING;

-- ══════════════════════════════════════
-- [Phase 3] AI 감성 분석 결과
-- ══════════════════════════════════════
CREATE TABLE IF NOT EXISTS sentiment_scores (
    id              SERIAL,
    time            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ticker          VARCHAR(20) NOT NULL,
    score           NUMERIC(5, 4) NOT NULL,
    confidence      NUMERIC(5, 4) NOT NULL DEFAULT 0.0,
    summary         TEXT,
    positive_factors JSONB DEFAULT '[]'::jsonb,
    negative_factors JSONB DEFAULT '[]'::jsonb,
    news_count      INTEGER NOT NULL DEFAULT 0,
    model_used      VARCHAR(50) NOT NULL,
    PRIMARY KEY (id, time)
);

SELECT create_hypertable('sentiment_scores', 'time', if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS idx_sentiment_ticker ON sentiment_scores (ticker, time DESC);

-- ══════════════════════════════════════
-- [Phase 3] AI 투자 의견
-- ══════════════════════════════════════
CREATE TABLE IF NOT EXISTS investment_opinions (
    id              SERIAL,
    time            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ticker          VARCHAR(20),
    opinion_type    VARCHAR(20) NOT NULL DEFAULT 'STOCK',
    action          VARCHAR(20) NOT NULL,
    conviction      NUMERIC(5, 4) NOT NULL DEFAULT 0.0,
    target_weight   NUMERIC(5, 4),
    reasoning       TEXT NOT NULL,
    market_context  TEXT,
    risk_factors    JSONB DEFAULT '[]'::jsonb,
    model_used      VARCHAR(50) NOT NULL,
    PRIMARY KEY (id, time)
);

SELECT create_hypertable('investment_opinions', 'time', if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS idx_opinion_ticker ON investment_opinions (ticker, time DESC);

-- ══════════════════════════════════════
-- [Phase 3] 앙상블 시그널 이력
-- ══════════════════════════════════════
CREATE TABLE IF NOT EXISTS ensemble_signals (
    id              SERIAL,
    time            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ticker          VARCHAR(20) NOT NULL,
    final_signal    NUMERIC(5, 4) NOT NULL,
    final_confidence NUMERIC(5, 4) NOT NULL DEFAULT 0.0,
    component_signals JSONB NOT NULL,
    weights_used    JSONB NOT NULL,
    risk_profile    VARCHAR(20) NOT NULL,
    PRIMARY KEY (id, time)
);

SELECT create_hypertable('ensemble_signals', 'time', if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS idx_ensemble_ticker ON ensemble_signals (ticker, time DESC);

-- ══════════════════════════════════════
-- [Phase 3] 앙상블 가중치 업데이트 이력
-- ══════════════════════════════════════
CREATE TABLE IF NOT EXISTS weight_update_history (
    id              SERIAL PRIMARY KEY,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    risk_profile    VARCHAR(20) NOT NULL,
    old_weights     JSONB NOT NULL,
    new_weights     JSONB NOT NULL,
    method          VARCHAR(30) NOT NULL,
    performance_metrics JSONB,
    reason          TEXT
);