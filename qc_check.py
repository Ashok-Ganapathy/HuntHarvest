#!/usr/bin/env python3
"""
HuntHarvest v2 — QC checklist, run after every backfill.
Mirrors the discipline from v1's spec doc (which had a QC checklist v1 itself failed —
its flagship proof case, SEZL 2025-08-08, was missing from the database). This version
checks structural correctness, not just "does count > 0".
"""
import os
import pymysql
import numpy as np

DB_CONF = dict(host="localhost", user="huntharvest_app",
               password=os.getenv("DB_PASS", "aFLzUDDru0Spair4MduIQjeg"),
               database="huntharvest", cursorclass=pymysql.cursors.DictCursor)


def check(label, passed, detail=""):
    print(f"{'PASS' if passed else 'FAIL'}  {label}" + (f"  — {detail}" if detail else ""))
    return passed


def main():
    conn = pymysql.connect(**DB_CONF)
    cur = conn.cursor()
    all_ok = True

    cur.execute("SELECT COUNT(*) c FROM events")
    total = cur.fetchone()["c"]
    all_ok &= check("Total event count > 0", total > 0, f"{total} events")

    # v1's flagship proof case - the one that was missing and started this whole rebuild.
    # Close-to-close definition differs slightly from v1's low-vs-prior-close, so this
    # checks for a SEZL event *near* that date/magnitude, not an exact match.
    cur.execute("""
        SELECT event_date, move_pct, market_cap_at_event FROM events
        WHERE ticker='SEZL' AND event_date BETWEEN '2025-08-05' AND '2025-08-12' AND direction='drop'
    """)
    sezl = cur.fetchall()
    all_ok &= check("SEZL has a qualifying drop near 2025-08-08 (v1's missing proof case)",
                     len(sezl) > 0, str(sezl))

    # No event should violate its own direction's threshold
    cur.execute("SELECT config_value FROM config WHERE config_key='drop_threshold_pct'")
    drop_th = float(cur.fetchone()["config_value"])
    cur.execute("SELECT config_value FROM config WHERE config_key='gain_threshold_pct'")
    gain_th = float(cur.fetchone()["config_value"])
    cur.execute("SELECT COUNT(*) c FROM events WHERE direction='drop' AND move_pct > %s", (drop_th,))
    bad_drops = cur.fetchone()["c"]
    all_ok &= check("No drop event violates the drop threshold", bad_drops == 0, f"{bad_drops} violations")
    cur.execute("SELECT COUNT(*) c FROM events WHERE direction='gain' AND move_pct < %s", (gain_th,))
    bad_gains = cur.fetchone()["c"]
    all_ok &= check("No gain event violates the gain threshold", bad_gains == 0, f"{bad_gains} violations")

    # The core bug this rebuild was about: market cap must be point-in-time, not static per ticker
    cur.execute("""
        SELECT ticker, COUNT(DISTINCT market_cap_at_event) distinct_caps, COUNT(*) n
        FROM events GROUP BY ticker HAVING n >= 3 AND distinct_caps = 1
    """)
    static_mcap_tickers = cur.fetchall()
    all_ok &= check("No ticker with 3+ events has a single frozen market_cap value (v1's core bug)",
                     len(static_mcap_tickers) == 0, f"{len(static_mcap_tickers)} tickers still static")

    # Every event must clear the market cap floor - the point-in-time filter should have
    # already excluded anything under it, this re-checks nothing slipped through
    cur.execute("SELECT config_value FROM config WHERE config_key='market_cap_min'")
    mcap_min = float(cur.fetchone()["config_value"])
    cur.execute("SELECT COUNT(*) c FROM events WHERE market_cap_at_event < %s", (mcap_min,))
    under_floor = cur.fetchone()["c"]
    all_ok &= check("No event has market_cap_at_event under the configured floor",
                     under_floor == 0, f"{under_floor} events under floor")

    # No duplicate ticker+date rows
    cur.execute("""
        SELECT ticker, event_date, COUNT(*) c FROM events GROUP BY ticker, event_date HAVING c > 1
    """)
    dupes = cur.fetchall()
    all_ok &= check("No duplicate ticker+event_date rows", len(dupes) == 0, f"{len(dupes)} duplicates")

    # Every event should have at least one price_path row (day 0)
    cur.execute("""
        SELECT COUNT(*) c FROM events e
        WHERE NOT EXISTS (SELECT 1 FROM price_path p WHERE p.event_id = e.id)
    """)
    orphans = cur.fetchone()["c"]
    all_ok &= check("Every event has at least one price_path row", orphans == 0, f"{orphans} orphaned events")

    # Sanity distribution check - flag extreme outliers for manual review (not necessarily
    # wrong, but worth a human glance - e.g. a >75% single-day move is rare and could be a
    # stock-split adjustment artifact rather than a real move)
    cur.execute("""
        SELECT ticker, event_date, direction, move_pct FROM events
        WHERE ABS(move_pct) > 75 ORDER BY ABS(move_pct) DESC LIMIT 10
    """)
    extreme = cur.fetchall()
    print(f"\nINFO  {len(extreme)} events with |move_pct| > 75% (not a failure, just worth a manual glance):")
    for r in extreme:
        print(f"      {r['ticker']} {r['event_date']} {r['direction']} {r['move_pct']:.1f}%")

    cur.execute("SELECT direction, COUNT(*) c, SUM(reverted) rev FROM events GROUP BY direction")
    print("\nSummary by direction:")
    for r in cur.fetchall():
        print(f"  {r['direction']}: {r['c']} events, {r['rev']} reverted ({r['rev']/r['c']*100:.1f}%)")

    cur.execute("SELECT COUNT(DISTINCT ticker) c FROM events")
    print(f"\nDistinct tickers with qualifying events: {cur.fetchone()['c']}")

    conn.close()
    print(f"\n{'ALL CHECKS PASSED' if all_ok else 'SOME CHECKS FAILED — review above before trusting this data'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    exit(main())
