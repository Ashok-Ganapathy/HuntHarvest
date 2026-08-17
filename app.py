#!/usr/bin/env python3
"""
HuntHarvest v2 — FastAPI serving layer.
Locked design: see PROJECT_STATE.md.

Auth: HTTP Basic against bcrypt hashes in the `users` table (not plaintext-in-code like v1).
Simple by design for a personal tool - no session/token management, browser caches
Basic Auth credentials, HTTPS (via Let's Encrypt once DNS points here) protects transit.

Output format follows the locked spec: per-event, this ticker's own case-list history
(not a trained-per-stock model — sample size is too small, see PROJECT_STATE.md) alongside
the pooled model's read, a confidence flag when the ticker's own sample is thin, and a
suggested action that treats drops ("does it bounce") and gains ("does it hold or fade")
differently rather than mirroring the same logic — this is a decision aid, not a guarantee.
"""
import os
import pathlib
import secrets
import datetime
import bcrypt
import pymysql
import numpy as np
from fastapi import FastAPI, Depends, HTTPException, Query
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from live_reaction_poll import fetch_minute_bars, session_for, ET
from daily_watch_scan import fetch_finviz_earnings_calendar, earnings_date, earnings_timing, next_trading_day

DB_CONF = dict(host="localhost", user="huntharvest_app",
               password=os.getenv("DB_PASS", "aFLzUDDru0Spair4MduIQjeg"),
               database="huntharvest", cursorclass=pymysql.cursors.DictCursor)

app = FastAPI(title="HuntHarvest v2")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
security = HTTPBasic()


def db():
    return pymysql.connect(**DB_CONF)


def get_user(credentials: HTTPBasicCredentials = Depends(security)):
    conn = db()
    try:
        cur = conn.cursor()
        cur.execute("SELECT username, password_hash, role FROM users WHERE username=%s", (credentials.username,))
        row = cur.fetchone()
        if not row or not bcrypt.checkpw(credentials.password.encode(), row["password_hash"].encode()):
            raise HTTPException(401, "invalid credentials", headers={"WWW-Authenticate": "Basic"})
        return row
    finally:
        conn.close()


def require_admin(user=Depends(get_user)):
    if user["role"] != "admin":
        raise HTTPException(403, "admin only")
    return user


@app.get("/api/me")
def me(user=Depends(get_user)):
    return {"username": user["username"], "role": user["role"]}


@app.get("/api/health")
def health():
    conn = db()
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) c FROM events")
        event_count = cur.fetchone()["c"]
        cur.execute("SELECT MAX(event_date) d FROM events")
        latest = cur.fetchone()["d"]
        return {"status": "ok", "event_count": event_count, "latest_event_date": str(latest) if latest else None}
    finally:
        conn.close()


def ticker_case_summary(cur, ticker, exclude_event_id=None):
    """This ticker's own historical events - the case-list half of the hybrid approach.
    Not a trained model - just a plain summary of what actually happened before."""
    cur.execute("""
        SELECT direction, reverted, days_to_revert, move_pct, event_date,
               shape_category, post_settlement_outcome, reaction_accuracy
        FROM events WHERE ticker=%s AND id != COALESCE(%s, -1)
        ORDER BY event_date
    """, (ticker, exclude_event_id))
    rows = cur.fetchall()
    if not rows:
        return {"sample_size": 0, "confidence": "none", "history": [],
                "summary": "No prior qualifying events for this ticker."}

    n = len(rows)
    reverted_n = sum(1 for r in rows if r["reverted"])
    days = [r["days_to_revert"] for r in rows if r["reverted"] and r["days_to_revert"] is not None]
    median_days = float(np.median(days)) if days else None
    confidence = "low" if n < 3 else ("medium" if n < 6 else "high")

    # A ticker's case history can mix drops and gains - "reverted" means something
    # different for each (bounced back up vs. faded back down), so a single verb
    # picked from just the last row would misdescribe the rest. Break out both.
    drops = [r for r in rows if r["direction"] == "drop"]
    gains = [r for r in rows if r["direction"] == "gain"]
    parts = []
    if drops:
        parts.append(f"{sum(1 for r in drops if r['reverted'])}/{len(drops)} drops bounced back")
    if gains:
        parts.append(f"{sum(1 for r in gains if r['reverted'])}/{len(gains)} gains faded back")
    summary = f"Last {n} qualifying events: " + ", ".join(parts)
    if median_days is not None:
        summary += f" (median {median_days:.0f} days when reverted)"

    # Deliverable #5 - shape distribution (spec §7.3), richer texture than a bare
    # revert/no-revert flag. Only counts rows the Phase 3 backfill has actually
    # classified (shape_category IS NOT NULL) - a thin/unanalyzed sample says so
    # plainly rather than pretending completeness.
    shapes = [r["shape_category"] for r in rows if r["shape_category"]]
    shape_counts = {}
    for s in shapes:
        shape_counts[s] = shape_counts.get(s, 0) + 1

    return {
        "sample_size": n, "reverted_count": reverted_n, "median_days_to_revert": median_days,
        "confidence": confidence, "summary": summary,
        "shape_distribution": shape_counts,
        "history": [{"event_date": str(r["event_date"]), "direction": r["direction"],
                     "move_pct": float(r["move_pct"]), "reverted": bool(r["reverted"]),
                     "days_to_revert": r["days_to_revert"], "shape_category": r["shape_category"],
                     "post_settlement_outcome": r["post_settlement_outcome"],
                     "reaction_accuracy": r["reaction_accuracy"]} for r in rows],
    }


