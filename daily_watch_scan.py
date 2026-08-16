#!/usr/bin/env python3
"""
HuntHarvest v2 — daily evening watch scan.
Phase 1 of the daily same-day earnings pipeline (Specs/SPEC_daily_pipeline.md, Stage 1).
Run once daily after market close via huntharvest-dailywatch.timer.

Builds tomorrow's small watchlist: Track A (bmo) = tickers reporting tomorrow before
open; Track B (amc) = tickers that reported today after close. Both react at the SAME
next trading session's open (spec §4), so both get watch_date = next trading day.

Universe gate: ticker must already exist in HuntHarvest's own `tickers` table (built
from the >$500M-cap historical backfill universe, ingest_historical.py) - simpler and
more consistent with the rest of the app than re-parsing Finviz's own Market Cap column.

Baseline features: same feature set/methodology as ingest_historical.py's
compute_indicators() + market/sector-relative return, captured as of today's close, so
live watchlist rows are directly comparable to the historical `events` table. The AR/CAR
α/β regression from spec §5 is deferred to a later phase (§7.4 "not yet decided" -
estimation-window length isn't locked yet).
"""
import os
import io
import logging
import datetime
import requests
import pandas as pd
import pymysql

from ingest_historical import fetch_daily_bars, compute_indicators, SECTOR_ETF, MARKET_BENCHMARK

FINVIZ_AUTH_TOKEN = os.environ.get("FINVIZ_AUTH_TOKEN", "fbf202db-4edd-4cbd-aa80-2706fe394d4b")
# Same big export column set AGSTOX's agstox_exchange.py already uses successfully -
# includes Earnings Date among many others; reused verbatim rather than reverse-
# engineering Finviz's numeric column codes for a smaller subset.
FINVIZ_EXPORT_COLUMNS = ("0,1,2,79,3,4,5,129,6,7,8,9,10,11,12,13,73,74,75,14,130,131,147,148,149,15,16,77,17,"
                         "18,142,19,20,143,21,23,22,132,133,82,78,127,128,144,145,146,24,25,85,26,27,28,29,30,"
                         "31,84,32,33,34,35,36,37,38,39,40,41,90,91,92,93,94,95,96,97,98,99,42,43,44,45,47,46,"
                         "138,139,140,48,49,50,51,52,53,54,55,56,57,58,134,125,126,59,68,70,80,83,76,60,61,62,"
                         "63,64,67,89,69,81,86,87,88,65,66,71,72,141,135,136,137,150,103,100,101,104,102,106,"
                         "107,108,109,110,111,112,113,114,115,116,117,118,119,120,121,122,123,124,105")

DB_CONF = dict(host="localhost", user="huntharvest_app",
               password=os.getenv("DB_PASS", "aFLzUDDru0Spair4MduIQjeg"),
               database="huntharvest", cursorclass=pymysql.cursors.DictCursor)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("daily_watch")


def next_trading_day(d):
    """Next weekday after d - doesn't account for market holidays (same simplification
    already used elsewhere in this codebase; a holiday just means an empty/stale watch
    that day, not a crash)."""
    nd = d + datetime.timedelta(days=1)
    while nd.weekday() >= 5:  # Sat=5, Sun=6
        nd += datetime.timedelta(days=1)
    return nd


def earnings_timing(date_str):
    """'b' before open, 'a' after close, None if undeterminable. Same parsing logic as
    AGSTOX's _earnings_timing() (agstox_exchange.py:28982) - Finviz's own /b /a
    shorthand, handles both the newer 'M/D/YYYY H:MM:SS AM/PM' format (real time) and
    older 'Jul 22 BMO'/'Jul 22 AMC' literal-suffix format."""
    if not date_str:
        return None
    s = str(date_str).strip()
    try:
        parts = s.split()
        if len(parts) >= 3 and '/' in parts[0]:
            t = datetime.datetime.strptime(parts[1] + ' ' + parts[2], '%I:%M:%S %p').time()
            if t < datetime.time(9, 30):
                return 'b'
            if t >= datetime.time(16, 0):
                return 'a'
            return None
    except Exception:
        pass
    su = s.upper()
    if 'BMO' in su:
        return 'b'
    if 'AMC' in su:
        return 'a'
    return None


def earnings_date(date_str):
    """Parses just the date portion from either Finviz format ('M/D/YYYY ...' or
    'Jul 22 BMO')."""
    if not date_str:
        return None
    s = str(date_str).strip()
    try:
        parts = s.split()
        if parts and '/' in parts[0]:
            return datetime.datetime.strptime(parts[0], '%m/%d/%Y').date()
    except Exception:
        pass
    try:
        cleaned = ' '.join(s.split()[:2])
        d = datetime.datetime.strptime(cleaned, '%b %d').date().replace(year=datetime.date.today().year)
        # 'Jul 22' has no year - if that date reads as >30 days in the past, it's
        # actually next year's date (Finviz never lists earnings from last year).
        if d < datetime.date.today() - datetime.timedelta(days=30):
            d = d.replace(year=d.year + 1)
        return d
    except Exception:
        return None


