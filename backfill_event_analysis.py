#!/usr/bin/env python3
"""
HuntHarvest v2 — one-time Phase 3 backfill for existing historical events.
Computes retracement_fraction/shape_category/post_settlement_outcome (spec §7.1-§7.3)
for all events using their ALREADY-STORED price_path - zero new API calls, pure local
computation. This is what makes it safe to run as a real backfill across all ~37,773
events rather than the lazy on-demand approach §7.5 locked for the (expensive, API-
calling) minute-level historical comparison - that restriction was specifically about
avoiding ~37K new Polygon minute-bar pulls, which doesn't apply here at all.

A 90-calendar-day-since-event heuristic decides whether an event's observation window
counts as settled enough to finalize (analysis_computed_at gets set) vs. still worth
re-checking later (left NULL, so a future run recomputes it) - grounded in PEAD's own
~60-trading-day drift window from the research (RESEARCH_institutional_methodology.md
§8.1), giving real room for the pattern to play out before calling it final.

Does NOT extend price_path with new daily bars - that's track_live_events.py's job,
scoped only to events this pipeline itself created (daily incremental ingest for the
OLD historical backfill is still out of scope, per the base v2 spec's non-goals).
"""
import os
import logging
import datetime
import pymysql

from event_analysis import retracement_fraction, classify_shape, classify_post_settlement

DB_CONF = dict(host="localhost", user="huntharvest_app",
               password=os.getenv("DB_PASS", "aFLzUDDru0Spair4MduIQjeg"),
               database="huntharvest", cursorclass=pymysql.cursors.DictCursor)

WINDOW_SETTLED_DAYS = 90

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("backfill_analysis")


def analyze_event(cur, ev):
    cur.execute("SELECT day_offset, close FROM price_path WHERE event_id=%s ORDER BY day_offset", (ev["id"],))
    path = [(r["day_offset"], float(r["close"])) for r in cur.fetchall() if r["close"] is not None]
    if not path or not ev["prior_close"]:
        return None

    final_offset, final_close = path[-1]
    current_pct = (final_close - float(ev["prior_close"])) / float(ev["prior_close"]) * 100
    frac = retracement_fraction(float(ev["move_pct"]), current_pct)

    window_elapsed = (datetime.date.today() - ev["event_date"]).days >= WINDOW_SETTLED_DAYS
    mirror_days = ev["days_to_revert"] or 30
    outcome = classify_post_settlement(path, float(ev["prior_close"]), float(ev["move_pct"]),
                                        mirror_days, window_elapsed)
    shape = classify_shape(ev["direction"], frac, ev["days_to_revert"], outcome)
    return frac, shape, outcome, window_elapsed


def main():
    conn = pymysql.connect(**DB_CONF)
    cur = conn.cursor()

    cur.execute("SELECT id, ticker, direction, event_date, prior_close, move_pct, days_to_revert "
                "FROM events WHERE analysis_computed_at IS NULL")
    events = cur.fetchall()
    log.info(f"{len(events)} events need analysis (not yet finalized)")

    done, finalized, skipped = 0, 0, 0
    for ev in events:
        try:
            result = analyze_event(cur, ev)
            if result is None:
                skipped += 1
                continue
            frac, shape, outcome, window_elapsed = result
            computed_at = datetime.datetime.now() if window_elapsed else None
            cur.execute("""
                UPDATE events SET retracement_fraction=%s, shape_category=%s,
                    post_settlement_outcome=%s, analysis_computed_at=%s
                WHERE id=%s
            """, (frac, shape, outcome, computed_at, ev["id"]))
            done += 1
            if window_elapsed:
                finalized += 1
            if done % 500 == 0:
                conn.commit()
                log.info(f"[{done}/{len(events)}] processed ({finalized} finalized so far)")
        except Exception as e:
            log.error(f"{ev['ticker']} {ev['event_date']} FAILED: {e}")

    conn.commit()
    log.info(f"DONE. {done} analyzed ({finalized} finalized, {done - finalized} still monitoring), "
             f"{skipped} skipped (no price_path/prior_close)")
    conn.close()


if __name__ == "__main__":
    main()