def suggest_action(direction, bounce_or_fade_prob, confidence):
    """Decision aid, not a guarantee - locked spec: gains and drops are NOT mirrored.
    For drops the model predicts bounce probability (higher = more likely to recover).
    For gains the SAME classifier target (`reverted`) means "faded back down", so a
    high probability here is bearish, not bullish - opposite interpretation, same column."""
    if bounce_or_fade_prob is None:
        return "WATCH", None
    if direction == "drop":
        if bounce_or_fade_prob > 0.6:
            return "BUY (dip)", "entry near reaction low, target = prior close"
        return "WATCH", None
    else:  # gain
        if bounce_or_fade_prob > 0.6:
            return "SELL (take profit)", "sell into strength before it fades toward prior close"
        return "HOLD (sustained)", None


SORTABLE_COLUMNS = {
    "event_date": "e.event_date", "ticker": "e.ticker", "sector": "e.sector",
    "direction": "e.direction", "move_pct": "e.move_pct", "market_cap_at_event": "e.market_cap_at_event",
    "prior_close": "e.prior_close", "reaction_close": "e.reaction_close",
    "causal_event_type": "e.causal_event_type", "causal_confidence": "e.causal_confidence",
    "reverted": "e.reverted", "days_to_revert": "e.days_to_revert",
    "market_relative_return": "e.market_relative_return", "sector_relative_return": "e.sector_relative_return",
    "bounce_probability": "p.bounce_probability", "expected_days_to_revert": "p.expected_days_to_revert",
    "volume_ratio": "e.volume_ratio", "rsi_14": "e.rsi_14", "atr_14": "e.atr_14",
    "price_vs_sma50": "e.price_vs_sma50", "price_vs_sma200": "e.price_vs_sma200", "mom_3m": "e.mom_3m",
    "price": "lq.price", "day_change_pct": "lq.day_change_pct", "week_change_pct": "lq.week_change_pct",
    "month_change_pct": "lq.month_change_pct", "rel_volume": "lq.rel_volume",
}


