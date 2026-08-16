#!/usr/bin/env python3
"""
HuntHarvest v2 — Phase 3 event analysis (Specs/SPEC_daily_pipeline.md §5, §7).
Shared, side-effect-free classification functions used by settle_watch.py (new live
events) and backfill_event_analysis.py (existing historical events). Kept separate from
both so the math has one place to live and one place to get it wrong.

Locked 2026-08-16:
  - AR/CAR estimation window: 252 trading days, ending 30 days before the event date,
    minimum 120 days required or AR/CAR is skipped for that event (spec §5, §7.4).
  - AR/CAR runs ALONGSIDE the already-live raw-% threshold (Phase 2), not replacing it.
  - PARTIAL-REVERSAL band: retracement_fraction in [0.30, 1.00).
  - V-SHAPED vs GRIND-BACK split: 10 trading days to revert.
"""
import datetime
import numpy as np

from ingest_historical import fetch_daily_bars, MARKET_BENCHMARK

AR_WINDOW_TRADING_DAYS = 252
AR_WINDOW_GAP_DAYS = 30          # calendar days between window end and the event
AR_WINDOW_MIN_TRADING_DAYS = 120
PARTIAL_REVERSAL_MIN = 0.30
V_SHAPED_MAX_DAYS = 10
GAVE_BACK_RETRACE_RATIO = 0.5    # gives back half of its peak retracement -> dead-cat


def estimate_alpha_beta(ticker, as_of_date):
    """Market-model regression (R_stock = alpha + beta*R_market + eps) over a trailing
    252-trading-day window ending 30 days before as_of_date. Returns (alpha, beta,
    window_start, window_end) or (None, None, None, None) if there isn't enough clean
    history (min 120 trading days) - callers must handle this gracefully, not assume
    AR/CAR is always available."""
    if isinstance(as_of_date, str):
        as_of_date = datetime.date.fromisoformat(as_of_date)
    window_end = as_of_date - datetime.timedelta(days=AR_WINDOW_GAP_DAYS)
    fetch_start = window_end - datetime.timedelta(days=int(AR_WINDOW_TRADING_DAYS * 1.55) + 20)

    stock_df = fetch_daily_bars(ticker, fetch_start.isoformat(), window_end.isoformat())
    mkt_df = fetch_daily_bars(MARKET_BENCHMARK, fetch_start.isoformat(), window_end.isoformat())
    if stock_df is None or mkt_df is None:
        return None, None, None, None

    merged = stock_df.merge(mkt_df, on="date", suffixes=("_s", "_m")).sort_values("date")
    if len(merged) < AR_WINDOW_MIN_TRADING_DAYS + 1:
        return None, None, None, None
    merged = merged.tail(AR_WINDOW_TRADING_DAYS + 1)  # +1 so returns cover the full window

    stock_ret = merged["close_s"].pct_change().dropna().values * 100
    mkt_ret = merged["close_m"].pct_change().dropna().values * 100
    if len(stock_ret) < AR_WINDOW_MIN_TRADING_DAYS:
        return None, None, None, None

    var_m = np.var(mkt_ret)
    if var_m == 0:
        return None, None, None, None
    beta = float(np.cov(stock_ret, mkt_ret)[0, 1] / var_m)
    alpha = float(np.mean(stock_ret) - beta * np.mean(mkt_ret))
    window_start = merged["date"].iloc[0]
    return alpha, beta, window_start, window_end


def compute_car(alpha, beta, stock_daily_returns, market_daily_returns):
    """Cumulative abnormal return: sum of (actual - expected) daily returns, where
    expected = alpha + beta*market_return. Both return lists must be same length and
    aligned day-for-day. Returns None if inputs are empty/mismatched."""
    if not stock_daily_returns or not market_daily_returns or len(stock_daily_returns) != len(market_daily_returns):
        return None
    ar = [s - (alpha + beta * m) for s, m in zip(stock_daily_returns, market_daily_returns)]
    return float(np.sum(ar))


