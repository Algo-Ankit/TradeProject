CREATE DATABASE IF NOT EXISTS trading;

CREATE TABLE IF NOT EXISTS trading.ticks
(
    timestamp    DateTime64(6, 'Asia/Kolkata'),
    symbol       LowCardinality(String),
    exchange     LowCardinality(String),
    last_price   Float64,
    bid          Float64,
    ask          Float64,
    bid_qty      Int32,
    ask_qty      Int32,
    volume       Int64,
    oi           Int64,
    received_at  DateTime64(6, 'Asia/Kolkata') DEFAULT now64(6)
)
ENGINE = MergeTree()
PARTITION BY toYYYYMM(timestamp)
ORDER BY (symbol, timestamp)
TTL toDateTime(timestamp) + INTERVAL 365 DAY
SETTINGS index_granularity = 8192;

CREATE TABLE IF NOT EXISTS trading.order_book
(
    timestamp  DateTime64(6, 'Asia/Kolkata'),
    symbol     LowCardinality(String),
    level      UInt8,
    bid_price  Float64,
    bid_qty    Int32,
    ask_price  Float64,
    ask_qty    Int32
)
ENGINE = MergeTree()
PARTITION BY toYYYYMM(timestamp)
ORDER BY (symbol, timestamp, level)
TTL toDateTime(timestamp) + INTERVAL 30 DAY
SETTINGS index_granularity = 8192;

CREATE TABLE IF NOT EXISTS trading.features
(
    timestamp    DateTime64(6, 'Asia/Kolkata'),
    symbol       LowCardinality(String),
    feature_name LowCardinality(String),
    value        Float64
)
ENGINE = MergeTree()
PARTITION BY toYYYYMM(timestamp)
ORDER BY (symbol, feature_name, timestamp)
TTL toDateTime(timestamp) + INTERVAL 90 DAY
SETTINGS index_granularity = 8192;

CREATE TABLE IF NOT EXISTS trading.fills
(
    fill_id         String,
    order_id        String,
    strategy_id     LowCardinality(String),
    timestamp       DateTime64(6, 'Asia/Kolkata'),
    symbol          LowCardinality(String),
    side            Enum8('BUY' = 1, 'SELL' = -1),
    qty             Int32,
    fill_price      Float64,
    expected_price  Float64,
    slippage_bps    Float64,
    commission_inr  Float64
)
ENGINE = MergeTree()
PARTITION BY toYYYYMM(timestamp)
ORDER BY (strategy_id, timestamp)
SETTINGS index_granularity = 8192;

CREATE TABLE IF NOT EXISTS trading.pnl_snapshots
(
    timestamp        DateTime64(6, 'Asia/Kolkata'),
    strategy_id      LowCardinality(String),
    realized_pnl     Float64,
    unrealized_pnl   Float64,
    total_pnl        Float64,
    gross_exposure   Float64,
    net_exposure     Float64
)
ENGINE = MergeTree()
PARTITION BY toYYYYMM(timestamp)
ORDER BY (strategy_id, timestamp)
TTL toDateTime(timestamp) + INTERVAL 365 DAY
SETTINGS index_granularity = 8192;

CREATE TABLE IF NOT EXISTS trading.orders
(
    order_id          String,
    strategy_id       LowCardinality(String),
    timestamp_sent    DateTime64(6, 'Asia/Kolkata'),
    timestamp_acked   Nullable(DateTime64(6, 'Asia/Kolkata')),
    symbol            LowCardinality(String),
    side              Enum8('BUY' = 1, 'SELL' = -1),
    order_type        Enum8('MARKET' = 1, 'LIMIT' = 2, 'SL' = 3, 'SL_M' = 4),
    qty               Int32,
    limit_price       Nullable(Float64),
    status            LowCardinality(String),
    broker_order_id   Nullable(String)
)
ENGINE = MergeTree()
PARTITION BY toYYYYMM(timestamp_sent)
ORDER BY (strategy_id, timestamp_sent)
SETTINGS index_granularity = 8192;
