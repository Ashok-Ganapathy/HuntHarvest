#!/usr/bin/env python3
"""
HuntHarvest v2 — live reaction polling.
Phase 2 (core mechanism) of the daily same-day earnings pipeline
(Specs/SPEC_daily_pipeline.md, Stage 2). Meant to run every few minutes via
huntharvest-livepoll.timer - idempotent and cheap to re-run; it no-ops fast whenever
there's nothing in an active poll window.

Scope of this pass (deliverables #2 and #4 from spec §2 - NOT #3, the historical
comparison view, which is a real next increment, not built here):
  - Track A (bmo): poll premarket through 60 min after open on watch_date. Checkpoint
    locks the moment the move clears the qualifying threshold (spec §4) - can happen
    pre-open. If the poll window elapses with no qualifying move, status -> 'dropped'.
  - Track B (amc): poll from report_date's after-hours print through watch_date's
    open+60min. Checkpoint is locked SPECIFICALLY to the next day's regular-session
    open print (spec §4's locked design) - the after-hours/premarket data is still
    captured (deliverable #2's "forming" data) but never defines the checkpoint.
  - Once a checkpoint locks and the move qualifies (|pct| >= config threshold), run the
    EXISTING trained model on the baseline features already captured in Phase 1 -
    deliverable #4, the confirmed event card. Does NOT yet write into events/price_path
    (that's Stage 3 / Phase 3, deferred - needs the AR/CAR window decided first).

AR/CAR (spec §5) is not used for the threshold check yet - single-figure raw % against
config's drop/gain thresholds, per §7.4's "AR fully replaces... or runs alongside" still
being an open decision.
"""
import os
import pickle
import logging
import datetime
import zoneinfo
import requests
import numpy as np
import pymysql

from notify import send_push_via_agstox

POLYGON_KEY = os.getenv("POLYGON_KEY", "74DMSl0HQK1PSLhQ4YVPW2sVq9HIgJ9I")
BASE = "https://api.polygon.io"
MODEL_DIR = os.path.dirname(os.path.abspath(__file__))
ET = zoneinfo.ZoneInfo("America/New_York")

# Same FEATURES list/order as train_models.py - the models were trained on exactly
# this shape, in exactly this order.
FEATURES = ["rsi_14", "atr_14", "price_vs_sma50", "price_vs_sma200", "mom_3m",
            "volume_ratio", "market_relative_return", "sector_relative_return", "log_mcap"]

DB_CONF = dict(host="localhost", user="huntharvest_app",
               password=os.getenv("DB_PASS", "aFLzUDDru0Spair4MduIQjeg"),
               database="huntharvest", cursorclass=pymysql.cursors.DictCursor)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("live_poll")


def fetch_minute_bars(ticker, date_str):
    """Real 1-min bars including extended hours - same endpoint/params already
    proven live in AGSTOX's fetch_intraday_bars() (agstox_exchange.py:33487),
    confirmed available on the current Polygon plan (spec §3)."""
    url = (f"{BASE}/v2/aggs/ticker/{ticker}/range/1/minute/{date_str}/{date_str}"
           f"?adjusted=true&sort=asc&limit=1000&extended_hours=true&apiKey={POLYGON_KEY}")
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        return r.json().get("results") or []
    except Exception as e:
        log.warning(f"fetch_minute_bars({ticker}, {date_str}) failed: {e}")
        return []


def session_for(dt_et):
    t = dt_et.time()
    if t < datetime.time(9, 30):
        return "premarket"
    if t < datetime.time(16, 0):
        return "regular"
    return "afterhours"


def load_config(conn):
    cur = conn.cursor()
    cur.execute("SELECT config_key, config_value FROM config")
    return {r["config_key"]: r["config_value"] for r in cur.fetchall()}


def load_model(direction, kind):
    path = os.path.join(MODEL_DIR, f"{direction}_{kind}.pkl")
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        return pickle.load(f)


