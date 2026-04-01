-- seed_db.sql — Create tables and insert 10 sample rows per table.
--
-- Usage:
--   1. Copy .env.example to .env and adjust credentials if needed:
--        cp .env.example .env
--
--   2. Start the database (creates DB and user from .env):
--        docker compose up -d
--
--   3. Run this script using the DATABASE_URL from .env:
--        source .env
--        psql "$DATABASE_URL" -f scripts/seed_db.sql
--
-- Prerequisites: Docker with the timescale/timescaledb image (see docker-compose.yml).
-- This script is idempotent — safe to run multiple times.

-- ════════════════════════════════════════════════════════════════════════════════
-- EXTENSIONS
-- ════════════════════════════════════════════════════════════════════════════════

CREATE EXTENSION IF NOT EXISTS timescaledb;

-- ════════════════════════════════════════════════════════════════════════════════
-- TABLE DEFINITIONS
-- ════════════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS minute_bars (
    time        TIMESTAMPTZ      NOT NULL,
    symbol      TEXT             NOT NULL,
    close       DOUBLE PRECISION NOT NULL
);

CREATE TABLE IF NOT EXISTS options_trades (
    sip_timestamp  TIMESTAMPTZ      NOT NULL,
    underlying     TEXT             NOT NULL,
    option_type    TEXT             NOT NULL,
    strike         DOUBLE PRECISION NOT NULL,
    price          DOUBLE PRECISION NOT NULL,
    size           INTEGER          NOT NULL,
    expiration     DATE             NOT NULL
);

CREATE TABLE IF NOT EXISTS fred_releases (
    series_id   TEXT             NOT NULL,
    date        DATE             NOT NULL,
    value       DOUBLE PRECISION NOT NULL
);

CREATE TABLE IF NOT EXISTS stock_splits (
    symbol      TEXT             NOT NULL,
    split_date  DATE             NOT NULL,
    split_ratio DOUBLE PRECISION NOT NULL
);

CREATE TABLE IF NOT EXISTS earnings_releases (
    ticker               TEXT NOT NULL,
    release_date         DATE NOT NULL,
    eps_actual_numeric   DOUBLE PRECISION,
    eps_forecast_numeric DOUBLE PRECISION,
    eps_surprise         DOUBLE PRECISION,
    reporting_time       TEXT
);

-- ════════════════════════════════════════════════════════════════════════════════
-- HYPERTABLES (TimescaleDB)
-- ════════════════════════════════════════════════════════════════════════════════

SELECT create_hypertable('minute_bars',    'time',          if_not_exists => true);
SELECT create_hypertable('options_trades', 'sip_timestamp', if_not_exists => true);

-- ════════════════════════════════════════════════════════════════════════════════
-- SEED DATA (only inserted if tables are empty)
-- ════════════════════════════════════════════════════════════════════════════════

DO $$
BEGIN

-- minute_bars: SPY ~472, QQQ ~402 on 2024-01-02. UTC = ET + 5 (EST).
IF NOT EXISTS (SELECT 1 FROM minute_bars LIMIT 1) THEN
    INSERT INTO minute_bars (time, symbol, close) VALUES
        ('2024-01-02 14:30:00+00', 'SPY', 472.65),  -- 09:30 ET open
        ('2024-01-02 15:00:00+00', 'SPY', 472.41),  -- 10:00 ET
        ('2024-01-02 16:00:00+00', 'SPY', 471.88),  -- 11:00 ET
        ('2024-01-02 18:00:00+00', 'SPY', 472.12),  -- 13:00 ET
        ('2024-01-02 21:00:00+00', 'SPY', 471.55),  -- 16:00 ET close
        ('2024-01-02 14:30:00+00', 'QQQ', 402.30),  -- 09:30 ET open
        ('2024-01-02 15:00:00+00', 'QQQ', 401.97),  -- 10:00 ET
        ('2024-01-02 16:00:00+00', 'QQQ', 401.45),  -- 11:00 ET
        ('2024-01-02 18:00:00+00', 'QQQ', 401.78),  -- 13:00 ET
        ('2024-01-02 21:00:00+00', 'QQQ', 401.10);  -- 16:00 ET close
    RAISE NOTICE 'minute_bars — seeded 10 rows';
ELSE
    RAISE NOTICE 'minute_bars — already has data, skipping seed';
END IF;