@app.get("/api/events")
def list_events(direction: str = Query(None, pattern="^(drop|gain)$"),
                 ticker: str = Query(None), limit: int = 100, offset: int = 0,
                 sort_by: str = "event_date", sort_dir: str = Query("desc", pattern="^(asc|desc)$"),
                 user=Depends(get_user)):
    # sort_by is user-controlled (table header clicks) - resolve through an allowlist
    # rather than interpolating the query param directly, to avoid SQL injection via
    # column name (parameterized queries can't parameterize identifiers/ORDER BY columns).
    sort_col = SORTABLE_COLUMNS.get(sort_by, "e.event_date")
    conn = db()
    try:
        cur = conn.cursor()
        where, params = [], []
        if direction:
            where.append("e.direction=%s"); params.append(direction)
        if ticker:
            where.append("e.ticker=%s"); params.append(ticker.upper())
        where_sql = ("WHERE " + " AND ".join(where)) if where else ""
        cur.execute(f"""
            SELECT e.*, p.bounce_probability, p.expected_days_to_revert, p.model_version,
                   lq.price, lq.day_change_pct, lq.week_change_pct, lq.month_change_pct,
                   lq.rel_volume, lq.quote_date
            FROM events e
            LEFT JOIN predictions p ON p.event_id = e.id
            LEFT JOIN live_quotes lq ON lq.ticker = e.ticker
            {where_sql} ORDER BY {sort_col} {sort_dir.upper()}, e.id DESC LIMIT %s OFFSET %s
        """, params + [limit, offset])
        rows = cur.fetchall()

        results = []
        for r in rows:
            case = ticker_case_summary(cur, r["ticker"], exclude_event_id=r["id"])
            action, price_note = suggest_action(r["direction"], r["bounce_probability"], case["confidence"])
            results.append({
                "id": r["id"], "ticker": r["ticker"], "sector": r["sector"],
                "market_cap_at_event": float(r["market_cap_at_event"]) if r["market_cap_at_event"] else None,
                "causal_event_type": r["causal_event_type"], "causal_confidence": r["causal_confidence"],
                "event_date": str(r["event_date"]), "direction": r["direction"],
                "prior_close": float(r["prior_close"]) if r["prior_close"] else None,
                "reaction_close": float(r["reaction_close"]) if r["reaction_close"] else None,
                "move_pct": float(r["move_pct"]),
                "reverted": bool(r["reverted"]), "days_to_revert": r["days_to_revert"],
                "shape_category": r["shape_category"], "post_settlement_outcome": r["post_settlement_outcome"],
                "reaction_accuracy": r["reaction_accuracy"],
                "retracement_fraction": float(r["retracement_fraction"]) if r["retracement_fraction"] is not None else None,
                "market_relative_return": float(r["market_relative_return"]) if r["market_relative_return"] is not None else None,
                "sector_relative_return": float(r["sector_relative_return"]) if r["sector_relative_return"] is not None else None,
                "volume_ratio": float(r["volume_ratio"]) if r["volume_ratio"] is not None else None,
                "rsi_14": float(r["rsi_14"]) if r["rsi_14"] is not None else None,
                "atr_14": float(r["atr_14"]) if r["atr_14"] is not None else None,
                "price_vs_sma50": float(r["price_vs_sma50"]) if r["price_vs_sma50"] is not None else None,
                "price_vs_sma200": float(r["price_vs_sma200"]) if r["price_vs_sma200"] is not None else None,
                "mom_3m": float(r["mom_3m"]) if r["mom_3m"] is not None else None,
                "model_bounce_or_fade_probability": float(r["bounce_probability"]) if r["bounce_probability"] is not None else None,
                "model_expected_days": float(r["expected_days_to_revert"]) if r["expected_days_to_revert"] is not None else None,
                "price": float(r["price"]) if r["price"] is not None else None,
                "day_change_pct": float(r["day_change_pct"]) if r["day_change_pct"] is not None else None,
                "week_change_pct": float(r["week_change_pct"]) if r["week_change_pct"] is not None else None,
                "month_change_pct": float(r["month_change_pct"]) if r["month_change_pct"] is not None else None,
                "rel_volume": float(r["rel_volume"]) if r["rel_volume"] is not None else None,
                "quote_date": str(r["quote_date"]) if r["quote_date"] else None,
                "ticker_case_history": case,
                "suggested_action": action, "suggested_note": price_note,
            })
        return {"count": len(results), "events": results}
    finally:
        conn.close()


