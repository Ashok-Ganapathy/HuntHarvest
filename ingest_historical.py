#!/usr/bin/env python3
"""
HuntHarvest v2 — historical backfill ingestion.
Locked design: see PROJECT_STATE.md.

Universe: US active stocks, market_cap > config.market_cap_min (default $500M)
Window: 2022-01-01 to today, daily bars only
Event: close-to-close move >= gain_threshold_pct or <= drop_threshold_pct (config-driven)
Market cap at event = shares_outstanding (current, cached) x historical close (fixes v1's
static-mcap bug — this varies by date instead of freezing at today's value).
Causal event tagging = 'earnings'/'approximate' when a SEC filing_date falls within
+/-10 trading days of the event (no real forward-looking calendar available yet — see
PROJECT_STATE.md "Known gap"). Everything else tags 'unknown'/'unknown', never guessed.
Price path tracked forward with NO fixed cutoff — reversion checked against all available
future data; if it never reverts before "today", recorded as reverted=False (right-censored),
not forced to a number. Never swallow DB insert errors (v1's #2 known failure mode).
"""
import os
import sys
import time
import math
import logging
import datetime
import requests
import pymysql
import numpy as np
import pandas as pd

POLYGON_KEY = os.getenv("POLYGON_KEY", "74DMSl0HQK1PSLhQ4YVPW2sVq9HIgJ9I")
BASE = "https://api.polygon.io"
START_DATE = "2022-01-01"
DB_CONF = dict(host="localhost", user="huntharvest_app",
               password=os.getenv("DB_PASS", "aFLzUDDru0Spair4MduIQjeg"),
               database="huntharvest", autocommit=False)

SECTOR_ETF = {
    "Technology": "XLK", "Healthcare": "XLV", "Financials": "XLF",
    "Consumer Discretionary": "XLY", "Consumer Staples": "XLP", "Energy": "XLE",
    "Industrials": "XLI", "Materials": "XLB", "Real Estate": "XLRE",
    "Utilities": "XLU", "Communication Services": "XLC",
}
MARKET_BENCHMARK = "SPY"

# Polygon only exposes SIC code/description, not GICS sector - no field to match
# "Technology"/"Healthcare"/etc. against directly (confirmed via smoke test on SEZL,
# which returned sic_description "SERVICES-BUSINESS SERVICES, NEC"). This is an
# approximate SIC-code-range -> GICS-like-sector mapping, not authoritative GICS
# classification, built to be good enough for sector-ETF relative-return purposes.
SIC_RANGES = [
    (100, 999, "Materials"), (1000, 1499, "Materials"), (1500, 1799, "Industrials"),
    (2000, 2199, "Consumer Staples"), (2200, 2399, "Consumer Discretionary"),
    (2400, 2799, "Materials"), (2800, 2836, "Materials"), (2830, 2836, "Healthcare"),
    (2837, 2899, "Materials"), (2900, 2999, "Energy"), (3000, 3599, "Industrials"),
    (3600, 3699, "Technology"), (3700, 3799, "Industrials"), (3800, 3829, "Industrials"),
    (3826, 3851, "Healthcare"), (3852, 3999, "Industrials"),
    (4000, 4799, "Industrials"), (4800, 4899, "Communication Services"),
    (4900, 4999, "Utilities"), (5000, 5199, "Industrials"), (5200, 5999, "Consumer Discretionary"),
    (6000, 6099, "Financials"), (6100, 6299, "Financials"), (6300, 6499, "Financials"),
    (6500, 6599, "Real Estate"), (6700, 6799, "Financials"),
    (7000, 7369, "Consumer Discretionary"), (7370, 7379, "Technology"),
    (7380, 7999, "Industrials"), (8000, 8099, "Healthcare"), (8100, 8999, "Industrials"),
]


def sic_to_sector(sic_code):
    try:
        code = int(sic_code)
    except (TypeError, ValueError):
        return "Unknown"
    for lo, hi, sector in reversed(SIC_RANGES):  # reversed: later, more-specific overrides checked first
        if lo <= code <= hi:
            return sector
    return "Unknown"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                     handlers=[logging.FileHandler("/var/www/huntorharvest/ingest.log"),
                               logging.StreamHandler()])
log = logging.getLogger("ingest")


def poly_get(url, params=None, max_retries=5):
    params = dict(params or {})
    params["apiKey"] = POLYGON_KEY
    for attempt in range(max_retries):
        try:
            r = requests.get(url, params=params, timeout=30)
            if r.status_code == 429:
                wait = 15 * (attempt + 1)
                log.warning(f"429 rate limited, sleeping {wait}s")
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            log.warning(f"request error {e}, retry {attempt+1}/{max_retries}")
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"Failed to fetch {url} after {max_retries} retries")