def predict(row):
    """Runs the existing trained model on the baseline features already captured in
    Phase 1 - deliverable #4. Direction picked from the checkpoint's own sign, not a
    separate guess. Returns (probability, expected_days) or (None, None) if a required
    feature is missing or the model file doesn't exist (e.g. no regressor trained for
    that direction - train_models.py skips it below 20 reverted rows)."""
    direction = "drop" if row["checkpoint_pct"] < 0 else "gain"
    feats = []
    for key in FEATURES[:-1]:
        v = row.get(key)
        if v is None:
            log.warning(f"{row['ticker']}: missing feature '{key}', skipping prediction")
            return direction, None, None
        feats.append(float(v))
    mcap = row.get("market_cap_at_scan")
    log_mcap = np.log10(max(float(mcap), 1)) if mcap else None
    if log_mcap is None:
        log.warning(f"{row['ticker']}: missing market_cap_at_scan, skipping prediction")
        return direction, None, None
    feats.append(log_mcap)

    clf = load_model(direction, "clf")
    reg = load_model(direction, "reg")
    if clf is None:
        log.warning(f"{direction}_clf.pkl not found, skipping prediction")
        return direction, None, None
    X = np.array([feats])
    prob = float(clf.predict_proba(X)[0, 1])
    exp_days = float(reg.predict(X)[0]) if reg is not None else None
    return direction, prob, exp_days


def active_candidates(conn, today):
    """Rows worth polling right now: Track A on its own watch_date, or Track B from
    the evening it reports (report_date) through its watch_date's open+60min."""
    cur = conn.cursor()
    cur.execute("""
        SELECT * FROM daily_watch
        WHERE status='watching' AND (watch_date=%s OR report_date=%s)
    """, (today, today))
    return cur.fetchall()


def in_poll_window(row, now_et):
    """True if this row should be actively polled right now, given its track and the
    current ET time. Track A: premarket through open+60min, watch_date only. Track B:
    report_date's after-hours (4pm+) through watch_date's open+60min - captures the
    full overnight arc even though only the final open print defines the checkpoint."""
    today_et = now_et.date()
    open_plus_60 = datetime.time(10, 30)
    if row["track"] == "bmo":
        if today_et != row["watch_date"]:
            return False
        return now_et.time() < open_plus_60
    else:  # amc
        if today_et == row["report_date"]:
            return now_et.time() >= datetime.time(16, 0)
        if today_et == row["watch_date"]:
            return now_et.time() < open_plus_60
        return False


def window_elapsed(row, now_et):
    """True once it's safe to give up on this row (no qualifying move found)."""
    today_et = now_et.date()
    cutoff = datetime.time(10, 30)
    if row["track"] == "bmo":
        return today_et == row["watch_date"] and now_et.time() >= cutoff
    else:
        return today_et == row["watch_date"] and now_et.time() >= cutoff