@app.get("/api/daily-watch")
def daily_watch(user=Depends(get_user)):
    """Deliverables #1 and #4 of the daily same-day pipeline (Specs/SPEC_daily_pipeline.md)
    - tomorrow's small watchlist, plus the confirmed event card once Phase 2's polling
    locks a checkpoint. Track A (bmo) reports before tomorrow's open; Track B (amc)
    reported today after close - both react at the same next-session open, so both live
    under one watch_date. Includes 'watching', 'settled', and 'dropped' rows for the
    latest watch_date so the UI can show forming/confirmed/no-event state (deliverable #3,
    the historical comparison view, is not built yet - a real next increment)."""
    conn = db()
    try:
        cur = conn.cursor()
        cur.execute("SELECT MAX(watch_date) d FROM daily_watch")
        latest = cur.fetchone()["d"]
        if not latest:
            return {"watch_date": None, "bmo": [], "amc": []}
        cur.execute("""
            SELECT dw.*, lq.price, lq.day_change_pct,
                   lt.price AS live_price, lt.pct_from_baseline AS live_pct, lt.ts_utc AS live_ts,
                   lt.session AS live_session
            FROM daily_watch dw
            LEFT JOIN live_quotes lq ON lq.ticker = dw.ticker
            LEFT JOIN live_reaction_ticks lt ON lt.id = (
                SELECT id FROM live_reaction_ticks WHERE watch_id = dw.id ORDER BY ts_utc DESC LIMIT 1
            )
            WHERE dw.watch_date=%s
            ORDER BY dw.track, dw.ticker
        """, (latest,))
        rows = cur.fetchall()
        out = {"watch_date": str(latest), "bmo": [], "amc": []}
        for r in rows:
            item = {
                "id": r["id"], "ticker": r["ticker"], "report_date": str(r["report_date"]),
                "status": r["status"],
                "prior_close": float(r["prior_close"]) if r["prior_close"] is not None else None,
                "sector": r["sector"],
                "market_cap_at_scan": float(r["market_cap_at_scan"]) if r["market_cap_at_scan"] is not None else None,
                "rsi_14": float(r["rsi_14"]) if r["rsi_14"] is not None else None,
                "atr_14": float(r["atr_14"]) if r["atr_14"] is not None else None,
                "price_vs_sma50": float(r["price_vs_sma50"]) if r["price_vs_sma50"] is not None else None,
                "price_vs_sma200": float(r["price_vs_sma200"]) if r["price_vs_sma200"] is not None else None,
                "mom_3m": float(r["mom_3m"]) if r["mom_3m"] is not None else None,
                "volume_ratio": float(r["volume_ratio"]) if r["volume_ratio"] is not None else None,
                "market_relative_return": float(r["market_relative_return"]) if r["market_relative_return"] is not None else None,
                "sector_relative_return": float(r["sector_relative_return"]) if r["sector_relative_return"] is not None else None,
                "price": float(r["price"]) if r["price"] is not None else None,
                "day_change_pct": float(r["day_change_pct"]) if r["day_change_pct"] is not None else None,
                "checkpoint_price": float(r["checkpoint_price"]) if r["checkpoint_price"] is not None else None,
                "checkpoint_pct": float(r["checkpoint_pct"]) if r["checkpoint_pct"] is not None else None,
                "checkpoint_locked_at": str(r["checkpoint_locked_at"]) if r["checkpoint_locked_at"] else None,
                "predicted_probability": float(r["predicted_probability"]) if r["predicted_probability"] is not None else None,
                "predicted_expected_days": float(r["predicted_expected_days"]) if r["predicted_expected_days"] is not None else None,
                "predicted_direction": r["predicted_direction"],
                "live_price": float(r["live_price"]) if r["live_price"] is not None else None,
                "live_pct": float(r["live_pct"]) if r["live_pct"] is not None else None,
                "live_ts": str(r["live_ts"]) if r["live_ts"] else None,
                "live_session": r["live_session"],
            }
            out["bmo" if r["track"] == "bmo" else "amc"].append(item)
        # Preview buckets (tonight's AMC, tomorrow's BMO/AMC) moved to their own
        # endpoint, /api/calendar-preview (2026-08-17) - full-column data, not just a
        # ticker list, polled on a slower interval since it does a fresh Finviz fetch.
        return out
    finally:
        conn.close()


def _preview_row(ticker, tinfo, lq):
    """Same shape as a real daily_watch row (renderDailyRow on the frontend is shared
    across both), but with baseline-specific fields left None - those aren't computed
    until the ticker becomes a real tracked candidate at its proper scan time. Cheap
    fields (sector, live price/day-change) come from already-cached tables, no new
    Polygon/Finviz per-ticker calls."""
    mcap = (tinfo["shares_outstanding"] * float(lq["price"])) if tinfo.get("shares_outstanding") and lq and lq.get("price") else None
    return {
        "id": None, "ticker": ticker, "status": "preview",
        "prior_close": None, "sector": tinfo.get("sector"),
        "market_cap_at_scan": mcap,
        "rsi_14": None, "atr_14": None, "price_vs_sma50": None, "price_vs_sma200": None,
        "mom_3m": None, "volume_ratio": None, "market_relative_return": None, "sector_relative_return": None,
        "price": float(lq["price"]) if lq and lq.get("price") is not None else None,
        "day_change_pct": float(lq["day_change_pct"]) if lq and lq.get("day_change_pct") is not None else None,
        "checkpoint_price": None, "checkpoint_pct": None, "checkpoint_locked_at": None,
        "predicted_probability": None, "predicted_expected_days": None, "predicted_direction": None,
        "live_price": None, "live_pct": None, "live_ts": None, "live_session": None,
    }