def load_config(conn):
    cur = conn.cursor()
    cur.execute("SELECT config_key, config_value FROM config")
    return {k: v for k, v in cur.fetchall()}


def fetch_universe(min_market_cap):
    """Full US active stock universe, paginated fully, filtered by real market cap."""
    log.info("Fetching full ticker list...")
    tickers = []
    url = f"{BASE}/v3/reference/tickers"
    params = {"market": "stocks", "active": "true", "limit": 1000}
    while url:
        j = poly_get(url, params)
        tickers.extend(t["ticker"] for t in j.get("results", []))
        url = j.get("next_url")
        params = {}  # next_url already has query params baked in except apiKey
        time.sleep(0.1)
    log.info(f"Total active US tickers: {len(tickers)}")

    universe = []
    for i, t in enumerate(tickers):
        try:
            j = poly_get(f"{BASE}/v3/reference/tickers/{t}")
            res = j.get("results", {}) or {}
            mcap = res.get("market_cap") or 0
            if mcap >= min_market_cap:
                universe.append({
                    "ticker": t,
                    "company_name": res.get("name", ""),
                    "sector": sic_to_sector(res.get("sic_code")),
                    "sic_code": res.get("sic_code", ""),
                    "shares_outstanding": res.get("weighted_shares_outstanding")
                                          or res.get("share_class_shares_outstanding") or 0,
                })
        except Exception as e:
            log.warning(f"universe filter failed for {t}: {e}")
        if i % 200 == 0:
            log.info(f"  universe filter {i}/{len(tickers)} -> {len(universe)} qualify")
        time.sleep(0.05)
    log.info(f"Universe >= ${min_market_cap:,.0f} market cap: {len(universe)} tickers")
    return universe


def ticker_valid_from(ticker):
    """Polygon reuses ticker symbols across unrelated companies over time (e.g. Facebook/Meta
    Platforms changed FB -> META on 2022-06-09; querying "META" bars before that date silently
    returns a DIFFERENT, unrelated small-cap company that held the symbol previously - a real
    ~1400% one-day "gain" artifact was found this way during QC, not a real price move).
    Returns the earliest date this ticker symbol has referred to its CURRENT entity, or None
    if there's no ticker_change event on record (symbol has always belonged to this company)."""
    try:
        j = poly_get(f"{BASE}/vX/reference/tickers/{ticker}/events", {"types": "ticker_change"})
        events = (j.get("results") or {}).get("events", [])
        matches = [e["date"] for e in events
                   if e.get("type") == "ticker_change" and e.get("ticker_change", {}).get("ticker") == ticker]
        return max(matches) if matches else None
    except Exception as e:
        log.warning(f"ticker_events fetch failed for {ticker}: {e}")
        return None


def fetch_daily_bars(ticker, start, end):
    valid_from = ticker_valid_from(ticker)
    if valid_from and valid_from > start:
        log.info(f"{ticker}: symbol reassigned {valid_from}, clipping start date from {start}")
        start = valid_from

    j = poly_get(f"{BASE}/v2/aggs/ticker/{ticker}/range/1/day/{start}/{end}",
                  {"adjusted": "true", "sort": "asc", "limit": 50000})
    bars = j.get("results", []) or []
    if not bars:
        return None
    df = pd.DataFrame(bars)
    df["date"] = pd.to_datetime(df["t"], unit="ms").dt.date
    df = df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
    return df[["date", "open", "high", "low", "close", "volume"]].sort_values("date").reset_index(drop=True)


def compute_indicators(df):
    """RSI(14), ATR(14), SMA50/200, 3m momentum, 20d volume avg. Adds columns in place."""
    close = df["close"]
    high = df["high"]
    low = df["low"]
    prev_close = close.shift(1)

    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    df["rsi_14"] = 100 - (100 / (1 + rs))

    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    df["atr_14"] = tr.rolling(14).mean()

    df["sma_50"] = close.rolling(50).mean()
    df["sma_200"] = close.rolling(200).mean()
    df["vol_avg_20"] = df["volume"].rolling(20).mean()
    df["mom_3m"] = (close - close.shift(63)) / close.shift(63) * 100
    return df


def fetch_sec_filing_dates(ticker):
    """Approximate earnings-date signal (see PROJECT_STATE.md gap). Returns sorted date list."""
    try:
        j = poly_get(f"{BASE}/vX/reference/financials", {"ticker": ticker, "limit": 20, "timeframe": "quarterly"})
        return sorted(pd.to_datetime(r["filing_date"]).date()
                      for r in j.get("results", []) if r.get("filing_date"))
    except Exception as e:
        log.warning(f"SEC filings fetch failed for {ticker}: {e}")
        return []


def is_near_filing(event_date, filing_dates, window_days=10):
    for fd in filing_dates:
        if abs((fd - event_date).days) <= window_days:
            return True
    return False