def fetch_finviz_earnings_calendar():
    """One bulk Finviz export - same technique already proven live in AGSTOX's
    /api/earnings-calendar (agstox_exchange.py:29052), ported here rather than waiting
    on a Polygon plan upgrade for Benzinga/TMX (Specs/SPEC_daily_pipeline.md §3)."""
    url = ("https://elite.finviz.com/export.ashx?v=152&o=-marketcap"
           "&c=" + FINVIZ_EXPORT_COLUMNS + "&auth=" + FINVIZ_AUTH_TOKEN)
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    text = resp.text.strip()
    if not text or text.lower().startswith("<!doctype") or "<html" in text.lower():
        raise RuntimeError("Finviz auth failed - check FINVIZ_AUTH_TOKEN.")
    return pd.read_csv(io.StringIO(text), low_memory=False)


def _nz(v):
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return float(v)


def compute_baseline_features(ticker, sector, spy_df, sector_dfs):
    """Same feature set/methodology as ingest_historical.py's historical events -
    RSI-14, ATR-14, SMA50/200-relative, 3m momentum, 20d volume ratio, market- and
    sector-relative return - captured as of today's close."""
    end = datetime.date.today().isoformat()
    start = (datetime.date.today() - datetime.timedelta(days=320)).isoformat()
    df = fetch_daily_bars(ticker, start, end)
    if df is None or len(df) < 20:
        return None
    df = compute_indicators(df)
    r = df.iloc[-1]
    prev = df.iloc[-2] if len(df) > 1 else None

    vol_ratio = (r["volume"] / r["vol_avg_20"]) if r["vol_avg_20"] else None
    price_vs_sma50 = ((r["close"] - r["sma_50"]) / r["sma_50"] * 100) if r["sma_50"] else None
    price_vs_sma200 = ((r["close"] - r["sma_200"]) / r["sma_200"] * 100) if r["sma_200"] else None

    today_move = ((r["close"] - prev["close"]) / prev["close"] * 100) if prev is not None and prev["close"] else None
    market_ret = None
    if today_move is not None and spy_df is not None and len(spy_df) > 1:
        spy_move = (spy_df.iloc[-1]["close"] - spy_df.iloc[-2]["close"]) / spy_df.iloc[-2]["close"] * 100
        market_ret = today_move - spy_move
    etf = SECTOR_ETF.get(sector)
    sector_ret = None
    if today_move is not None and etf and etf in sector_dfs and len(sector_dfs[etf]) > 1:
        sdf = sector_dfs[etf]
        s_move = (sdf.iloc[-1]["close"] - sdf.iloc[-2]["close"]) / sdf.iloc[-2]["close"] * 100
        sector_ret = today_move - s_move

    return {
        "prior_close": float(r["close"]),
        "rsi_14": _nz(r["rsi_14"]), "atr_14": _nz(r["atr_14"]),
        "price_vs_sma50": _nz(price_vs_sma50), "price_vs_sma200": _nz(price_vs_sma200),
        "mom_3m": _nz(r["mom_3m"]), "volume_ratio": _nz(vol_ratio),
        "market_relative_return": _nz(market_ret), "sector_relative_return": _nz(sector_ret),
    }