@app.get("/api/calendar-preview")
def calendar_preview(user=Depends(get_user)):
    """Deliverable extension (2026-08-17, Ashok's request) - full-column preview (not
    just a ticker list) for tonight's AMC reporters plus tomorrow's BMO and AMC
    reporters, ahead of when they'd otherwise first appear as real tracked candidates.
    Fetches Finviz fresh each call (same technique as daily_watch_scan.py) - kept as
    its own endpoint, polled on a slower interval than /api/daily-watch, since a bulk
    Finviz export is heavier than the plain DB reads the 30s poll does."""
    conn = db()
    try:
        cur = conn.cursor()
        cur.execute("SELECT ticker, sector, shares_outstanding FROM tickers")
        tracked = {r["ticker"]: r for r in cur.fetchall()}
        cur.execute("SELECT ticker, price, day_change_pct FROM live_quotes")
        quotes = {r["ticker"]: r for r in cur.fetchall()}

        today = datetime.date.today()
        tomorrow = next_trading_day(today)

        fdf = fetch_finviz_earnings_calendar()
        buckets = {"today_amc": [], "tomorrow_bmo": [], "tomorrow_amc": []}
        for _, row in fdf.iterrows():
            ticker = row.get("Ticker")
            if not ticker or ticker not in tracked:
                continue
            raw = row.get("Earnings Date")
            ed = earnings_date(raw)
            timing = earnings_timing(raw)
            if ed is None or timing is None:
                continue
            if ed == today and timing == 'a':
                buckets["today_amc"].append(ticker)
            elif ed == tomorrow and timing == 'b':
                buckets["tomorrow_bmo"].append(ticker)
            elif ed == tomorrow and timing == 'a':
                buckets["tomorrow_amc"].append(ticker)

        out = {"today": str(today), "tomorrow": str(tomorrow)}
        for key, tickers in buckets.items():
            out[key] = [_preview_row(t, tracked[t], quotes.get(t)) for t in sorted(tickers)]
        return out
    finally:
        conn.close()


@app.get("/api/daily-watch/{watch_id}/ticks")
def daily_watch_ticks(watch_id: int, user=Depends(get_user)):
    """Deliverable #2 - today's raw minute-by-minute reaction data for one watchlisted
    ticker. Full data points, not a chart (locked design decision, spec §6 Stage 2)."""
    conn = db()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id, ticker, track, status FROM daily_watch WHERE id=%s", (watch_id,))
        watch = cur.fetchone()
        if not watch:
            raise HTTPException(404, "unknown watch id")
        cur.execute("""
            SELECT ts_utc, price, volume, pct_from_baseline, session
            FROM live_reaction_ticks WHERE watch_id=%s ORDER BY ts_utc
        """, (watch_id,))
        ticks = cur.fetchall()
        return {
            "ticker": watch["ticker"], "track": watch["track"], "status": watch["status"],
            "ticks": [{"ts_utc": str(t["ts_utc"]), "price": float(t["price"]) if t["price"] is not None else None,
                       "volume": t["volume"],
                       "pct_from_baseline": float(t["pct_from_baseline"]) if t["pct_from_baseline"] is not None else None,
                       "session": t["session"]} for t in ticks],
        }
    finally:
        conn.close()


HISTORY_COMPARE_MAX_EVENTS = 5


def _time_et(ts_utc_str):
    return (datetime.datetime.fromisoformat(str(ts_utc_str)).replace(tzinfo=datetime.timezone.utc)
            .astimezone(ET).strftime("%H:%M"))


def _normalize_minute_bars(bars, baseline_price):
    out = []
    for b in bars:
        ts_utc = datetime.datetime.fromtimestamp(b["t"] / 1000, tz=datetime.timezone.utc)
        dt_et = ts_utc.astimezone(ET)
        price = b["c"]
        pct = (price - baseline_price) / baseline_price * 100 if baseline_price else None
        out.append({
            "ts_utc": ts_utc.strftime("%Y-%m-%d %H:%M:%S"), "time_et": dt_et.strftime("%H:%M"),
            "price": price, "pct_from_baseline": pct, "session": session_for(dt_et),
        })
    return out


