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
    ('charge_settings', '{"brokerage_per_order": 20, "gst_on_brokerage": 0.18, "stt_delivery_sell": 0.001, "stt_intraday_sell": 0.00025, "exchange_txn_pct": 3.45e-05, "sebi_pct": 1e-06, "stamp_duty_buy": 0.00015, "dp_delivery_sell": 15.93}')
ON CONFLICT (key) DO NOTHING;
