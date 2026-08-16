#!/usr/bin/env python3
"""
HuntHarvest v2 — live quotes refresh.
Populates `live_quotes` (current price, day/week/month % change, relative volume) for
every ticker that has at least one qualifying event, so the dashboard can show current
market context alongside historical event data without making a live API call per
page load. Meant to run periodically (e.g. every 15-30 min during market hours via a
systemd timer or cron) - not built into the request path itself, that wouldn't scale.
"""
import os
import logging
import datetime
import pymysql
from ingest_historical import fetch_daily_bars, poly_get, BASE

DB_CONF = dict(host="localhost", user="huntharvest_app",
               password=os.getenv("DB_PASS", "aFLzUDDru0Spair4MduIQjeg"),
               database="huntharvest", cursorclass=pymysql.cursors.DictCursor)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("live_quotes")


def compute_quote(ticker):
    end = datetime.date.today().isoformat()
    start = (datetime.date.today() - datetime.timedelta(days=45)).isoformat()
    df = fetch_daily_bars(ticker, start, end)
    if df is None or len(df) < 2:
        return None

    latest = df.iloc[-1]
    price = float(latest["close"])

    def change_pct(days_back):
        idx = len(df) - 1 - days_back
        if idx < 0:
            return None
        base = df.iloc[idx]["close"]
        return (price - base) / base * 100 if base else None

    avg_vol_20 = df["volume"].tail(21).iloc[:-1].mean() if len(df) > 20 else df["volume"].iloc[:-1].mean()
    rel_volume = float(latest["volume"] / avg_vol_20) if avg_vol_20 else None

    return {
        "ticker": ticker, "price": price,
        "day_change_pct": change_pct(1), "week_change_pct": change_pct(5),
        "month_change_pct": change_pct(21), "rel_volume": rel_volume,
        "quote_date": latest["date"],
    }


def main():
    conn = pymysql.connect(**DB_CONF)
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT ticker FROM events")
    tickers = [r["ticker"] for r in cur.fetchall()]
    log.info(f"Refreshing live quotes for {len(tickers)} tickers")

    done = 0
    for i, ticker in enumerate(tickers):
        try:
            q = compute_quote(ticker)
            if q is None:
                continue
            cur.execute("""
                INSERT INTO live_quotes (ticker, price, day_change_pct, week_change_pct,
                    month_change_pct, rel_volume, quote_date)
                VALUES (%(ticker)s, %(price)s, %(day_change_pct)s, %(week_change_pct)s,
                    %(month_change_pct)s, %(rel_volume)s, %(quote_date)s)
                ON DUPLICATE KEY UPDATE price=VALUES(price), day_change_pct=VALUES(day_change_pct),
                    week_change_pct=VALUES(week_change_pct), month_change_pct=VALUES(month_change_pct),
                    rel_volume=VALUES(rel_volume), quote_date=VALUES(quote_date)
            """, q)
            conn.commit()
            done += 1
        except Exception as e:
            log.error(f"{ticker} FAILED: {e}")
        if i % 100 == 0:
            log.info(f"[{i+1}/{len(tickers)}] {done} updated so far")

    log.info(f"DONE. {done}/{len(tickers)} tickers updated")
    conn.close()


if __name__ == "__main__":
    main()
