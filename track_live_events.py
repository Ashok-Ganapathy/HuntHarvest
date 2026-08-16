#!/usr/bin/env python3
"""
HuntHarvest v2 — daily forward-tracker for live-pipeline-originated events only.
Scoped strictly to events created via daily_watch (settle_watch.py) that are still
within their observation window - NOT the ~37,773 historical backfill events (daily
incremental ingest for those stays out of scope, per the base v2 spec's non-goals).
Fetches each still-tracking event's latest daily bar(s), appends to price_path,
updates reverted/days_to_revert if newly crossed, and re-runs the same Phase 3
analysis backfill_event_analysis.py uses so both paths agree.

Meant to run once daily (after close) via a systemd timer.
"""
import os
import logging
import datetime
import pymysql

from ingest_historical import fetch_daily_bars
from event_analysis import retracement_fraction, classify_shape, classify_post_settlement
from backfill_event_analysis import WINDOW_SETTLED_DAYS

DB_CONF = dict(host="localhost", user="huntharvest_app",
               password=os.getenv("DB_PASS", "aFLzUDDru0Spair4MduIQjeg"),
               database="huntharvest", cursorclass=pymysql.cursors.DictCursor)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("track_live_events")


def main():
    conn = pymysql.connect(**DB_CONF)
    cur = conn.cursor()

    cur.execute("""
        SELECT e.* FROM events e
        JOIN daily_watch dw ON dw.folded_event_id = e.id
        WHERE e.analysis_computed_at IS NULL
    """)
    events = cur.fetchall()
    log.info(f"{len(events)} live-originated events still tracking")

    for ev in events:
        try:
            cur.execute("SELECT MAX(day_offset) d FROM price_path WHERE event_id=%s", (ev["id"],))
            last_offset = cur.fetchone()["d"] or 0
            cur.execute("SELECT trade_date FROM price_path WHERE event_id=%s AND day_offset=%s",
                        (ev["id"], last_offset))
            last_date = cur.fetchone()["trade_date"]

            fetch_from = last_date + datetime.timedelta(days=1)
            today = datetime.date.today()
            if fetch_from > today:
                continue  # already caught up

            df = fetch_daily_bars(ev["ticker"], fetch_from.isoformat(), today.isoformat())
            if df is not None and len(df):
                for i, row in df.iterrows():
                    offset = last_offset + i + 1
                    cur.execute("""
                        INSERT INTO price_path (event_id, trade_date, day_offset, close)
                        VALUES (%s,%s,%s,%s) ON DUPLICATE KEY UPDATE close=VALUES(close)
                    """, (ev["id"], row["date"], offset, float(row["close"])))
                conn.commit()

            cur.execute("SELECT day_offset, close FROM price_path WHERE event_id=%s ORDER BY day_offset", (ev["id"],))
            path = [(r["day_offset"], float(r["close"])) for r in cur.fetchall() if r["close"] is not None]
            if not path:
                continue

            prior_close = float(ev["prior_close"])
            move_pct = float(ev["move_pct"])
            direction = ev["direction"]

            # Update reverted/days_to_revert if not already set - same crossing rule
            # ingest_historical.py uses for consistency.
            if not ev["reverted"]:
                for off, close in path:
                    if off == 0:
                        continue
                    if (direction == "drop" and close >= prior_close) or (direction == "gain" and close <= prior_close):
                        cur.execute("UPDATE events SET reverted=1, days_to_revert=%s WHERE id=%s", (off, ev["id"]))
                        ev = dict(ev)
                        ev["reverted"], ev["days_to_revert"] = True, off
                        break

            final_close = path[-1][1]
            current_pct = (final_close - prior_close) / prior_close * 100
            frac = retracement_fraction(move_pct, current_pct)
            window_elapsed = (today - ev["event_date"]).days >= WINDOW_SETTLED_DAYS
            mirror_days = ev["days_to_revert"] or 30
            outcome = classify_post_settlement(path, prior_close, move_pct, mirror_days, window_elapsed)
            shape = classify_shape(direction, frac, ev["days_to_revert"], outcome)
            computed_at = datetime.datetime.now() if window_elapsed else None

            cur.execute("""
                UPDATE events SET observation_days=%s, retracement_fraction=%s, shape_category=%s,
                    post_settlement_outcome=%s, analysis_computed_at=%s
                WHERE id=%s
            """, (path[-1][0], frac, shape, outcome, computed_at, ev["id"]))
            conn.commit()
            log.info(f"{ev['ticker']} ({ev['event_date']}): day {path[-1][0]}, retrace={frac}, "
                     f"outcome={outcome}, shape={shape}")
        except Exception as e:
            conn.rollback()
            log.error(f"{ev['ticker']} tracking FAILED: {e}")

    conn.close()


if __name__ == "__main__":
    main()
