#!/usr/bin/env python3
"""
HuntHarvest v2 — morning catch-up scan.
Real gap found live 2026-08-17: the evening scan (daily_watch_scan.py) is a single
snapshot of Finviz's earnings calendar. A ticker whose earnings date/timing gets added
or confirmed by Finviz AFTER that snapshot (before today's open) is silently missed for
the rest of that day - happened for real with EMAT/DRUG, both showing BMO 8:30am for
"today" only once checked the next morning, not the evening before.

Run once, early morning before market open (huntharvest-morningcatchup.timer, ~11:00 UTC
/ ~7:00am ET - well before the 9:30 open and before any today daily bar exists yet, so
compute_baseline_features()'s "today's close" is still safely yesterday's close).

Scope: BMO only. AMC doesn't need a morning catch-up - an AMC reporter's baseline is
captured the evening it reports (same-day evening scan), and its reaction isn't until
the NEXT day, so there's no same-morning urgency the way there is for BMO tickers whose
reaction is happening within the hour.
"""
import logging
import datetime
import pymysql

from daily_watch_scan import (
    fetch_finviz_earnings_calendar, earnings_date, earnings_timing,
    compute_baseline_features, DB_CONF,
)
from ingest_historical import fetch_daily_bars, MARKET_BENCHMARK, SECTOR_ETF

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("morning_catchup")


def main():
    conn = pymysql.connect(**DB_CONF)
    cur = conn.cursor()

    today = datetime.date.today()
    cur.execute("SELECT ticker, sector, shares_outstanding FROM tickers")
    tracked = {r["ticker"]: r for r in cur.fetchall()}

    cur.execute("SELECT ticker FROM daily_watch WHERE watch_date=%s AND track='bmo'", (today,))
    already_have = {r["ticker"] for r in cur.fetchall()}

    log.info("Fetching Finviz earnings calendar (catch-up pass)...")
    fdf = fetch_finviz_earnings_calendar()

    missed = []
    for _, row in fdf.iterrows():
        ticker = row.get("Ticker")
        if not ticker or ticker not in tracked or ticker in already_have:
            continue
        raw = row.get("Earnings Date")
        ed = earnings_date(raw)
        timing = earnings_timing(raw)
        if ed == today and timing == 'b':
            missed.append(ticker)

    log.info(f"{len(missed)} BMO reporter(s) for today not caught by last night's scan: {missed}")
    if not missed:
        conn.close()
        return

    b_end = today.isoformat()
    b_start = (today - datetime.timedelta(days=320)).isoformat()
    spy_df = fetch_daily_bars(MARKET_BENCHMARK, b_start, b_end)
    sector_dfs = {}
    for etf in set(SECTOR_ETF.values()):
        d = fetch_daily_bars(etf, b_start, b_end)
        if d is not None:
            sector_dfs[etf] = d

    written = 0
    for ticker in missed:
        try:
            info = tracked[ticker]
            feats = compute_baseline_features(ticker, info["sector"], spy_df, sector_dfs, exclude_today=True)
            if feats is None:
                log.warning(f"{ticker}: no usable price history, skipping")
                continue
            mcap = (info["shares_outstanding"] * feats["prior_close"]) if info["shares_outstanding"] else None

            cur.execute("""
                INSERT INTO upcoming_earnings (ticker, report_date, report_time, confirmed, source)
                VALUES (%s,%s,'bmo',%s,%s)
                ON DUPLICATE KEY UPDATE report_time=VALUES(report_time), confirmed=VALUES(confirmed)
            """, (ticker, today, True, 'finviz_catchup'))

            cur.execute("""
                INSERT INTO daily_watch
                (ticker, watch_date, track, report_date, baseline_date, prior_close,
                 rsi_14, atr_14, price_vs_sma50, price_vs_sma200, mom_3m, volume_ratio,
                 market_relative_return, sector_relative_return, market_cap_at_scan, sector, status)
                VALUES (%s,%s,'bmo',%s,%s,%s, %s,%s,%s,%s,%s,%s, %s,%s,%s,%s,'watching')
                ON DUPLICATE KEY UPDATE status=VALUES(status)
            """, (ticker, today, today, today, feats["prior_close"],
                  feats["rsi_14"], feats["atr_14"], feats["price_vs_sma50"], feats["price_vs_sma200"],
                  feats["mom_3m"], feats["volume_ratio"], feats["market_relative_return"],
                  feats["sector_relative_return"], mcap, info["sector"]))
            conn.commit()
            written += 1
            log.info(f"{ticker}: caught up, baseline close=${feats['prior_close']:.2f}")
        except Exception as e:
            conn.rollback()
            log.error(f"{ticker} FAILED: {e}")

    log.info(f"DONE. {written}/{len(missed)} caught-up tickers added to today's watchlist")
    conn.close()


if __name__ == "__main__":
    main()