def poll_row(conn, row, thresholds, now_et):
    cur = conn.cursor()
    dates_to_fetch = []
    if row["track"] == "bmo":
        dates_to_fetch = [row["watch_date"].isoformat()]
    else:
        dates_to_fetch = sorted({row["report_date"].isoformat(), row["watch_date"].isoformat()})

    prior_close = float(row["prior_close"])
    all_bars = []
    for d in dates_to_fetch:
        all_bars.extend(fetch_minute_bars(row["ticker"], d))
    if not all_bars:
        return

    ticks = []
    for b in all_bars:
        ts_utc = datetime.datetime.fromtimestamp(b["t"] / 1000, tz=datetime.timezone.utc)
        dt_et = ts_utc.astimezone(ET)
        price = b["c"]
        pct = (price - prior_close) / prior_close * 100 if prior_close else None
        ticks.append({
            "ts_utc": ts_utc.strftime("%Y-%m-%d %H:%M:%S"), "price": price,
            "volume": b.get("v"), "pct": pct, "session": session_for(dt_et), "dt_et": dt_et,
        })

    if ticks:
        cur.executemany("""
            INSERT INTO live_reaction_ticks (watch_id, ts_utc, price, volume, pct_from_baseline, session)
            VALUES (%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE price=VALUES(price), volume=VALUES(volume),
                pct_from_baseline=VALUES(pct_from_baseline), session=VALUES(session)
        """, [(row["id"], t["ts_utc"], t["price"], t["volume"], t["pct"], t["session"]) for t in ticks])
        conn.commit()

    drop_thr = float(thresholds["drop_threshold_pct"])
    gain_thr = float(thresholds["gain_threshold_pct"])
    checkpoint = None

    if row["track"] == "bmo":
        # Checkpoint locks the moment ANY tick (premarket or regular) clears threshold.
        qualifying = [t for t in ticks if t["pct"] is not None and (t["pct"] <= drop_thr or t["pct"] >= gain_thr)]
        if qualifying:
            checkpoint = min(qualifying, key=lambda t: t["dt_et"])
    else:
        # Checkpoint is locked to the watch_date open print specifically, regardless
        # of after-hours/premarket noise (spec §4 locked design).
        opens = [t for t in ticks if t["dt_et"].date() == row["watch_date"] and t["dt_et"].time() >= datetime.time(9, 30)]
        if opens:
            checkpoint = min(opens, key=lambda t: t["dt_et"])

    if checkpoint is not None:
        qualifies = checkpoint["pct"] is not None and (checkpoint["pct"] <= drop_thr or checkpoint["pct"] >= gain_thr)
        if qualifies:
            pred_row = dict(row)
            pred_row["checkpoint_pct"] = checkpoint["pct"]
            direction, prob, exp_days = predict(pred_row)
            cur.execute("""
                UPDATE daily_watch SET status='settled', checkpoint_price=%s, checkpoint_pct=%s,
                    checkpoint_locked_at=%s, predicted_probability=%s, predicted_expected_days=%s,
                    predicted_direction=%s
                WHERE id=%s
            """, (checkpoint["price"], checkpoint["pct"], checkpoint["ts_utc"],
                  prob, exp_days, direction, row["id"]))
            conn.commit()
            log.info(f"{row['ticker']} ({row['track'].upper()}): SETTLED, checkpoint={checkpoint['pct']:.1f}%, "
                     f"model_prob={prob}, exp_days={exp_days}")

            verb = "Bounce" if direction == "drop" else "Fade"
            title = f"{row['ticker']} {direction.upper()} {checkpoint['pct']:+.1f}%"
            body = (f"${checkpoint['price']:.2f}. "
                    + (f"{verb} prob {prob*100:.0f}%" + (f", ~{exp_days:.0f}d" if exp_days is not None else "")
                       if prob is not None else "model unavailable"))
            send_push_via_agstox(title, body)  # best-effort - never blocks/breaks the settle on failure
        elif row["track"] == "amc":
            # AMC's checkpoint is a fixed point in time (the open print) - once we have
            # it and it doesn't qualify, this row is done, not still "watching".
            cur.execute("UPDATE daily_watch SET status='dropped' WHERE id=%s", (row["id"],))
            conn.commit()
            log.info(f"{row['ticker']} (AMC): open print {checkpoint['pct']:.1f}% - below threshold, dropped")
    elif window_elapsed(row, now_et):
        cur.execute("UPDATE daily_watch SET status='dropped' WHERE id=%s", (row["id"],))
        conn.commit()
        log.info(f"{row['ticker']} ({row['track'].upper()}): poll window elapsed, no qualifying move - dropped")


def main():
    conn = pymysql.connect(**DB_CONF)
    now_et = datetime.datetime.now(tz=ET)
    today = now_et.date()
    thresholds = load_config(conn)

    candidates = active_candidates(conn, today)
    active = [r for r in candidates if in_poll_window(r, now_et)]
    log.info(f"{len(candidates)} watching rows touch today; {len(active)} in an active poll window right now")

    for row in active:
        try:
            poll_row(conn, row, thresholds, now_et)
        except Exception as e:
            log.error(f"{row['ticker']} poll FAILED: {e}")

    conn.close()


if __name__ == "__main__":
    main()
