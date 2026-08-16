#!/usr/bin/env python3
"""
HuntHarvest v2 — Stage 3 settle job (Specs/SPEC_daily_pipeline.md §6).
Folds newly-settled `daily_watch` rows (Phase 2's checkpoint locked, move qualified)
into the permanent `events`/`price_path` tables, exactly as historical events look -
so the next monthly retrain picks them up automatically, with zero extra wiring.

Also computes reaction-accuracy (§7.2) immediately, since the early-read tick data is
already captured. Does NOT compute retracement_fraction/shape_category/
post_settlement_outcome here - those need days of forward price data this event
doesn't have yet on day 0; backfill_event_analysis.py (run daily) handles those for
this event once enough time has passed, same as it does for historical events.

Meant to run daily (or a few times a day) via a systemd timer - idempotent, only
touches rows with folded_event_id IS NULL.
"""
import os
import logging
import datetime
import pymysql

from event_analysis import classify_reaction_accuracy

DB_CONF = dict(host="localhost", user="huntharvest_app",
               password=os.getenv("DB_PASS", "aFLzUDDru0Spair4MduIQjeg"),
               database="huntharvest", cursorclass=pymysql.cursors.DictCursor)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("settle_watch")


def get_early_pct(cur, watch_id, track):
    """The early, noisy read (§7.2): first premarket tick for Track A, first
    after-hours tick for Track B - the print right after the report, before the
    genuine checkpoint locks."""
    if track == "bmo":
        cur.execute("""SELECT pct_from_baseline FROM live_reaction_ticks
                       WHERE watch_id=%s ORDER BY ts_utc LIMIT 1""", (watch_id,))
    else:
        cur.execute("""SELECT pct_from_baseline FROM live_reaction_ticks
                       WHERE watch_id=%s AND session='afterhours' ORDER BY ts_utc LIMIT 1""", (watch_id,))
    row = cur.fetchone()
    return float(row["pct_from_baseline"]) if row and row["pct_from_baseline"] is not None else None


def main():
    conn = pymysql.connect(**DB_CONF)
    cur = conn.cursor()

    cur.execute("SELECT * FROM daily_watch WHERE status='settled' AND folded_event_id IS NULL")
    rows = cur.fetchall()
    log.info(f"{len(rows)} settled watch rows to fold into events")

    for r in rows:
        try:
            direction = r["predicted_direction"] or ("drop" if r["checkpoint_pct"] < 0 else "gain")
            cur.execute("SELECT MAX(event_date) d FROM events WHERE ticker=%s AND event_date < %s",
                        (r["ticker"], r["watch_date"]))
            last = cur.fetchone()["d"]
            days_since_last = (r["watch_date"] - last).days if last else None

            cur.execute("""
                INSERT INTO events
                (ticker, event_date, direction, causal_event_type, causal_confidence,
                 prior_close, reaction_close, move_pct, volume_ratio, rsi_14, atr_14,
                 price_vs_sma50, price_vs_sma200, mom_3m, market_relative_return,
                 sector_relative_return, market_cap_at_event, sector, days_since_last_event,
                 reaction_accuracy, analysis_computed_at)
                VALUES (%s,%s,%s,'earnings','confirmed', %s,%s,%s,%s,%s,%s, %s,%s,%s,%s,
                        %s,%s,%s,%s, %s,%s)
                ON DUPLICATE KEY UPDATE reaction_close=VALUES(reaction_close), move_pct=VALUES(move_pct)
            """, (
                r["ticker"], r["watch_date"], direction,
                r["prior_close"], r["checkpoint_price"], r["checkpoint_pct"], r["volume_ratio"],
                r["rsi_14"], r["atr_14"], r["price_vs_sma50"], r["price_vs_sma200"], r["mom_3m"],
                r["market_relative_return"], r["sector_relative_return"], r["market_cap_at_scan"],
                r["sector"], days_since_last,
                classify_reaction_accuracy(get_early_pct(cur, r["id"], r["track"]), float(r["checkpoint_pct"])),
                datetime.datetime.now(),
            ))
            event_id = cur.lastrowid
            if event_id == 0:
                cur.execute("SELECT id FROM events WHERE ticker=%s AND event_date=%s", (r["ticker"], r["watch_date"]))
                event_id = cur.fetchone()["id"]

            cur.execute("""
                INSERT INTO price_path (event_id, trade_date, day_offset, close)
                VALUES (%s,%s,0,%s)
                ON DUPLICATE KEY UPDATE close=VALUES(close)
            """, (event_id, r["watch_date"], r["checkpoint_price"]))

            cur.execute("UPDATE daily_watch SET folded_event_id=%s WHERE id=%s", (event_id, r["id"]))
            conn.commit()
            log.info(f"{r['ticker']} ({r['watch_date']}): folded into events id={event_id}")
        except Exception as e:
            conn.rollback()
            log.error(f"{r['ticker']} settle FAILED: {e}")

    conn.close()


if __name__ == "__main__":
    main()