def ensure_connected(conn):
    """conn.ping(reconnect=True) is deprecated in newer pymysql - the recommended
    replacement is to catch the failure and reconnect the same Connection object
    explicitly via .connect()."""
    try:
        conn.ping(reconnect=False)
    except Exception:
        conn.connect()


def nz(v):
    """NaN/None -> None (MySQL NULL). Pandas rolling windows (RSI/ATR/SMA200/mom_3m)
    are NaN until enough lookback history has accumulated - pymysql can't insert
    a raw float('nan') directly, it must become NULL."""
    if v is None:
        return None
    try:
        if isinstance(v, float) and math.isnan(v):
            return None
    except TypeError:
        pass
    return v


def insert_event_and_path(conn, ticker, row, df, idx, sector, market_cap,
                            market_ret, sector_ret, days_since_last, causal_type, causal_conf):
    cur = conn.cursor()
    direction = "drop" if row["move_pct"] < 0 else "gain"

    # Walk forward from the event day to find reversion, using ALL available future data
    # (this IS the historical backfill, so "future" relative to the event is already known).
    # No artificial cutoff — right-censored (reverted=False) only if we truly run out of data.
    prior_close = row["prior_close"]
    reverted = False
    days_to_revert = None
    path_rows = [(0, row["close"])]
    for offset in range(1, len(df) - idx):
        future_close = df.iloc[idx + offset]["close"]
        path_rows.append((offset, future_close))
        if direction == "drop" and future_close >= prior_close:
            reverted, days_to_revert = True, offset
            break
        if direction == "gain" and future_close <= prior_close:
            reverted, days_to_revert = True, offset
            break
    observation_days = path_rows[-1][0]

    try:
        cur.execute("""
            INSERT INTO events
            (ticker, event_date, direction, causal_event_type, causal_confidence,
             prior_close, reaction_open, reaction_high, reaction_low, reaction_close,
             move_pct, volume, volume_ratio, rsi_14, atr_14, price_vs_sma50, price_vs_sma200,
             mom_3m, market_relative_return, sector_relative_return, market_cap_at_event,
             sector, days_since_last_event, reverted, days_to_revert, observation_days)
            VALUES (%s,%s,%s,%s,%s, %s,%s,%s,%s,%s, %s,%s,%s,%s,%s,%s,%s, %s,%s,%s,%s, %s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE reaction_close=VALUES(reaction_close)
        """, (
            ticker, row["date"], direction, causal_type, causal_conf,
            nz(prior_close), nz(row["open"]), nz(row["high"]), nz(row["low"]), nz(row["close"]),
            nz(row["move_pct"]), int(row["volume"]), nz(row["vol_ratio"]), nz(row["rsi_14"]), nz(row["atr_14"]),
            nz(row["price_vs_sma50"]), nz(row["price_vs_sma200"]), nz(row["mom_3m"]),
            nz(market_ret), nz(sector_ret), nz(market_cap),
            sector, days_since_last, reverted, days_to_revert, observation_days,
        ))
        event_id = cur.lastrowid
        if event_id == 0:
            # ON DUPLICATE KEY UPDATE path — fetch the existing id
            cur.execute("SELECT id FROM events WHERE ticker=%s AND event_date=%s", (ticker, row["date"]))
            event_id = cur.fetchone()[0]
            cur.execute("DELETE FROM price_path WHERE event_id=%s", (event_id,))

        cur.executemany(
            "INSERT INTO price_path (event_id, trade_date, day_offset, close) VALUES (%s,%s,%s,%s)",
            [(event_id, df.iloc[idx + off]["date"] if off < len(df) - idx else row["date"], off, nz(c))
             for off, c in path_rows]
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise  # never swallow — v1's known failure mode #2


def process_ticker(conn, ticker_info, thresholds, spy_df, sector_dfs):
    ticker = ticker_info["ticker"]
    df = fetch_daily_bars(ticker, START_DATE, datetime.date.today().isoformat())
    if df is None or len(df) < 70:
        return 0
    df = compute_indicators(df)
    filing_dates = fetch_sec_filing_dates(ticker)

    events_found = 0
    last_event_date = None
    for idx in range(1, len(df)):
        r = df.iloc[idx]
        prev_close = df.iloc[idx - 1]["close"]
        if not prev_close:
            continue
        move_pct = (r["close"] - prev_close) / prev_close * 100
        if move_pct > float(thresholds["drop_threshold_pct"]) and move_pct < float(thresholds["gain_threshold_pct"]):
            continue  # doesn't qualify either direction

        row = r.to_dict()
        row["move_pct"] = move_pct
        row["prior_close"] = prev_close
        row["vol_ratio"] = (r["volume"] / r["vol_avg_20"]) if r["vol_avg_20"] else None
        row["price_vs_sma50"] = ((r["close"] - r["sma_50"]) / r["sma_50"] * 100) if r["sma_50"] else None
        row["price_vs_sma200"] = ((r["close"] - r["sma_200"]) / r["sma_200"] * 100) if r["sma_200"] else None

        event_date = r["date"]
        spy_row = spy_df[spy_df["date"] == event_date]
        market_ret = None
        if not spy_row.empty:
            spy_prev = spy_df[spy_df["date"] < event_date].tail(1)
            if not spy_prev.empty:
                spy_move = (spy_row.iloc[0]["close"] - spy_prev.iloc[0]["close"]) / spy_prev.iloc[0]["close"] * 100
                market_ret = move_pct - spy_move

        sector = ticker_info["sector"]
        etf = SECTOR_ETF.get(sector)
        sector_ret = None
        if etf and etf in sector_dfs:
            sdf = sector_dfs[etf]
            s_row = sdf[sdf["date"] == event_date]
            s_prev = sdf[sdf["date"] < event_date].tail(1)
            if not s_row.empty and not s_prev.empty:
                s_move = (s_row.iloc[0]["close"] - s_prev.iloc[0]["close"]) / s_prev.iloc[0]["close"] * 100
                sector_ret = move_pct - s_move

        market_cap_at_event = (ticker_info["shares_outstanding"] * r["close"]
                                if ticker_info["shares_outstanding"] else None)
        # Point-in-time qualification, not just current-cap: universe selection above uses
        # today's market cap to decide which tickers to pull at all, but each individual
        # historical event must independently clear the floor at the time it happened -
        # otherwise a stock that grew 40x since 2023 (e.g. SEZL) would have all its old,
        # genuinely-sub-$500M-at-the-time events counted as if they'd always qualified.
        # This is the same class of bug (retroactive vs. point-in-time market cap) v1 had.
        if market_cap_at_event is None or market_cap_at_event < float(thresholds["market_cap_min"]):
            continue
        days_since_last = (event_date - last_event_date).days if last_event_date else None
        causal_hit = is_near_filing(event_date, filing_dates)
        causal_type = "earnings" if causal_hit else "unknown"
        causal_conf = "approximate" if causal_hit else "unknown"

        insert_event_and_path(conn, ticker, row, df, idx, sector, market_cap_at_event,
                               market_ret, sector_ret, days_since_last, causal_type, causal_conf)
        events_found += 1
        last_event_date = event_date

    return events_found


def upsert_ticker_meta(conn, t):
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO tickers (ticker, company_name, sector, sic_code, shares_outstanding)
        VALUES (%s,%s,%s,%s,%s)
        ON DUPLICATE KEY UPDATE company_name=VALUES(company_name), sector=VALUES(sector),
            sic_code=VALUES(sic_code), shares_outstanding=VALUES(shares_outstanding)
    """, (t["ticker"], t["company_name"], t["sector"], t["sic_code"], t["shares_outstanding"]))
    conn.commit()


def main():
    conn = pymysql.connect(**DB_CONF)
    thresholds = load_config(conn)
    log.info(f"Config: {thresholds}")

    universe = fetch_universe(float(thresholds["market_cap_min"]))
    # fetch_universe is Polygon-only (no DB calls) and can run 30+ min for the full
    # ticker list - long enough for the connection to go stale (observed: Ubuntu's
    # unattended-upgrades restarted mysqld mid-run when a linked system library got
    # patched). ensure_connected() transparently reconnects if needed.
    ensure_connected(conn)
    for t in universe:
        upsert_ticker_meta(conn, t)

    today = datetime.date.today().isoformat()
    log.info("Fetching market benchmark (SPY) and sector ETFs...")
    spy_df = fetch_daily_bars(MARKET_BENCHMARK, START_DATE, today)
    sector_dfs = {}
    for etf in set(SECTOR_ETF.values()):
        d = fetch_daily_bars(etf, START_DATE, today)
        if d is not None:
            sector_dfs[etf] = d
        time.sleep(0.1)

    total_events = 0
    for i, t in enumerate(universe):
        try:
            ensure_connected(conn)  # cheap, guards against any mid-run disconnect
            n = process_ticker(conn, t, thresholds, spy_df, sector_dfs)
            total_events += n
            if n:
                log.info(f"[{i+1}/{len(universe)}] {t['ticker']}: +{n} events, total={total_events}")
            elif i % 50 == 0:
                log.info(f"[{i+1}/{len(universe)}] {t['ticker']}: 0 events (progress checkpoint)")
        except Exception as e:
            log.error(f"{t['ticker']} FAILED: {e}")  # logged, never silently swallowed
        time.sleep(0.1)

    log.info(f"DONE. Total events: {total_events}")
    conn.close()


if __name__ == "__main__":
    main()
