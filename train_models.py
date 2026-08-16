#!/usr/bin/env python3
"""
HuntHarvest v2 — pooled model training.
Locked design: see PROJECT_STATE.md.

Separate models for drops vs. gains (locked spec: "different treatment, not mirrored" —
a drop reverting means "bounced back", a gain reverting means "faded"; these are different
questions even though they share the same underlying `reverted`/`days_to_revert` columns).

Per direction: a classifier for "did it revert" (bounce for drops, fade for gains), and a
regressor for "how many days" trained ONLY on rows that actually reverted — right-censored
rows (never reverted within available data) have no real days-to-revert value, so forcing a
number into the regression target would bias it. This is a deliberately simpler stand-in for
a full survival model (e.g. lifelines' Cox regression) — noted as a possible upgrade later,
not built now, per "no enterprise overhead" for a personal tool.

Per-ticker case list (the OTHER half of the hybrid approach) is not a trained model at all —
it's computed live from the `events` table by the app, not here.
"""
import os
import sys
import pickle
import logging
import datetime
import pymysql
import numpy as np
import pandas as pd
from sqlalchemy import create_engine
from sklearn.ensemble import GradientBoostingClassifier, GradientBoostingRegressor
from sklearn.model_selection import train_test_split
from sklearn.metrics import classification_report, r2_score
from sklearn.utils.class_weight import compute_sample_weight

DB_CONF = dict(host="localhost", user="huntharvest_app",
               password=os.getenv("DB_PASS", "aFLzUDDru0Spair4MduIQjeg"),
               database="huntharvest")
DB_URL = f"mysql+pymysql://{DB_CONF['user']}:{DB_CONF['password']}@{DB_CONF['host']}/{DB_CONF['database']}"
MODEL_DIR = os.path.dirname(os.path.abspath(__file__))
FEATURES = ["rsi_14", "atr_14", "price_vs_sma50", "price_vs_sma200", "mom_3m",
            "volume_ratio", "market_relative_return", "sector_relative_return", "log_mcap"]

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("train")


def load_events():
    engine = create_engine(DB_URL)
    df = pd.read_sql("SELECT * FROM events", engine)
    df["log_mcap"] = np.log10(df["market_cap_at_event"].astype(float).clip(lower=1))
    return df


def train_direction(df, direction, model_version):
    sub = df[df["direction"] == direction].copy()
    sub = sub.dropna(subset=FEATURES)
    if len(sub) < 30:
        log.warning(f"{direction}: only {len(sub)} usable rows (need 30+), skipping training")
        return None, None, sub

    X = sub[FEATURES].values
    y_reverted = sub["reverted"].astype(int).values

    Xtr, Xte, ytr, yte = train_test_split(X, y_reverted, test_size=0.2, random_state=42,
                                           stratify=y_reverted if y_reverted.sum() >= 5 else None)
    # GradientBoostingClassifier has no class_weight param (unlike RandomForest) - without
    # this, the minority class (the ~20% that DON'T revert - arguably the more useful case
    # to flag correctly) gets essentially ignored in favor of majority-class accuracy.
    sw = compute_sample_weight("balanced", ytr)
    clf = GradientBoostingClassifier(n_estimators=200, max_depth=3, random_state=42)
    clf.fit(Xtr, ytr, sample_weight=sw)
    log.info(f"{direction} classifier ({'bounce' if direction=='drop' else 'fade'} prob), class-balanced:")
    log.info("\n" + classification_report(yte, clf.predict(Xte), zero_division=0))

    reverted_sub = sub[sub["reverted"] == 1].dropna(subset=["days_to_revert"])
    reg = None
    if len(reverted_sub) >= 20:
        Xr = reverted_sub[FEATURES].values
        yr = reverted_sub["days_to_revert"].astype(float).values
        Xr_tr, Xr_te, yr_tr, yr_te = train_test_split(Xr, yr, test_size=0.2, random_state=42)
        reg = GradientBoostingRegressor(n_estimators=200, max_depth=3, random_state=42)
        reg.fit(Xr_tr, yr_tr)
        log.info(f"{direction} days-to-revert regressor R2: "
                 f"train={reg.score(Xr_tr, yr_tr):.3f} test={r2_score(yr_te, reg.predict(Xr_te)):.3f}"
                 f" (n={len(reverted_sub)} reverted-only rows)")
    else:
        log.warning(f"{direction}: only {len(reverted_sub)} reverted rows, skipping days-to-revert regressor")

    with open(f"{MODEL_DIR}/{direction}_clf.pkl", "wb") as f:
        pickle.dump(clf, f)
    if reg:
        with open(f"{MODEL_DIR}/{direction}_reg.pkl", "wb") as f:
            pickle.dump(reg, f)

    return clf, reg, sub