def main():
    conn = pymysql.connect(**DB_CONF)
    cur = conn.cursor()

    cur.execute("SELECT ticker, sector, shares_outstanding FROM tickers")
    tracked = {r["ticker"]: r for r in cur.fetchall()}
    log.info(f"Tracked universe: {len(tracked)} tickers")

    today = datetime.date.today()
    watch_date = next_trading_day(today)
    log.info(f"Building watchlist for {watch_date} (today={today})")

    log.info("Fetching Finviz earnings calendar...")
    fdf = fetch_finviz_earnings_calendar()
    if "Earnings Date" not in fdf.columns or "Ticker" not in fdf.columns:
        raise RuntimeError("Finviz export missing expected columns - check FINVIZ_EXPORT_COLUMNS")

    candidates = []    # (ticker, track, report_date) - actively tracked starting tomorrow
    preview_amc = []   # (ticker, report_date) - reports tomorrow evening, informational only,
                        # real tracking starts with tomorrow evening's own scan run
    for _, row in fdf.iterrows():
        ticker = row.get("Ticker")
        if not ticker or ticker not in tracked:
            continue
        raw = row.get("Earnings Date")
        ed = earnings_date(raw)
        timing = earnings_timing(raw)
        if ed is None or timing is None:
            continue
        if ed == watch_date and timing == 'b':
            candidates.append((ticker, 'bmo', ed))
        elif ed == today and timing == 'a':
            candidates.append((ticker, 'amc', ed))
        elif ed == watch_date and timing == 'a':
            # Reports tomorrow evening - real reaction is the day AFTER (Tuesday if
            # watch_date is Monday), so this isn't an active tracking candidate yet
            # (that starts with tomorrow evening's own scan run). Informational-only
            # preview so it doesn't first appear out of nowhere the day it's reacting.
            preview_amc.append((ticker, ed))

    n_bmo = sum(1 for c in candidates if c[1] == 'bmo')
    n_amc = sum(1 for c in candidates if c[1] == 'amc')
    log.info(f"Candidates: {len(candidates)} (BMO={n_bmo}, AMC={n_amc}), "
             f"preview (tomorrow-evening AMC, not yet tracked): {len(preview_amc)}")

    for ticker, report_date in preview_amc:
        try:
            cur.execute("""
                INSERT INTO upcoming_earnings (ticker, report_date, report_time, confirmed, source)
                VALUES (%s,%s,'amc',%s,%s)
                ON DUPLICATE KEY UPDATE confirmed=VALUES(confirmed), source=VALUES(source)
            """, (ticker, report_date, True, 'finviz'))
        except Exception as e:
            log.error(f"{ticker} preview write FAILED: {e}")
    conn.commit()

    if not candidates:
        log.info("No qualifying reporters for the next session - nothing to write. Done.")
        conn.close()
        return

    log.info("Fetching SPY + sector ETF benchmarks...")
    b_end = today.isoformat()
    b_start = (today - datetime.timedelta(days=320)).isoformat()
    spy_df = fetch_daily_bars(MARKET_BENCHMARK, b_start, b_end)
    sector_dfs = {}
    for etf in set(SECTOR_ETF.values()):
        d = fetch_daily_bars(etf, b_start, b_end)
        if d is not None:
            sector_dfs[etf] = d

    written = 0
    for ticker, track, report_date in candidates:
        try:
            info = tracked[ticker]
            feats = compute_baseline_features(ticker, info["sector"], spy_df, sector_dfs)
            if feats is None:
                log.warning(f"{ticker}: no usable price history, skipping")
                continue
            mcap = (info["shares_outstanding"] * feats["prior_close"]) if info["shares_outstanding"] else None

            cur.execute("""
                INSERT INTO upcoming_earnings (ticker, report_date, report_time, confirmed, source)
                VALUES (%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE report_time=VALUES(report_time), confirmed=VALUES(confirmed),
                    source=VALUES(source)
            """, (ticker, report_date, track, True, 'finviz'))

            cur.execute("""
                INSERT INTO daily_watch
                (ticker, watch_date, track, report_date, baseline_date, prior_close,
                 rsi_14, atr_14, price_vs_sma50, price_vs_sma200, mom_3m, volume_ratio,
                 market_relative_return, sector_relative_return, market_cap_at_scan, sector, status)
                VALUES (%s,%s,%s,%s,%s,%s, %s,%s,%s,%s,%s,%s, %s,%s,%s,%s,'watching')
                ON DUPLICATE KEY UPDATE
                    track=VALUES(track), report_date=VALUES(report_date), baseline_date=VALUES(baseline_date),
                    prior_close=VALUES(prior_close), rsi_14=VALUES(rsi_14), atr_14=VALUES(atr_14),
                    price_vs_sma50=VALUES(price_vs_sma50), price_vs_sma200=VALUES(price_vs_sma200),
                    mom_3m=VALUES(mom_3m), volume_ratio=VALUES(volume_ratio),
                    market_relative_return=VALUES(market_relative_return),
                    sector_relative_return=VALUES(sector_relative_return),
                    market_cap_at_scan=VALUES(market_cap_at_scan), sector=VALUES(sector), status='watching'
            """, (ticker, watch_date, track, report_date, today, feats["prior_close"],
                  feats["rsi_14"], feats["atr_14"], feats["price_vs_sma50"], feats["price_vs_sma200"],
                  feats["mom_3m"], feats["volume_ratio"], feats["market_relative_return"],
                  feats["sector_relative_return"], mcap, info["sector"]))
            conn.commit()
            written += 1
            log.info(f"{ticker} ({track.upper()}): baseline captured, close=${feats['prior_close']:.2f}")
        except Exception as e:
            conn.rollback()
            log.error(f"{ticker} FAILED: {e}")

    log.info(f"DONE. {written}/{len(candidates)} tickers written to watchlist for {watch_date}")
    conn.close()


if __name__ == "__main__":
    main()