-- options_trades: SPY/QQQ on 2024-01-02. Mix of call/put, size classes, expirations.
IF NOT EXISTS (SELECT 1 FROM options_trades LIMIT 1) THEN
    INSERT INTO options_trades (sip_timestamp, underlying, option_type, strike, price, size, expiration) VALUES
        ('2024-01-02 14:35:12+00', 'SPY', 'call', 473.0, 1.25, 5,   '2024-01-02'),   -- 0DTE retail
        ('2024-01-02 14:42:08+00', 'SPY', 'put',  471.0, 0.88, 50,  '2024-01-02'),   -- 0DTE mid
        ('2024-01-02 15:10:33+00', 'SPY', 'call', 475.0, 0.45, 200, '2024-01-05'),   -- weekly institutional
        ('2024-01-02 15:55:01+00', 'SPY', 'put',  470.0, 1.10, 10,  '2024-01-05'),   -- weekly mid
        ('2024-01-02 17:20:44+00', 'SPY', 'call', 472.0, 2.30, 1,   '2024-01-19'),   -- monthly retail
        ('2024-01-02 14:31:05+00', 'QQQ', 'call', 403.0, 1.50, 25,  '2024-01-02'),   -- 0DTE mid
        ('2024-01-02 14:48:19+00', 'QQQ', 'put',  400.0, 0.72, 150, '2024-01-02'),   -- 0DTE institutional
        ('2024-01-02 15:30:55+00', 'QQQ', 'call', 405.0, 0.33, 3,   '2024-01-05'),   -- weekly retail
        ('2024-01-02 16:15:22+00', 'QQQ', 'put',  399.0, 1.05, 75,  '2024-01-05'),   -- weekly mid
        ('2024-01-02 19:00:11+00', 'QQQ', 'call', 402.0, 2.80, 100, '2024-01-19');   -- monthly institutional
    RAISE NOTICE 'options_trades — seeded 10 rows';
ELSE
    RAISE NOTICE 'options_trades — already has data, skipping seed';
END IF;

-- fred_releases: Mix of daily/weekly/monthly series, late 2023 – early 2024.
IF NOT EXISTS (SELECT 1 FROM fred_releases LIMIT 1) THEN
    INSERT INTO fred_releases (series_id, date, value) VALUES
        ('T10Y2Y',        '2023-12-28', -0.34),
        ('T10Y2Y',        '2023-12-29', -0.38),
        ('T10Y2Y',        '2024-01-02', -0.35),
        ('BAMLH0A0HYM2',  '2023-12-28',  3.54),
        ('BAMLH0A0HYM2',  '2023-12-29',  3.51),
        ('DFF',           '2023-12-28',  5.33),
        ('DFF',           '2023-12-29',  5.33),
        ('VIXCLS',        '2023-12-28', 12.45),
        ('VIXCLS',        '2023-12-29', 12.28),
        ('CPIAUCSL',      '2023-11-01', 307.051);
    RAISE NOTICE 'fred_releases — seeded 10 rows';
ELSE
    RAISE NOTICE 'fred_releases — already has data, skipping seed';
END IF;

-- stock_splits: Real historical splits matching split_adjustments.py defaults.
IF NOT EXISTS (SELECT 1 FROM stock_splits LIMIT 1) THEN
    INSERT INTO stock_splits (symbol, split_date, split_ratio) VALUES
        ('AMZN',  '2022-06-06', 20.0),
        ('GOOG',  '2022-07-18', 20.0),
        ('GOOGL', '2022-07-18', 20.0),
        ('GME',   '2022-07-22', 4.0),
        ('TSLA',  '2022-08-25', 3.0),
        ('SHOP',  '2022-06-29', 10.0),
        ('PANW',  '2022-09-14', 3.0),
        ('WMT',   '2024-02-26', 3.0),
        ('NVDA',  '2024-06-10', 10.0),
        ('AVGO',  '2024-07-15', 10.0);
    RAISE NOTICE 'stock_splits — seeded 10 rows';
ELSE
    RAISE NOTICE 'stock_splits — already has data, skipping seed';
END IF;

-- earnings_releases: Mix of BMO/AMC with realistic EPS values.
IF NOT EXISTS (SELECT 1 FROM earnings_releases LIMIT 1) THEN
    INSERT INTO earnings_releases (ticker, release_date, eps_actual_numeric, eps_forecast_numeric, eps_surprise, reporting_time) VALUES
        ('AAPL',  '2024-02-01', 2.18, 2.10, 3.81,  'amc'),
        ('MSFT',  '2024-01-30', 2.93, 2.78, 5.40,  'amc'),
        ('GOOGL', '2024-01-30', 1.64, 1.59, 3.14,  'amc'),
        ('AMZN',  '2024-02-01', 1.00, 0.80, 25.00, 'amc'),
        ('META',  '2024-02-01', 5.33, 4.96, 7.46,  'amc'),
        ('TSLA',  '2024-01-24', 0.71, 0.74, -4.05, 'amc'),
        ('JPM',   '2024-01-12', 3.97, 3.32, 19.58, 'bmo'),
        ('UNH',   '2024-01-12', 6.16, 5.98, 3.01,  'bmo'),
        ('BAC',   '2024-01-12', 0.70, 0.68, 2.94,  'bmo'),
        ('DAL',   '2024-01-12', 1.28, 1.16, 10.34, 'bmo');
    RAISE NOTICE 'earnings_releases — seeded 10 rows';
ELSE
    RAISE NOTICE 'earnings_releases — already has data, skipping seed';
END IF;

END $$;

-- ════════════════════════════════════════════════════════════════════════════════
-- VERIFICATION
-- ════════════════════════════════════════════════════════════════════════════════

DO $$
DECLARE
    tbl TEXT;
    cnt BIGINT;
BEGIN
    FOREACH tbl IN ARRAY ARRAY['minute_bars','options_trades','fred_releases','stock_splits','earnings_releases']
    LOOP
        EXECUTE format('SELECT count(*) FROM %I', tbl) INTO cnt;
        RAISE NOTICE '% — % rows', tbl, cnt;
    END LOOP;
END $$;
