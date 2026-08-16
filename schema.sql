-- HuntHarvest v2 schema
-- Locked design: see PROJECT_STATE.md

CREATE TABLE IF NOT EXISTS config (
    config_key VARCHAR(64) PRIMARY KEY,
    config_value VARCHAR(255) NOT NULL,
    description VARCHAR(255),
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    username VARCHAR(64) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    role ENUM('admin','user') DEFAULT 'user',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tickers (
    ticker VARCHAR(10) PRIMARY KEY,
    company_name VARCHAR(255),
    sector VARCHAR(100),
    sic_code VARCHAR(10),
    shares_outstanding BIGINT,
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

-- One row per qualifying drop/gain event
CREATE TABLE IF NOT EXISTS events (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    ticker VARCHAR(10) NOT NULL,
    event_date DATE NOT NULL,
    direction ENUM('drop','gain') NOT NULL,
    causal_event_type ENUM('earnings','guidance','ma_corporate_action','regulatory_legal','analyst_action','macro_index_driven','unknown') DEFAULT 'unknown',
    -- 'approximate' = tagged earnings via SEC filing-date proximity (no real calendar source yet, see PROJECT_STATE.md)
    causal_confidence ENUM('confirmed','approximate','unknown') DEFAULT 'unknown',
    prior_close DECIMAL(12,4),
    premarket_price DECIMAL(12,4),
    reaction_open DECIMAL(12,4),
    reaction_high DECIMAL(12,4),
    reaction_low DECIMAL(12,4),
    reaction_close DECIMAL(12,4),
    move_pct DECIMAL(8,4) NOT NULL,
    volume BIGINT,
    volume_ratio DECIMAL(8,4),
    rsi_14 DECIMAL(8,4),
    atr_14 DECIMAL(12,4),
    price_vs_sma50 DECIMAL(8,4),
    price_vs_sma200 DECIMAL(8,4),
    mom_3m DECIMAL(8,4),
    market_relative_return DECIMAL(8,4),
    sector_relative_return DECIMAL(8,4),
    market_cap_at_event DECIMAL(20,2),
    sector VARCHAR(100),
    days_since_last_event INT,
    reverted BOOLEAN DEFAULT FALSE,
    days_to_revert INT,
    observation_days INT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uniq_ticker_date (ticker, event_date),
    INDEX idx_ticker (ticker),
    INDEX idx_event_date (event_date),
    INDEX idx_direction (direction)
);

-- Full daily price path from event day forward, until reversion or observation end
CREATE TABLE IF NOT EXISTS price_path (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    event_id BIGINT NOT NULL,
    trade_date DATE NOT NULL,
    day_offset INT NOT NULL,
    close DECIMAL(12,4),
    FOREIGN KEY (event_id) REFERENCES events(id) ON DELETE CASCADE,
    UNIQUE KEY uniq_event_offset (event_id, day_offset),
    INDEX idx_event (event_id)
);

-- Pooled-model output, recomputed on retrain
CREATE TABLE IF NOT EXISTS predictions (
    event_id BIGINT PRIMARY KEY,
    model_version VARCHAR(32),
    bounce_probability DECIMAL(6,4),
    expected_days_to_revert DECIMAL(8,2),
    predicted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (event_id) REFERENCES events(id) ON DELETE CASCADE
);

-- Forward-looking earnings calendar. Schema ready; ingestion blocked on data source (see PROJECT_STATE.md gap).
CREATE TABLE IF NOT EXISTS upcoming_earnings (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    ticker VARCHAR(10) NOT NULL,
    report_date DATE NOT NULL,
    report_time ENUM('bmo','amc','unknown') DEFAULT 'unknown',
    confirmed BOOLEAN DEFAULT FALSE,
    source VARCHAR(50),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uniq_ticker_report (ticker, report_date)
);

-- Daily same-day pipeline, Phase 1 (Specs/SPEC_daily_pipeline.md, Stage 1).
-- Small daily watchlist: tomorrow's BMO reporters + today's AMC reporters - both react
-- at the SAME next-session open (spec §4), hence one shared watch_date for both tracks.
-- Baseline features use the same methodology as `events` (RSI/ATR/SMA/momentum/vol-ratio/
-- market+sector-relative return) so live and historical rows are directly comparable.
CREATE TABLE IF NOT EXISTS daily_watch (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    ticker VARCHAR(10) NOT NULL,
    watch_date DATE NOT NULL,
    track ENUM('bmo','amc') NOT NULL,
    report_date DATE NOT NULL,
    baseline_date DATE NOT NULL,
    prior_close DECIMAL(12,4),
    rsi_14 DECIMAL(8,4),
    atr_14 DECIMAL(12,4),
    price_vs_sma50 DECIMAL(8,4),
    price_vs_sma200 DECIMAL(8,4),
    mom_3m DECIMAL(8,4),
    volume_ratio DECIMAL(8,4),
    market_relative_return DECIMAL(8,4),
    sector_relative_return DECIMAL(8,4),
    market_cap_at_scan DECIMAL(20,2),
    sector VARCHAR(100),
    -- 'watching' -> 'settled' (qualifying move confirmed) or 'dropped' (poll window
    -- elapsed, no qualifying move) once Phase 2's checkpoint logic locks a result.
    status ENUM('watching','settled','dropped') DEFAULT 'watching',
    -- Phase 2 (Stage 2, Specs/SPEC_daily_pipeline.md §4/§6) - the locked qualifying
    -- checkpoint: premarket-clears-threshold for Track A, next-day regular-session
    -- open for Track B (locked design, not the noisier earlier prints).
    checkpoint_price DECIMAL(12,4),
    checkpoint_pct DECIMAL(8,4),
    checkpoint_locked_at TIMESTAMP NULL,
    -- Confirmed event card (deliverable #4) - existing trained model run on the
    -- baseline features already captured above, once the checkpoint locks.
    predicted_probability DECIMAL(6,4),
    predicted_expected_days DECIMAL(8,2),
    predicted_direction ENUM('drop','gain') NULL,
    -- Stage 3 (settle job) - which permanent `events` row this got folded into, once
    -- settled. NULL until folded; prevents double-insertion on repeat job runs.
    folded_event_id BIGINT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uniq_ticker_watchdate (ticker, watch_date),
    INDEX idx_watch_date (watch_date),
    INDEX idx_track (track)
);

-- Raw minute-by-minute reaction data (deliverable #2) - today's forming reaction for
-- each watchlisted ticker, polled during the track-appropriate window (Stage 2). Not a
-- chart, full data points (locked design decision 2026-08-16).
CREATE TABLE IF NOT EXISTS live_reaction_ticks (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    watch_id BIGINT NOT NULL,
    ts_utc DATETIME NOT NULL,
    price DECIMAL(12,4),
    volume BIGINT,
    pct_from_baseline DECIMAL(8,4),
    session ENUM('premarket','regular','afterhours') NOT NULL,
    FOREIGN KEY (watch_id) REFERENCES daily_watch(id) ON DELETE CASCADE,
    UNIQUE KEY uniq_watch_ts (watch_id, ts_utc),
    INDEX idx_watch (watch_id)
);

-- Phase 3 (Specs/SPEC_daily_pipeline.md §5, §7). Locked 2026-08-16: AR/CAR estimation
-- window = 252 trading days, ending 30 days before the event, min 120 days required or
-- skipped; runs ALONGSIDE Phase 2's already-live raw-% threshold, doesn't replace it.
-- PARTIAL-REVERSAL band = retracement_fraction in [0.30, 1.00); V-SHAPED vs GRIND-BACK
-- split at 10 trading days to revert.
ALTER TABLE events
    ADD COLUMN retracement_fraction DECIMAL(8,4) NULL,
    ADD COLUMN shape_category ENUM('v_shaped','grind_back','partial_reversal','dead_cat','no_recovery','continuation','fade') NULL,
    ADD COLUMN post_settlement_outcome ENUM('held','gave_back','extended','monitoring') NULL,
    ADD COLUMN reaction_accuracy ENUM('accurate','overreacted','underreacted','reversed') NULL,
    ADD COLUMN car_at_checkpoint DECIMAL(8,4) NULL,
    ADD COLUMN analysis_computed_at TIMESTAMP NULL;

-- Per-ticker α/β (market-model regression vs SPY) - cached here since it's per-ticker,
-- not per-event, reusable across all of that ticker's events past and future (spec §5).
ALTER TABLE tickers
    ADD COLUMN alpha DECIMAL(12,8) NULL,
    ADD COLUMN beta DECIMAL(10,6) NULL,
    ADD COLUMN alpha_beta_window_start DATE NULL,
    ADD COLUMN alpha_beta_window_end DATE NULL,
    ADD COLUMN alpha_beta_computed_at TIMESTAMP NULL;

-- Edge-strength tracking (deliverable #7) - one row per monthly retrain, rolling
-- recent-vs-prior win rate comparison so a decaying edge (research §8.1) doesn't go
-- unnoticed.
CREATE TABLE IF NOT EXISTS edge_strength_log (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    computed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    model_version VARCHAR(32),
    recent_window_start DATE, recent_window_end DATE, recent_win_rate DECIMAL(6,4), recent_n INT,
    prior_window_start DATE, prior_window_end DATE, prior_win_rate DECIMAL(6,4), prior_n INT,
    trend ENUM('holding','weakening','improving','insufficient_data') NULL
);

INSERT INTO config (config_key, config_value, description) VALUES
    ('drop_threshold_pct', '-10', 'Move % (negative) that qualifies as a drop event'),
    ('gain_threshold_pct', '10', 'Move % (positive) that qualifies as a gain event'),
    ('market_cap_min', '500000000', 'Minimum market cap (USD) for universe inclusion')
ON DUPLICATE KEY UPDATE config_key=config_key;