def write_predictions(conn, df_all, clf, reg, direction, model_version):
    if clf is None:
        return
    sub = df_all[(df_all["direction"] == direction)].dropna(subset=FEATURES)
    if sub.empty:
        return
    X = sub[FEATURES].values
    bounce_prob = clf.predict_proba(X)[:, 1]
    expected_days = reg.predict(X) if reg is not None else [None] * len(sub)

    cur = conn.cursor()
    rows = [(int(eid), model_version, float(bp),
             float(ed) if ed is not None else None)
            for eid, bp, ed in zip(sub["id"], bounce_prob, expected_days)]
    cur.executemany("""
        INSERT INTO predictions (event_id, model_version, bounce_probability, expected_days_to_revert)
        VALUES (%s,%s,%s,%s)
        ON DUPLICATE KEY UPDATE model_version=VALUES(model_version),
            bounce_probability=VALUES(bounce_probability),
            expected_days_to_revert=VALUES(expected_days_to_revert),
            predicted_at=CURRENT_TIMESTAMP
    """, rows)
    conn.commit()
    log.info(f"{direction}: wrote {len(rows)} predictions")


EDGE_WINDOW_MONTHS = 6          # each comparison window's length
EDGE_SETTLE_DAYS = 90           # only compare events old enough for reverted/not to be
                                 # meaningfully final - matches backfill_event_analysis.py's
                                 # own settled-window convention, grounded in PEAD's ~60
                                 # trading-day drift window (research §8.1)
EDGE_MIN_N = 20                 # per window, below this the comparison isn't trustworthy
EDGE_HOLDING_BAND = 0.05        # win-rate delta smaller than this counts as "holding"


def check_edge_strength(conn, model_version):
    """Deliverable #7 (spec §2, §6 Stage 3) - operationalizes the research finding that
    PEAD-style edges decay over time and shouldn't be assumed constant (research doc
    §8.1). Compares recent vs. prior reverted-rate across BOTH directions pooled (the
    concern is whether the underlying reversion pattern itself is changing, not
    profitability) and logs whether the edge looks like it's holding, weakening, or
    improving - surfaced in the app, not just logged and forgotten."""
    cutoff = datetime.date.today() - datetime.timedelta(days=EDGE_SETTLE_DAYS)
    recent_end = cutoff
    recent_start = recent_end - datetime.timedelta(days=30 * EDGE_WINDOW_MONTHS)
    prior_end = recent_start
    prior_start = prior_end - datetime.timedelta(days=30 * EDGE_WINDOW_MONTHS)

    cur = conn.cursor()

    def window_stats(start, end):
        cur.execute("SELECT COUNT(*) n, SUM(reverted) wins FROM events WHERE event_date >= %s AND event_date < %s",
                    (start, end))
        n, wins = cur.fetchone()  # plain tuple cursor here, not DictCursor - matches
        n = n or 0                # this file's existing convention (see load_config())
        wins = wins or 0
        return n, (wins / n if n else None)

    recent_n, recent_wr = window_stats(recent_start, recent_end)
    prior_n, prior_wr = window_stats(prior_start, prior_end)

    if recent_n < EDGE_MIN_N or prior_n < EDGE_MIN_N:
        trend = "insufficient_data"
    else:
        diff = recent_wr - prior_wr
        trend = "holding" if abs(diff) < EDGE_HOLDING_BAND else ("weakening" if diff < 0 else "improving")

    cur.execute("""
        INSERT INTO edge_strength_log
        (model_version, recent_window_start, recent_window_end, recent_win_rate, recent_n,
         prior_window_start, prior_window_end, prior_win_rate, prior_n, trend)
        VALUES (%s,%s,%s,%s,%s, %s,%s,%s,%s, %s)
    """, (model_version, recent_start, recent_end, recent_wr, recent_n,
          prior_start, prior_end, prior_wr, prior_n, trend))
    conn.commit()
    log.info(f"Edge strength: recent={recent_wr} (n={recent_n}), prior={prior_wr} (n={prior_n}), trend={trend}")


def main():
    conn = pymysql.connect(**DB_CONF)
    df = load_events()
    log.info(f"Loaded {len(df)} total events ({(df['direction']=='drop').sum()} drops, "
             f"{(df['direction']=='gain').sum()} gains)")

    model_version = datetime.date.today().isoformat()

    drop_clf, drop_reg, drop_sub = train_direction(df, "drop", model_version)
    gain_clf, gain_reg, gain_sub = train_direction(df, "gain", model_version)

    write_predictions(conn, df, drop_clf, drop_reg, "drop", model_version)
    write_predictions(conn, df, gain_clf, gain_reg, "gain", model_version)

    check_edge_strength(conn, model_version)

    conn.close()
    log.info("DONE")


if __name__ == "__main__":
    main()