def retracement_fraction(move_pct, current_pct_from_prior_close):
    """How much of the original move has been retraced, as a fraction. 0 = no
    recovery at all (still at the reaction extreme), 1.0 = fully back at prior_close,
    >1.0 = overshot past prior_close (EXTENDED). Same formula works for both
    directions since move_pct and current_pct_from_prior_close share sign convention."""
    if move_pct is None or current_pct_from_prior_close is None or move_pct == 0:
        return None
    return 1 - (current_pct_from_prior_close / move_pct)


def classify_shape(direction, retrace_frac, days_to_revert, post_settlement_outcome):
    """Rule-based taxonomy (spec §7.3). Gains and drops are NOT mirrored (locked in the
    base v2 spec) - gains use CONTINUATION/FADE, drops use the fuller V-SHAPED/GRIND-
    BACK/DEAD-CAT/NO-RECOVERY set. PARTIAL-REVERSAL is shared middle-band language for
    both, per the assumption correction that this is the modal outcome, not an edge
    case."""
    if retrace_frac is None:
        return None
    if direction == "gain":
        if retrace_frac < PARTIAL_REVERSAL_MIN:
            return "continuation"
        if retrace_frac < 1.0:
            return "partial_reversal"
        return "fade"
    # drop
    if retrace_frac < PARTIAL_REVERSAL_MIN:
        return "no_recovery"
    if retrace_frac < 1.0:
        return "partial_reversal"
    if post_settlement_outcome == "gave_back":
        return "dead_cat"
    return "grind_back" if (days_to_revert and days_to_revert > V_SHAPED_MAX_DAYS) else "v_shaped"


def classify_post_settlement(path_closes, prior_close, move_pct, mirror_window_days, window_fully_elapsed):
    """§7.1, widened 2026-08-16 to cover partial reversals too, not just full reverts.
    path_closes: list of (day_offset, close) tuples, day 0 = event day, in order.
    Finds the peak retracement_fraction reached, then checks what happens across the
    remaining mirrored window: HELD (stayed at/beyond peak), GAVE_BACK (fell back past
    half of peak, adverse direction - dead-cat definition locked 2026-08-16), EXTENDED
    (kept moving favorably beyond peak), or MONITORING (window hasn't elapsed yet)."""
    if not path_closes or move_pct is None or not prior_close:
        return None
    fracs = [(off, retracement_fraction(move_pct, (c - prior_close) / prior_close * 100))
             for off, c in path_closes if c is not None]
    fracs = [(off, f) for off, f in fracs if f is not None]
    if not fracs:
        return None

    peak_offset, peak_frac = max(fracs, key=lambda t: t[1])
    after_peak = [f for off, f in fracs if off > peak_offset]
    last_offset = fracs[-1][0]

    if not after_peak:
        # No data past the peak at all. Two genuinely different cases, not one:
        # historical events insert_event_and_path() (ingest_historical.py) stops
        # recording the DAY reversion is first detected - so for a reverted event,
        # "no data past peak" almost always means the path was truncated exactly at
        # the crossing, not that we verified it held. Only return a real answer when
        # the peak isn't the very last point we have visibility into; otherwise this
        # is honestly unknown, not "held" by default.
        if peak_offset == last_offset:
            return None
        return "monitoring" if not window_fully_elapsed else "held"
    min_after = min(after_peak)
    max_after = max(after_peak)
    if min_after <= peak_frac * GAVE_BACK_RETRACE_RATIO:
        return "gave_back"
    if max_after > peak_frac * 1.1:  # meaningfully past peak, not just noise
        return "extended"
    if not window_fully_elapsed and (fracs[-1][0] - peak_offset) < mirror_window_days:
        return "monitoring"
    return "held"


def classify_reaction_accuracy(early_pct, checkpoint_pct, tolerance_ratio=0.20):
    """§7.2 - compares the early, noisy read (premarket/immediate after-hours) against
    the genuine locked checkpoint. Bucketed, not a black-box score."""
    if early_pct is None or checkpoint_pct is None or checkpoint_pct == 0:
        return None
    same_direction = (early_pct >= 0) == (checkpoint_pct >= 0)
    if not same_direction and abs(early_pct) >= 1.0:
        return "reversed"
    ratio = abs(early_pct) / abs(checkpoint_pct)
    if abs(ratio - 1) <= tolerance_ratio:
        return "accurate"
    return "overreacted" if ratio > 1 else "underreacted"
