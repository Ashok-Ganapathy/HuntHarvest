# HuntHarvest — Project State

## What this is (v2)
An earnings-reaction monitoring and decision-support tool. For every US stock >$500M market cap, track quarterly-earnings-driven price moves of ≥10% (either direction), learn each stock's own historical pattern of what happens after such a move, and surface that pattern — not a deterministic prediction — ahead of the stock's next earnings report so the user can decide whether to buy, sell, or hold. Independent from AGSTOX (separate droplet, separate codebase, separate user base) — earnings-pattern trading is a different mental model from AGSTOX's general screening and would confuse AGSTOX's casual users if merged.

---

## Locked v2 design spec (2026-08-15, via extended design conversation)

### Universe & scope
- All US active stocks, real market cap > $500M (admin-configurable threshold, stored in `config` table — not hardcoded)
- **Bidirectional**: track both drops AND gains ≥10% (threshold also admin-configurable), treated with different downstream logic (see Output framing below)
- Historical window: **2022-01-01 to now** — COVID-era data (pre-2022) deliberately excluded as unrepresentative
- Trigger: tied specifically to **earnings report dates** (a "causal event"), not just any ≥10% move regardless of cause — see Data sources below for the real gap here

### Causal event taxonomy
Stored as an open/extensible category field. V1 (this build) only populates "earnings" — the field exists so other event types can be added later without restructuring:
`earnings` | `guidance` | `ma_corporate_action` | `regulatory_legal` | `analyst_action` | `macro_index_driven` | `unknown`

### Baseline & data granularity
- Reversion reference point = **prior trading day's close** (not further back)
- Premarket price on the report day itself is captured as an additional reference point
- **Historical backfill = daily bars only.** Intraday 5-/10-minute bars are **live-only** — pulled only for tickers reporting *today* that have already crossed the threshold, never backfilled historically (this is what keeps the historical pull affordable)
- **Day-before prep**: for a stock reporting Tuesday, capture baseline conditions (price, indicators) the prior trading day (Monday) — requires a forward-looking earnings calendar, see gap below

### Outcome tracking
- Full **daily price path** from event day to reversion (or ongoing) is stored — not just a single "days to revert" number. The day-count is *derived* from the path, not captured instead of it.
- No fixed cutoff on how long to track — treated as a **survival-analysis problem**: some events are right-censored (haven't reverted yet, or may never within any observable window). Model and storage both need to represent "not yet reverted" as a valid state, not force a number.

### Feature set (captured per event, point-in-time — locked 2026-08-15)
- RSI (14-day)
- ATR (14-day)
- Price vs. 50-day SMA, price vs. 200-day SMA
- Volume ratio (event-day volume ÷ 20-day trailing average)
- 3-month momentum
- Market-relative return (stock's move − SPY's same-day move) — isolates idiosyncratic vs. macro-driven moves
- Sector-relative return (stock's move − its sector ETF's same-day move) — port comparison logic from AGSTOX's Bulletin Board rather than rebuild from scratch
- Market cap **at the time of the event**, not today's market cap applied retroactively (this was v1's core bug — see Archive below). Approximated as `shares_outstanding × historical_close` since Polygon doesn't expose a historical market-cap time series directly.
- Days since this ticker's last qualifying event (recency)
- Not built for v1, flagged for later: short interest %, analyst rating changes

