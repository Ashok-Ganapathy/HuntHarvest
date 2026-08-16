#!/usr/bin/env python3
"""
HuntHarvest v2 — combined backfill for the two items explicitly deferred at the end of
the Phase 3 build (real API cost, run only on explicit go-ahead):
  #1 - extend price_path for historical reverted events PAST their reversion point, so
       post_settlement_outcome/dead_cat can be genuinely computed instead of left NULL
       (ingest_historical.py's original backfill stops recording the day reversion is
       first detected - see TASKS.md for the bug this caused and how it was fixed).
  #2 - compute and cache alpha/beta per ticker (market-model regression vs SPY,
       event_analysis.py's AR/CAR foundation) across the tracked universe.

Combined into ONE pass per ticker because both need a daily-bar fetch covering that
ticker's history - one Polygon call per ticker (plus SPY once, globally) covers both,
instead of two separate full-universe passes.
"""
import os
import time
import logging
import datetime
import pymysql
import numpy as np

from ingest_historical import fetch_daily_bars, MARKET_BENCHMARK
from event_analysis import AR_WINDOW_TRADING_DAYS, AR_WINDOW_GAP_DAYS, AR_WINDOW_MIN_TRADING_DAYS

DB_CONF = dict(host="localhost", user="huntharvest_app",
               password=os.getenv("DB_PASS", "aFLzUDDru0Spair4MduIQjeg"),
               database="huntharvest", cursorclass=pymysql.cursors.DictCursor)

POST_REVERT_TRACK_DAYS = 40  # extra trading days past reversion to fetch, for durability

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("backfill_deep")


def main():
    conn = pymysql.connect(**DB_CONF)
    cur = conn.cursor()

    cur.execute("SELECT ticker FROM tickers")
    all_tickers = [r["ticker"] for r in cur.fetchall()]
    log.info(f"{len(all_tickers)} tickers total")

    today = datetime.date.today()
    log.info("Fetching SPY once...")
    spy_start = (today - datetime.timedelta(days=int(AR_WINDOW_TRADING_DAYS * 1.6) + 1500)).isoformat()
    spy_df = fetch_daily_bars(MARKET_BENCHMARK, spy_start, today.isoformat())
    if spy_df is None:
        log.error("Could not fetch SPY - aborting")
        return

    done_ab, done_path, checked = 0, 0, 0
    for i, ticker in enumerate(all_tickers):
        try:
            cur.execute("SELECT id, event_date, direction, prior_close, move_pct, days_to_revert "
                        "FROM events WHERE ticker=%s AND reverted=1", (ticker,))
            reverted_events = cur.fetchall()
            cur.execute("SELECT MAX(event_date) d FROM events WHERE ticker=%s", (ticker,))
            latest_event_date = cur.fetchone()["d"]
            if latest_event_date is None:
                continue

            ab_window_end = latest_event_date - datetime.timedelta(days=AR_WINDOW_GAP_DAYS)
            ab_window_start_est = ab_window_end - datetime.timedelta(days=int(AR_WINDOW_TRADING_DAYS * 1.55) + 20)
            earliest_needed = min([ev["event_date"] for ev in reverted_events], default=ab_window_start_est)
            fetch_start = min(ab_window_start_est, earliest_needed)

            df = fetch_daily_bars(ticker, fetch_start.isoformat(), today.isoformat())
            time.sleep(0.1)
            checked += 1
            if df is None or len(df) < 5:
                continue

            # --- alpha/beta (item #2) ---
            merged = df.merge(spy_df, on="date", suffixes=("_s", "_m"))
            ab_slice = merged[merged["date"] <= ab_window_end].tail(AR_WINDOW_TRADING_DAYS + 1)
            if len(ab_slice) >= AR_WINDOW_MIN_TRADING_DAYS + 1:
                s_ret = ab_slice["close_s"].pct_change().dropna().values * 100
                m_ret = ab_slice["close_m"].pct_change().dropna().values * 100
                var_m = np.var(m_ret)
                if var_m > 0:
                    beta = float(np.cov(s_ret, m_ret)[0, 1] / var_m)
                    alpha = float(np.mean(s_ret) - beta * np.mean(m_ret))
                    cur.execute("""
                        UPDATE tickers SET alpha=%s, beta=%s, alpha_beta_window_start=%s,
                            alpha_beta_window_end=%s, alpha_beta_computed_at=%s WHERE ticker=%s
                    """, (alpha, beta, ab_slice["date"].iloc[0], ab_window_end, datetime.datetime.now(), ticker))
                    done_ab += 1

            # --- extend price_path past reversion (item #1) ---
            for ev in reverted_events:
                cur.execute("SELECT MAX(day_offset) d FROM price_path WHERE event_id=%s", (ev["id"],))
                last_offset = cur.fetchone()["d"] or 0
                after = df[df["date"] > ev["event_date"]].reset_index(drop=True)
                extend = after.iloc[last_offset:last_offset + POST_REVERT_TRACK_DAYS]
                if extend.empty:
                    continue
                rows = [(ev["id"], row["date"], last_offset + j + 1, float(row["close"]))
                        for j, (_, row) in enumerate(extend.iterrows())]
                if rows:
                    cur.executemany("""
                        INSERT INTO price_path (event_id, trade_date, day_offset, close)
                        VALUES (%s,%s,%s,%s) ON DUPLICATE KEY UPDATE close=VALUES(close)
                    """, rows)
                    done_path += 1

            conn.commit()
            if (i + 1) % 200 == 0:
                log.info(f"[{i+1}/{len(all_tickers)}] fetched={checked}, alpha/beta={done_ab}, "
                         f"price_path extended for {done_path} events")
        except Exception as e:
            conn.rollback()
            log.error(f"{ticker} FAILED: {e}")

    log.info(f"DONE. {checked} tickers fetched, alpha/beta computed for {done_ab}, "
             f"price_path extended for {done_path} reverted events")
    conn.close()


if __name__ == "__main__":
    main()