@app.get("/api/daily-watch/{watch_id}/history-compare")
def daily_watch_history_compare(watch_id: int, user=Depends(get_user)):
    """Deliverable #3 - today's live reaction alongside this same ticker's own past
    qualifying events' minute-by-minute data, normalized the same way (spec §6 Stage 2).
    Computed on-demand, not pre-cached - a handful of Polygon calls per click, matching
    the spec's own on-demand design (still just this ticker's own case list, not a bulk
    backfill). No shape-category fallback yet - §7.3's taxonomy is Phase 3, not built -
    a ticker with no/thin history is reported plainly, not silently backfilled."""
    conn = db()
    try:
        cur = conn.cursor()
        cur.execute("SELECT id, ticker, prior_close, status FROM daily_watch WHERE id=%s", (watch_id,))
        watch = cur.fetchone()
        if not watch:
            raise HTTPException(404, "unknown watch id")

        cur.execute("""
            SELECT ts_utc, price, pct_from_baseline, session
            FROM live_reaction_ticks WHERE watch_id=%s ORDER BY ts_utc
        """, (watch_id,))
        today_ticks = cur.fetchall()

        cur.execute("""
            SELECT id, event_date, direction, move_pct, prior_close, reverted, days_to_revert
            FROM events WHERE ticker=%s ORDER BY event_date DESC LIMIT %s
        """, (watch["ticker"], HISTORY_COMPARE_MAX_EVENTS))
        past_events = cur.fetchall()

        history = []
        for ev in past_events:
            bars = fetch_minute_bars(watch["ticker"], str(ev["event_date"])) if ev["prior_close"] else []
            history.append({
                "event_date": str(ev["event_date"]), "direction": ev["direction"],
                "move_pct": float(ev["move_pct"]), "reverted": bool(ev["reverted"]),
                "days_to_revert": ev["days_to_revert"],
                "ticks": _normalize_minute_bars(bars, float(ev["prior_close"])) if ev["prior_close"] else [],
            })

        return {
            "ticker": watch["ticker"], "status": watch["status"],
            "today_ticks": [{
                "ts_utc": str(t["ts_utc"]), "time_et": _time_et(t["ts_utc"]),
                "price": float(t["price"]) if t["price"] is not None else None,
                "pct_from_baseline": float(t["pct_from_baseline"]) if t["pct_from_baseline"] is not None else None,
                "session": t["session"],
            } for t in today_ticks],
            "history": history,
            "history_note": None if history else "No prior qualifying events for this ticker yet - nothing to compare against.",
        }
    finally:
        conn.close()


@app.get("/api/events/{ticker}/history")
def ticker_history(ticker: str, user=Depends(get_user)):
    conn = db()
    try:
        cur = conn.cursor()
        return ticker_case_summary(cur, ticker.upper())
    finally:
        conn.close()


@app.get("/api/config")
def get_config(user=Depends(get_user)):
    conn = db()
    try:
        cur = conn.cursor()
        cur.execute("SELECT config_key, config_value, description FROM config")
        return {r["config_key"]: {"value": r["config_value"], "description": r["description"]}
                for r in cur.fetchall()}
    finally:
        conn.close()


class ConfigUpdate(BaseModel):
    value: str


@app.put("/api/config/{key}")
def update_config(key: str, body: ConfigUpdate, user=Depends(require_admin)):
    conn = db()
    try:
        cur = conn.cursor()
        # Check existence separately - MySQL's UPDATE rowcount reflects rows *changed*,
        # not rows *matched*, so setting a value to what it already was would otherwise
        # look identical to "key doesn't exist" (both report rowcount=0).
        cur.execute("SELECT 1 FROM config WHERE config_key=%s", (key,))
        if cur.fetchone() is None:
            raise HTTPException(404, "unknown config key")
        cur.execute("UPDATE config SET config_value=%s WHERE config_key=%s", (body.value, key))
        conn.commit()
        return {"ok": True, "key": key, "value": body.value}
    finally:
        conn.close()


@app.get("/")
def root():
    index = pathlib.Path(__file__).parent / "index.html"
    if index.exists():
        return FileResponse(index)
    return {"status": "HuntHarvest v2 API running, no frontend built yet"}