### Modeling approach — hybrid, not a single model
Per-ticker sample size is too small to train an individual model per stock (roughly 5–15 qualifying events per ticker since 2022 — not enough data). Instead:
1. **One pooled model** (gradient-boosted trees) trained across *all* tickers' events, using the feature set above — this is what has enough data to learn a real pattern. Predicts bounce probability + expected magnitude/timing.
2. **A per-ticker historical case list** alongside it — not a trained model, just "here are this ticker's own past N qualifying events and what happened each time." Satisfies the "pattern for this specific stock" requirement without pretending a handful of data points is enough to train anything. ("Compare history, but don't blindly trust history" was the user's own framing.)
- Training cadence: periodic (e.g. monthly, or manually triggered) — not continuous. This is a personal tool, not something needing real MLOps.

### Output format — per stock, on the main screen
- Ticker / sector / market cap / causal event type + date
- The move: prior close → premarket → reaction price, drop or gain %
- This ticker's own case-list history (e.g. "last 6 qualifying events: 4 bounced within a median 5 days, 2 never reverted within 30 days")
- Pooled model's read: bounce probability %, expected magnitude/timing
- A confidence flag when the ticker's own sample is thin (1-2 past events reads very differently than 10+)
- Suggested action + a suggested price level
- Same-day sector/market context (to distinguish idiosyncratic moves from "everything moved that day")

### Gains vs. drops — different treatment, not mirrored
- **Drops**: "does it bounce" framing — a potential buy-the-dip setup, suggested entry near/below the reaction low, target = reversion toward prior close
- **Gains**: "does it hold or fade" framing — two genuinely different outcomes need two different suggestions. Sustained gain → hold/add signal for existing holders. Faded gain → sell-into-strength/take-profit signal. Gains do **not** get a symmetric "buy the spike" recommendation the way drops get "buy the dip."

### This IS the application, not a bolt-on alert
The upcoming-earnings-with-historical-pattern view is the *primary* screen, not a notification layer sitting on top of something else.

### Deployment & auth
- Independent app, own droplet (142.93.196.178, replaces the original 165.227.88.24 which died during MySQL install on 1GB RAM — see Archive)
- Multi-user (keeps a login pattern similar to v1, ashok + others), but credentials in `.env`/SECRETS.md, never hardcoded/committed like v1 was
- **DB: MySQL, not SQLite** — user already operates MySQL for AGSTOX; multi-user + concurrent daily-write-while-serving-reads pattern favors a real DB server over SQLite's single-writer model
- Stack: FastAPI + uvicorn, scikit-learn, plain Python + `requests` for ingestion, static HTML/JS frontend (no build tooling — matches the scale of a personal tool)

---

## ⚠️ Known gap: earnings-calendar data source (verified 2026-08-15)
The spec depends on knowing *in advance* who's reporting earnings and when (for day-before prep). Checked three Polygon/Massive candidates:
- `/benzinga/v1/earnings` — documented, but current $79/mo Stocks Developer plan returns "You are not entitled to this data, upgrade your plan"
- `/tmx/v1/corporate-events` (Wall Street Horizon) — same result, also gated
- `/vX/reference/financials` (Polygon's own SEC-filings data) — **this one works**, returns real `filing_date`/`acceptance_datetime` for 10-Q/10-K filings. Useful as an approximate *historical* cross-check (which price moves likely correspond to real earnings events), but **not forward-looking** — companies file the formal SEC document days-to-weeks after the actual earnings press release/reaction day, so this cannot tell you who's reporting tomorrow.

**Net**: historical backfill is fully buildable now. The live "day-before prep" / forward-looking calendar piece needs either a Polygon plan upgrade (to unlock Benzinga or TMX) or a separate earnings-calendar data source — not yet resolved, tracked in TASKS.md.

---

## MySQL setup (confirmed working, 2026-08-15)
- Droplet: 142.93.196.178 (2GB RAM, replaces the dead original)
- Database: `huntharvest`, dedicated app user `huntharvest_app` (not root)
- Python venv at `/var/www/huntorharvest/venv`: fastapi, uvicorn, scikit-learn, pandas, numpy, requests, pymysql, python-dotenv, cryptography
- Full credentials in SECRETS.md (gitignored)

---

## Archive — v1 (superseded, kept for reference)

### What v1 was
A stock screener + ML tool ("HuntorHarvest.com Earnings ML Pro") that found US stocks with large single-day drops and predicted bounce probability / expected P&L, on SQLite. It existed as a working droplet-deployed site before this project was ever opened as a session — discovered, not built from scratch, in Session 1.

### Why it was torn down rather than fixed
Audited all ~10 script variants on the v1 droplet. Confirmed real, structural bugs, not just cosmetic ones:
1. **Market cap stored as a single static (current) value per ticker**, applied retroactively to every historical row regardless of date — e.g. SEZL's `market_cap` was identical (`4224193756.16`) across all 29 of its rows spanning 2023–2026. This meant the core ">$500M at time of event" qualifying filter had never actually been enforced correctly.
2. **The spec's own flagship proof case was missing.** The developer handoff spec required `SEZL 2025-08-08 -34.69%` to be found in the DB — it wasn't there. Root cause traced to `rebuild_clean.py`'s requirement of 10 forward trading days of data to compute `pnl_10d`, which silently right-censored any real event within ~2 weeks of the pull's end date.
3. **Mismatched ML feature counts across scripts feeding the same model files** — `enrich_final.py`/`fix_bounce.py` scored with 3 features (`drop_pct, mom_3m, vol_ratio`), while the live `app.py` scored with 5 (`drop, mom_3m, vol_ratio, log10(mcap), cluster)`. A shape mismatch would throw inside a caught `except: pass`, meaning live predictions for unlabeled rows may have been silently failing entirely.
4. **`ingest_fresh.py`** — the script `deploy.sh` actually calls on every deploy — generates entirely random fake data (`TKR{i%500}` tickers, `random.uniform()` for every field), exactly the "fake ticker" failure mode the original spec explicitly warned against. Lucky that it apparently hadn't been re-run recently, since doing so would silently wipe real data with garbage.
5. Config/deploy drift: local repo's checked-in `huntorharvest.service`/`.conf` (port 8000, `/root/huntorharvest`) didn't match what was actually live on the droplet (port 8001, `/var/www/huntorharvest`) — accumulated drift from iterative patching without cleanup.

Given these were structural (not superficial) bugs, and the user's own read was that the app had been "really inconsistent" to work with previously, the decision was to archive and rebuild rather than patch.

### What was preserved
- Full git history: bundled (`git bundle create --all`) and saved to `~/HuntHarvest_v1_archive/huntharvest_v1_legacy.bundle`, tagged `v1-legacy` in GitHub
- The real `earnings.db` (6,083 rows), `bounce_model.pkl`, `pnl_model.pkl` — all copied to `~/HuntHarvest_v1_archive/`
- All 13 audited script variants — copied to `~/HuntHarvest_v1_archive/old_scripts/`
- The original droplet (165.227.88.24) itself became unresponsive during the v2 MySQL install (1GB RAM, likely OOM) and didn't recover even after a DO Power Cycle attempt — replaced with a new 2GB droplet rather than repaired. Old droplet not yet destroyed in the DO dashboard as of this writing, but nothing on it is unique/unbacked-up.

### v1's original locked scope (superseded)
- Universe: all US active stocks, market cap > $500M
- Drop-only (not bidirectional): `(low_today - close_yesterday)/close_yesterday <= -10%`
- Lookback: originally spec'd as 90 days rolling, but drifted in practice to ~3 years (2023-10-30 to 2026-08-14) through accumulated script changes
- DB: SQLite, `all_drops` table (18 columns)

---

## Session history

### Session 1 (2026-08-15) — Discovery, audit, teardown, and v2 spec lock
- Discovered `~/HuntHarvest` already existed as a working droplet-deployed v1 site, not actually a new project
- Read the v1 developer handoff spec, audited all script variants, found the structural bugs listed above
- Full extended design conversation with Ashok to define v2 from scratch (see locked spec above)
- Archived v1 completely (git bundle + DB + models + scripts), tore down the old droplet's service
- Original droplet died during v2 infra setup (1GB RAM, MySQL install); replaced with a new 2GB droplet (142.93.196.178)
- Set up MySQL (`huntharvest` DB + dedicated app user) and the full Python stack on the new droplet
- Verified/fixed a Cloudflare API token for future DNS cutover (huntorharvest.com managed via Cloudflare) — cutover deliberately held until v2 has something live to point at
- Verified the earnings-calendar data gap (Benzinga/TMX endpoints exist but aren't included in the current Polygon plan) — historical backfill unaffected, live day-before-prep blocked until resolved
- User gave explicit build go-ahead — proceeded to schema + ingestion + training + app code

### Session 1 continued (2026-08-16) — v2 build
- Applied `schema.sql`: `events`, `price_path`, `predictions`, `config`, `users`, `tickers`, `upcoming_earnings` (7 tables) to the `huntharvest` MySQL DB
- Seeded `config` (admin-maintainable drop/gain thresholds, market cap floor) and `users` (ashok=admin, train=user, bcrypt-hashed)
- Wrote `ingest_historical.py`. Smoke-tested on SEZL before committing to the full run — caught and fixed three real bugs:
  1. Pandas `NaN` (from indicators needing more lookback than early rows have) can't insert into MySQL directly — needed a sanitizer
  2. Polygon exposes only SIC code/description, no GICS sector field — built a SIC-code-range → sector mapping instead of matching description text (which would have silently broken sector-relative-return for nearly every ticker)
  3. **Point-in-time market cap qualification**: universe selection uses *current* market cap to decide which tickers to pull, but a ticker's own historical events must independently clear the floor at the time they happened — a stock like SEZL that grew ~40x since 2023 would otherwise have its genuinely-sub-$500M-at-the-time 2023 events counted as if they'd always qualified. This is the exact same bug class (retroactive vs. point-in-time) that broke v1's market cap field. Fixed with a per-event filter.
  - After fixes, SEZL produced a `2025-08-08 -34.32%, $3.08B` event — closely matching v1 spec's exact flagship proof case (`SEZL 2025-08-08 -34.69%, $4.2B`) that was mysteriously absent from v1's database. Strong validation that the rebuild fixed the actual bug.
- Wrote `train_models.py` — separate classifier (bounce/fade probability) + regressor (days-to-revert, trained only on non-censored/reverted rows) per direction, not mirrored between drops and gains per the locked spec. Compile- and connectivity-tested; not yet run against real data.
- Wrote `app.py` — FastAPI, HTTP Basic auth against bcrypt hashes (not v1's plaintext dict), role-based admin config editing, `/api/events` with per-ticker case history + suggested action baked in, `/api/config`. Live-tested end to end: auth accept/reject, admin-vs-user role enforcement, config read/write. Found and fixed one real bug: MySQL's `UPDATE` rowcount reflects rows *changed* not rows *matched*, so a no-op config update (setting a value to what it already was) looked identical to "unknown key" — fixed with an explicit existence check before updating.
- Wrote `qc_check.py` — structural QC checklist (proof case, threshold violations, static-market-cap detection, orphaned price paths, duplicate events, outlier move sizes) — an evolution of v1's spec's QC checklist, which v1 itself failed.
- Prepared `huntharvest.service` (systemd, `EnvironmentFile=.env` rather than secrets baked into the unit file) and `huntorharvest.com.conf` (nginx) — deployed to the droplet but not yet started/enabled, waiting on trained models and DNS respectively.
- Launched the full historical backfill (2022-01-01 to today) in the background on the droplet — 13,110 total active US tickers, 3,277 qualifying at >$500M current market cap, processing one by one.

### Session 1 continued (2026-08-16) — backfill redo, training, QC, frontend build, live quotes
- **First backfill run completed** (40,528 events) and was trained — but a QC spot-check on outlier moves caught a real bug: Meta Platforms' ticker change (FB→META, 2022-06-09) meant bars fetched under "META" before that date belonged to a different, unrelated company (produced a fake +1394.7% "event"). Root cause: Polygon reuses ticker symbols across unrelated companies over time. Fixed via `ticker_valid_from()` using Polygon's `/vX/reference/tickers/{ticker}/events` endpoint, which clips the fetch start date to when the current entity actually held the symbol.
- Wiped all data (`events`/`price_path`/`predictions`) and redid the backfill. Learned mid-cleanup that `DELETE` on `price_path` (~5M rows) is far too slow via row-by-row InnoDB deletion on this droplet — switched to `TRUNCATE` (with `FOREIGN_KEY_CHECKS=0` to get around MySQL's parent-table restriction), had to `KILL` a stuck `DELETE` query first.
- **Second backfill run crashed mid-run** (`Lost connection to MySQL server during query`) — root cause: Ubuntu's `unattended-upgrades` restarted mysqld as a side effect of patching a linked system library, unrelated to the code. Fixed by adding `ensure_connected()` (ping-and-reconnect) before DB operations in long-running loops, rather than trying to fight the OS's automatic security patching.
- **Third backfill run completed successfully**: 37,773 events, 2,696 distinct tickers, 2022-01-01 to 2026-08-14.
- **Training**: an unweighted first pass showed misleadingly good 81%/75% accuracy that was actually just the model always guessing the majority "reverted" class (5-8% recall on the minority class). Fixed with `sample_weight` class-balancing (`GradientBoostingClassifier` has no `class_weight` param unlike RandomForest) — honest numbers are 63-64% accuracy with 53-67% recall on both classes. Days-to-revert regression stayed weak (R² 0.05-0.10), consistent with the design's intent to pair the pooled model with per-ticker case history rather than lean on it alone.
- **QC passed** all 8 structural checks, including the SEZL `2025-08-08 -34.32%, $3.08B` proof case that was the specific bug missing from v1. 10 outlier moves >75% flagged for manual review, not failures — spot-checked ALOY (a very recently reassigned/listed ticker, genuinely volatile) and BMNR (a real crypto-treasury mania event matching BitMine Immersion's actual 2025 market history) against raw price data, both confirmed real.
- **Frontend built** (`index.html`) iteratively with live user feedback: sidebar navigation (Earnings Watch / System tabs, admin config + account moved under System), a sortable 28-column table (server-side sort via an allowlisted column map), expandable per-ticker case history rows. Bugs found and fixed live: a JS crash when a ticker had zero prior events (missing `history` key in one API code path), a summary-text bug that mixed drops and gains under one ambiguous verb, a full-width layout fix (content was capped at 1200px regardless of viewport), and several rounds of column reordering to put decision-relevant fields (Event/Source/Confidence/Recom) near Ticker instead of buried mid-table.
- **Live quotes** built (`update_live_quotes.py`, `live_quotes` table) — current price, day/week/month % change, relative volume, computed from a ~45-day bars fetch per ticker. Populated once for all 2,696+ tickers; not yet scheduled to refresh periodically.
- **Not yet done**: starting the real systemd service (only ad-hoc test instances have run, via SSH tunnel for live browser preview during development), Cloudflare DNS cutover, fresh SSL cert, scheduling live-quote refresh and periodic retraining.

### Session 1 continued (2026-08-16) — go-live
Real `huntharvest` systemd service started, Cloudflare DNS cut over to 142.93.196.178, Let's
Encrypt SSL issued (ECDSA, expires 2026-11-14, auto-renews), `huntharvest-quotes.timer`
(30 min) and `huntharvest-train.timer` (monthly) enabled. End-to-end verified live over
`https://huntorharvest.com`. App is live and self-sustaining as of this point.

### Session 1 continued (2026-08-16) — research phase, daily pipeline design
With the app live, dug into the gap between it (backward-looking lookup tool) and Ashok's
actual desired workflow (small daily watchlist, live reaction tracking, compare to pattern).
Extensive real-material research pass, not just intro-level: post-earnings announcement
drift (PEAD) literature, event-study methodology (abnormal returns/CAR, market-model
regression), purged cross-validation (López de Prado - found `train_models.py`'s random
`train_test_split` as a real, concrete gap), SUE, factor-zoo/significance discipline
(Harvey-Liu-Zhu), and - the most important correction - literature specific to *extreme*
(≥10%) moves showing attention-driven overreaction + *partial* reversal, not the classic
moderate-surprise underreaction-drift the original framing leaned on. Also cross-checked
AGSTOX's structurally similar Fingerprint Bounce/Chart Pattern Detector features against
the same findings (real: no abnormal-return adjustment either; AGSTOX is actually *ahead*
on purged-style time-ordered holdout validation - noted in AGSTOX's own TASKS.md as a
backlog item, not built). Full writeup: `Specs/RESEARCH_institutional_methodology.md`,
`Specs/PEAD_Research_Summary.pptx`.

Locked design in `Specs/SPEC_daily_pipeline.md`: two tracks (BMO baseline=prior close,
checkpoint=premarket-clears-threshold; AMC baseline=prior close, checkpoint=next-day open
specifically, not the noisier after-hours/premarket prints - both research- and
microstructure-literature-backed), an AR/CAR event-study foundation (252-trading-day
estimation window ending 30 days before the event, min 120 days, running *alongside* the
raw-% threshold rather than replacing it), and a post-event analysis layer (post-settlement
durability, reaction-accuracy scoring, and a shape taxonomy including a new PARTIAL-REVERSAL
bucket - locked as the *expected modal* outcome for this population, not an edge case,
per the extreme-move research finding above).

### Session 1 continued (2026-08-16) — daily pipeline build, Phases 1-3, all live-verified
Built and deployed in three phases, each with real go-ahead and real verification against
live or historical data, not just clean exit codes:

- **Phase 1** (`daily_watch_scan.py`, new `daily_watch` table, `/api/daily-watch`, "Daily
  Watch" tab - now the default view): evening scan builds tomorrow's small watchlist,
  reusing AGSTOX's Finviz earnings-calendar-parsing technique rather than waiting on a
  Polygon plan upgrade. Live-verified: real HTHT row (reports 2026-08-17 BMO) flowing
  DB→API→frontend.
- **Phase 2** (`live_reaction_poll.py` every 5 min, `settle_watch.py`... actually settle
  moved to a later step below, checkpoint/prediction columns on `daily_watch`,
  `live_reaction_ticks`, `/api/daily-watch/{id}/ticks` and `/history-compare`): live
  minute-by-minute reaction capture, checkpoint-lock detection per track, confirmed
  event card (existing trained model run on Phase 1's baseline features), and the
  historical-comparison view (this ticker's own past events' minute data, on-demand).
  Verified against a real historical event (WTI, 2026-08-10, +11.37%) via a temporary
  test row since Phase 2 was built on a weekend - real Polygon bars, correct checkpoint
  lock, real model prediction, cleaned up after. **Real bug caught+fixed**: the AMC
  table's column headers weren't updated when Status/Confirmed Read columns were added -
  would have silently misaligned columns once a real AMC row appeared.
- **Phase 3** (`event_analysis.py` shared math, `settle_watch.py` every 15 min,
  `backfill_event_analysis.py`, `track_live_events.py` daily, edge-strength tracking added
  to `train_models.py`): AR/CAR market-model regression, retracement-fraction-based shape
  taxonomy, post-settlement durability, reaction-accuracy scoring. Ran the historical
  backfill for real across all 37,786 existing events (zero API cost, pure local
  computation). **Real bug caught and fixed during verification, not after**: 28,542
  reverted events initially showed `post_settlement_outcome='held'` - checking the actual
  numbers (reverted events averaged only 66.6 observed days vs. 332 for non-reverted)
  revealed this was a false default, not a genuine result: `ingest_historical.py`'s
  original backfill stops recording `price_path` the day a reversion is first detected,
  so there was no data past that point to check durability against. Fixed
  `classify_post_settlement()` to return `None` (honestly unknown) instead of defaulting
  to "held," reset, reran. Also found a real, honest limitation left unfixed at the time:
  `dead_cat` shape classification was structurally unreachable for historical reverted
  events for the same reason - flagged as a scoped follow-up (extending `price_path` needs
  real API calls), not silently hidden.

All 7 pipeline timers confirmed active on the droplet: dailywatch, livepoll, settle,
tracklive, analysisbackfill (added later, see below), quotes, train.

### Session 1 continued (2026-08-16) — deferred backfills, edge-strength, git/droplet cleanup
- **Combined backfill** (`backfill_deep_analysis.py`, explicit go-ahead, ~20 min real
  runtime): one Polygon call per ticker (not per event) covering both deferred items -
  α/β computed for 2,587/3,277 tickers, `price_path` extended 40 real days past reversion
  for 30,009 reverted events. Reran the analysis backfill afterward and **verified the
  real effect rather than trusting a matching summary log line** (it looked suspiciously
  identical to the pre-extension run's numbers) - confirmed via the underlying data that
  `dead_cat` went from 0 (structurally unreachable) to 1,911 genuine classifications, and
  the shape distribution shifted substantially now that `retracement_fraction` reflects
  real forward data instead of the crossing-moment snapshot.
- **Gap caught in Phase 3's own scheduling**: `backfill_event_analysis.py` had only ever
  been run manually - nothing was scheduled to re-check events as they crossed the 90-day
  settled window. Added `huntharvest-analysisbackfill.timer` (weekly, zero API cost).
- **Edge-strength bug caught testing it for the first time**: `check_edge_strength()`
  (added to `train_models.py` earlier) assumed dict-cursor access but this file's
  `conn.cursor()` returns plain tuples - crashed on first real run. Fixed, reran clean.
  **First real result**: recent win rate 73.59% (n=5,423) vs. prior 73.07% (n=4,326) →
  trend=holding, logged to `edge_strength_log`.
- **Git cleanup**: investigating "git init v2 code" turned up a real, unrelated finding -
  `~/HuntHarvest` had no `.git` of its own; a stray one was sitting at
  `/Users/ashokganapathy/.git` (the whole home folder), remote already pointed at the
  HuntHarvest GitHub repo, no commits yet (no damage done, but `git add -A` from inside
  HuntHarvest would have staged the entire home directory). Flagged directly rather than
  fixed silently. Also found the remote's `main` wasn't empty - it held v1's actual single
  commit, and the documented `v1-legacy` tag had never actually been pushed. Fixed with
  explicit go-ahead at each step: removed the stray `.git`, `git init` properly scoped to
  `~/HuntHarvest`, tagged the real v1 commit as `v1-legacy` (pushed), committed all 39 v2
  files, force-pushed (`--force-with-lease`) as the new `main`. Verified on the remote.
- **Droplet cleanup**: Ashok renamed the live droplet ("HuntHarvest" in DO, was the
  default hostname) and destroyed the dead old one (165.227.88.24) himself the same
  morning - confirmed via a fresh write-scoped DO token (added to SECRETS.md) that both
  were already done before attempting either.

**End of session state**: full daily pipeline (Phases 1-3) live and self-sustaining on its
own timers. **Monday 2026-08-17 is the first real end-to-end trading-day test** - HTHT is
genuinely on the watchlist reporting BMO that morning. No open items from this session;
remaining backlog is pre-existing and lower-priority (see TASKS.md).
