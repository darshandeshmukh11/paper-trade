-- Run once in Supabase: SQL Editor → New query → paste → Run

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trades (
    id BIGSERIAL PRIMARY KEY,
    traded_at TEXT NOT NULL,
    symbol TEXT NOT NULL,
    exchange TEXT NOT NULL DEFAULT 'NSE',
    segment TEXT NOT NULL DEFAULT 'Equity Delivery',
    side TEXT NOT NULL,
    qty INTEGER NOT NULL,
    price DOUBLE PRECISION NOT NULL,
    position_id TEXT,
    notes TEXT,
    gross DOUBLE PRECISION NOT NULL,
    charges DOUBLE PRECISION NOT NULL,
    net_cash DOUBLE PRECISION NOT NULL,
    created_at TEXT NOT NULL,
    stop_loss DOUBLE PRECISION,
    target_price DOUBLE PRECISION
);

CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades (symbol);
CREATE INDEX IF NOT EXISTS idx_trades_position ON trades (position_id);

INSERT INTO settings (key, value) VALUES
    ('starting_capital', '1000000'),
    ('charge_settings', '{"dp_delivery_sell": 15.93}')
ON CONFLICT (key) DO NOTHING;
