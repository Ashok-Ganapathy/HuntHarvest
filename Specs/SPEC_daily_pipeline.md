# SPEC — Daily Same-Day Earnings Pipeline

**Status**: Design locked 2026-08-16, discussed and refined live with Ashok across multiple rounds. **Not built.** Needs an explicit build go-ahead before any code is written, per HuntHarvest's standing rule (a locked spec is not itself authorization to build).

**Context**: HuntHarvest v2 is live at huntorharvest.com, but it is currently a **backward-looking research tool only** — for a ticker you already have in mind, it shows that ticker's own history of ≥10% earnings-driven moves and the pooled model's bounce/fade read. It does not detect or flag anything on its own, day to day. This spec covers the missing piece: a real daily pipeline that surfaces *tomorrow's small watchlist tonight*, then tracks the actual reaction as it happens.

---

## 1. Problem statement

Three gaps, previously tracked as separate unrelated TODOs, turned out on inspection to be one connected pipeline:

1. **No forward-looking earnings calendar** — nothing tells the app who reports tomorrow. `upcoming_earnings` exists in the schema, empty, unfed.
2. **No daily incremental ingest** — `ingest_historical.py` is a slow (~1.5hr) full backfill, not a "check what happened today" job.
3. **No live/intraday reaction tracking** — the original locked v2 spec called for intraday bars pulled live for tickers crossing threshold *today*; this was never written, only described.

Net effect: the app is a lookup tool, not a daily-use dashboard. Ashok's own framing of the desired workflow: *"every day in the afternoon or evening, look at the stocks reporting tomorrow... capture all the data... that'll be the starting point... there'll be only a few items every day, which is good... then for those stocks, watch the moment the market starts reacting... compare with the pattern."*

## 2. Deliverables

**#1 is the starting point — everything else is downstream of it, not parallel to it. Build order matters.**

1. **The daily reporting-stocks dashboard (starting point / first deliverable, on its own)** — a live view of two short lists: tickers reporting tomorrow before open (Track A), and tickers that reported today after close (Track B). Populated fresh every evening. A handful of names, not the full 28-column universe table. Nothing else below can function until this exists — it defines which tickers get watched at all.
2. **Live reaction data per watchlisted ticker** — once the market/premarket starts moving, raw minute-by-minute data (no chart — full data points, today's reaction), clearly labeled "forming / not yet confirmed" until the track-appropriate checkpoint locks.
3. **Historical comparison, data-to-data** — today's minute-by-minute reaction data set alongside this same ticker's own past qualifying events' minute-by-minute data, normalized the same way, computed on-demand the moment the ticker lands on today's watchlist.
4. **Confirmed event card** once the checkpoint locks (premarket clears threshold for Track A, next-day open for Track B) — bounce/fade probability + expected days from the existing trained model, triggered automatically instead of only on manual lookup.
5. **Enriched per-ticker case history** — each past event gains three derived tags instead of a bare revert/no-revert flag: post-settlement durability outcome, reaction-accuracy read, shape category (see §7).
6. **Backend jobs**: evening scan (builds the watchlist), reaction-tracking during market hours (polls minute bars for just that day's names), settle (folds the confirmed event into existing `events`/`price_path` tables).
7. **Edge-strength tracking** (added 2026-08-16) — each monthly retrain compares recent win rate/CAR against prior periods and records whether the underlying edge is holding, weakening, or improving, instead of silently assuming it's constant (see §6, Stage 3).

**Explicitly not part of this deliverable set**: analyst-estimate/whisper-number features (phase 2, needs a different data source — see §8), bulk historical backfill of the new scoring fields (§7.5), any change to the existing full-history table, monthly retrain cadence, or `ingest_historical.py`.

## 3. Data feasibility (checked, not assumed)

- **Earnings calendar**: Polygon's own forward-looking calendar (Benzinga/TMX) is gated behind a plan upgrade — confirmed blocked 2026-08-15 (see PROJECT_STATE.md "Known gap"). **Real unblock**: AGSTOX already solves this exact problem, live, for free — `/api/earnings-calendar` in `agstox_exchange.py` parses Finviz's raw earnings-date+time string, splitting before-open vs. after-close reporters. Reuse the same technique here rather than waiting on a paid tier.
- **Extended-hours price data**: confirmed 2026-08-16 via Polygon/Massive's own docs — the aggregates (bars) endpoint includes pre-market and after-hours trades **by default, on every paid tier including the current $79/mo Stocks Developer plan**. No parameter needed, no upgrade needed.
- **Minute-by-minute data — already solved, reuse don't rebuild**: AGSTOX already pulls real 1-minute bars (including extended hours) from the same Polygon/Massive account HuntHarvest uses — `fetch_intraday_bars()` in `agstox_exchange.py:33487`, hitting `.../v2/aggs/ticker/{ticker}/range/1/minute/{date}/{date}?adjusted=true&extended_hours=true`, live-verified during real market hours (2026-08-10, real VWAP/Opening Range numbers for AAPL). Stage 2 should reuse this exact fetch pattern (and `compute_vwap_orb_from_bars()`'s bar-filtering logic as a reference) rather than build a new one. No new vendor, no new data source, nothing left unverified on the feasibility side.

## 4. Core design: two tracks, not one

Before-open (BMO) and after-close (AMC) reporters have genuinely different reaction mechanics — conflating them was the wrong mental model. Each gets its own baseline logic and its own "when is the move real" checkpoint.

### Track A — BMO (reports before tomorrow's open)

- **Baseline**: today's regular-session close — the last clean pre-reaction price, nothing has happened to the stock since.
- **Mechanics**: report typically drops ~6:00–8:30am ET, well before the 9:30 open. Premarket absorbs the reaction directly. The 9:30 open is essentially premarket's verdict made official — one continuous reaction window, not several.
- **Checkpoint (when the move counts as "qualifying" and feeds the model)**: as soon as premarket price clears the ≥10% threshold — no need to wait for the opening bell. This is knowable before market open.

### Track B — AMC (reported today, after close)

- **Baseline**: today's regular-session close.
- **Mechanics**: report drops after 4:00pm, sometimes with an evening call attached. Three distinct checkpoints exist, not one:
  1. Immediate after-hours print (right after the release — thin volume, often overreacts)
  2. Next morning's premarket (more information priced in, still thin)
  3. Next day's regular-session open (real liquidity, institutions actually transacting)
- **Checkpoint (locked)**: the qualifying/model-feeding event is anchored to **the next day's regular-session open**, not the noisier after-hours or premarket prints. Matches Ashok's own framing — after-hours moves on thin volume regularly overstate or understate the real verdict; the tradable answer firms up at the next session's open.
- **Display rule (locked)**: the after-hours/premarket data IS shown live on the dashboard as the move forms, clearly labeled "forming / not yet confirmed" — useful context for watching it develop — but it does not drive the model and is not stored as the qualifying event.

### Nuance carried forward, not a blocker

The existing historical `events` table (37,773 events, QC-passed) was built on same-day close-to-close moves for *everything*, with no BMO/AMC distinction — for AMC that's a reasonable approximation of the above; for BMO it's looser (a full day's close-to-close can include intraday drift beyond just the report reaction). This doesn't invalidate already-QC'd historical data or trigger a redo. It's a reason the *live* pipeline needs its own more precise checkpoint logic rather than reusing that exact historical rule verbatim. Flagged for later reconsideration only if it matters in practice — see §5 for a deeper version of this same issue.

## 5. Abnormal-return foundation (event study methodology)

Added 2026-08-16, grounded in `RESEARCH_institutional_methodology.md` Parts 2 & 4. Concept locked; exact parameters (estimation-window length, recalibrated threshold) TBD at build time. This is the rigorous version of the "raw price % isn't quite the right yardstick" issue already flagged in §4.

**The mechanics** (standard event-study methodology, MacKinlay 1997):
1. **Estimation window** — a clean period before the event (e.g. trailing 120–250 trading days, stopping ~30 days short of the event to avoid pre-event run-up contamination) used to learn how this specific stock normally behaves.
2. **Market-model regression** on that window: `R_stock,t = α + β × R_market,t + ε` — a simple CAPM-style regression against SPY (already used elsewhere in the codebase as `MARKET_BENCHMARK`). Gives that ticker its own α/β.
3. **Abnormal return (AR)** on the event day: `AR_t = actual return − (α + β × market's actual return that day)` — the part of the move α/β don't explain.
4. **Cumulative abnormal return (CAR)**: sum of daily ARs forward from the event — the real "size of the market's reassessment," net of the stock just moving with the market.
5. **Statistical test**: t-stat on CAR (or average CAR across similar events), using estimation-window residual variance — not just eyeballing whether the average looks positive.

**Three concrete places this changes the design above:**

1. **Event detection (regression estimated in Stage 1, threshold checked in Stage 2 — §6)** — Stage 1's baseline capture now also estimates each watchlisted ticker's own α/β via the market-model regression (§5 mechanics, step 2); Stage 2 then replaces/augments the raw ≥10% price-change threshold with a check on AR computed from that α/β. `market_relative_return` (an existing feature) implicitly assumes β=1 for every stock; true AR uses each ticker's *own* β. A +12% move means something different for a high-beta growth name than a low-beta defensive one, and different again depending on what the market did that same day — the raw-% rule currently can't tell those apart.
2. **Reversion tracking (§7.1 post-settlement durability, §7.3 shape taxonomy)** — track **CAR returning to zero**, not raw price returning to prior close. Matters in practice, not just academically: if the broad market rallies for unrelated reasons over the following weeks, raw price can look "recovered" when the stock never genuinely bounced net of that rally — or the reverse, a market selloff can mask a real stock-specific recovery. Every HELD/GAVE_BACK/EXTENDED classification and shape-category label is more honest computed on CAR.
3. **Statistical backing for case-history claims** — use the **Boehmer-Musumeci-Poulsen (BMP) standardized cross-sectional test** (not a naive t-test) on average CAR over the recovery window, tested against the estimation window's own volatility, for per-ticker/per-category claims ("3 V-shaped, 1 dead-cat, 1 no-recovery") instead of a bare count. BMP specifically corrects for the event-induced variance increase that a plain t-test misses (`RESEARCH_institutional_methodology.md` §8.4) — same fix as the significance-reporting gap in the research doc's Part 5, applied here specifically with the actual named method.

**Why this is compatible with the small-daily-footprint design**: α/β are **per-ticker, not per-event** — estimated once from a trailing window, reusable across all of that ticker's events past and future. Computing it for the day's handful of watchlist names is one cheap OLS regression per ticker, fully compatible with the lazy, on-demand computation already locked in §7.5. Nothing here requires touching the small-daily-footprint constraint.

**Not yet decided**: exact estimation-window length; whether AR fully replaces the raw-% threshold or runs alongside it initially; whether to extend beyond a single-factor market model (e.g. Fama-French) later — deferred, single-factor is the standard baseline and sufficient to start.

## 6. Pipeline stages

**Stage 1 — Evening scan (~4:30pm ET, after close)** — *delivers §2 item #1, the starting point*
- Pull tomorrow's BMO reporters + today's AMC reporters (Finviz-calendar technique, §3), filtered to the tracked >$500M universe.
- For each, capture point-in-time baseline features — the same `FEATURES` list `train_models.py` already uses (RSI-14, ATR-14, price vs. SMA50/SMA200, 3-month momentum, volume ratio, market-relative return, sector-relative return, log market cap) — as of today's close.
- Also estimate each ticker's own α/β via the market-model regression (§5, step 2) against SPY over its trailing estimation window — cheap, one-time per ticker, reused by Stage 2's AR-based threshold check and by §7's CAR-based reversion tracking.
- Write into `upcoming_earnings` (schema already exists, currently unfed) or a new lightweight `daily_watch` table — small row count by design (a handful of names most days), becomes the dashboard's new default/primary view instead of the full 28-column history table.

**Stage 2 — Reaction tracking** — *delivers §2 items #2–4*
- Scoped to just that day's short watchlist — not the whole market, which is what makes intraday polling tractable (a handful of names a day, not 2,696 tickers). This small daily footprint is what makes everything below affordable.
- Poll Polygon intraday bars (pre-market included, per §3) at the track-appropriate window.
- Track A: poll from pre-market through open; flag "qualifying" the moment threshold (AR-based per §5, or raw-% initially) clears.
- Track B: display the after-hours/premarket move live as "forming"; lock the qualifying value at next day's regular-session open.
- **Raw data, not a chart**: today's reaction captured minute-by-minute (% move from baseline, time-aligned from report time/open) as full data — every point — alongside the same minute-by-minute data for this same ticker's own past qualifying events, normalized the same way, so the comparison is a real data-to-data match, not a visual read. A ticker landing on today's watchlist is the trigger to lazily compute its own past-event minute paths right then (per §7.5's on-demand rule — still just the day's handful of names, not a bulk backfill). If a ticker has too few/no past events with minute-level detail, fall back to the broader shape-category average data as the comparison, flagged as thinner-confidence.
- Once qualified, run the ticker through the existing trained model (`gain_clf`/`drop_clf`/`*_reg`) plus that ticker's own case history — same logic `app.py` already has for the historical view, applied live.

**Stage 3 — Settle** — *delivers §2 items #6 and #7*
- Once the checkpoint price is captured, the event folds into the normal `events`/`price_path` tables exactly as historical events do — no new storage model needed, reuses what's already built.
- Feeds into the next scheduled monthly retrain (`huntharvest-train.timer`) automatically.
- **Edge-strength tracking (locked 2026-08-16 — confirmed to build, not just a caveat)**: each monthly retrain also computes a rolling comparison — win rate / average CAR over the most recent N months vs. the prior N months (window TBD at build time) — and records whether the underlying edge looks like it's holding, weakening, or improving, rather than silently assuming a constant edge. Directly operationalizes the research finding that PEAD-style edges decay over time and shouldn't be assumed stable (`RESEARCH_institutional_methodology.md` §8.1). Surfaced somewhere visible (System/admin panel), not just logged and forgotten.

## 7. Post-event analysis layer

Three extensions (7.1–7.3), plus open items (7.4) and a backfill policy (7.5) — all additive to Stages 1–3, no new storage model, all derived from data the pipeline already collects. Delivers §2 item #5. Per §5, 7.1 and 7.3 below should ultimately compute on CAR, not raw price.

### 7.1 Post-settlement durability check
**Renamed and widened 2026-08-16** (was "post-recovery durability check," covered only `reverted=True` events). A bare revert/no-revert flag doesn't say whether the outcome *stuck* — and per the assumption correction below, most ≥10% events won't reach a clean full revert in the first place, so a durability check that only fires on full reverts would miss the majority of events. This now applies to **any event that reaches a settled read** — full revert (§7.3 V-SHAPED/GRIND-BACK) or partial reversal (§7.3 PARTIAL-REVERSAL) — not just full reverts. Once an event settles, keep watching for a mirrored window equal to that event's own `days_to_revert` (or, for partial-reversal events, the point where the retracement's pace first slows), and classify the outcome relative to wherever it settled:
- **HELD** — stayed at/beyond the settled level (full or partial) through the window
- **GAVE_BACK** (dead-cat bounce) — **locked definition**: price re-crosses back past the halfway point between the reaction price and the settled level, in the adverse direction, at any point in the window
- **EXTENDED** — kept moving favorably beyond the settled level — for a partial reversal, this means continuing on toward a fuller reversion, not just holding where it stalled
- **MONITORING** — window hasn't fully elapsed yet (right-censored, same discipline as `reverted` itself — never force a bucket before the window is actually over)
Computable entirely from the existing `price_path` table (full daily path, no fixed cutoff already stored) — no new data collection needed for events already in the DB.

**Assumption correction (2026-08-16, per `RESEARCH_institutional_methodology.md` §8.2-8.3)**: this section's original binary framing (reverted or not) understated what real ≥10% moves actually do. The literature for this specific extreme-move population points to short-horizon overreaction followed by *partial* reversal as the modal outcome, not a clean full-revert/no-revert split — and real gap-fill data backs this up (>10% gaps only fully fill 60% of the time within 5 days, vs. 90%+ for small 3-5% gaps). §7.3 below measures degree of retracement, and this section now tracks durability starting from wherever an event actually settles, not only from a full revert.

### 7.2 Reaction-accuracy scoring
Compares the early, noisy read (premarket print for Track A, immediate after-hours print for Track B) against the genuine locked checkpoint (open / next-day open, per §4). Bucketed, not a black-box score:
- **ACCURATE** — early read within tolerance of the genuine move
- **OVERREACTED** — early move bigger than genuine, same direction (market walked it back)
- **UNDERREACTED** — early move smaller than genuine, same direction (market kept extending)
- **REVERSED** — direction flipped entirely between early read and genuine checkpoint
Rolled up per-ticker, and per-sector/market-cap-bucket when a ticker's own sample is thin (same confidence-flag pattern already planned for case history) — surfaced as a descriptive stat ("this name's premarket has historically overstated the move by ~30%"), not a causal explanation. Full "why" attribution is out of scope for a personal tool.

### 7.3 Event shape taxonomy
A rule-based relabeling of what's already computed, plus one new measured dimension (retracement %) and one new input (relative volume) — both additive, no new data collection:
- **V-SHAPED** — fast full revert, held
- **GRIND-BACK** — slow revert (above a days-to-revert threshold, TBD at build time), held
- **PARTIAL-REVERSAL** (**added 2026-08-16**) — retraces a meaningful fraction of the move (e.g. 30-70%, exact band TBD at build time) but neither fully reverts nor fully holds through the tracking window. Per §7.1's assumption correction, this is expected to be the *modal* outcome for ≥10% moves, not an edge case — the taxonomy needs to represent it as a real, common bucket, not force events into the older binary V-SHAPED/NO-RECOVERY split.
- **DEAD-CAT** — reverted, then gave it back (= 7.1's GAVE_BACK)
- **NO-RECOVERY** — never reverted (the existing right-censored case)
- **CONTINUATION** / **FADE** — gains-side outcomes already framed in the base v2 spec (hold/add vs. sell-into-strength)

**Relative volume as a signal (added 2026-08-16)**: `volume_ratio` already exists as a baseline feature (§6, Stage 1) but wasn't previously used inside the shape read itself. Real gap-trading statistics show 3x+ relative volume with a clear catalyst tends to continue/hold, while low volume with no clear news tends to fade within the first hour — cheap to surface as a companion signal alongside the shape category (e.g. "V-SHAPED, but on thin relative volume — lower confidence this holds"), not a new data source.

Live use: when a new event is forming (Stage 2), show the ticker's own historical shape distribution (e.g. "last 5 events: 2 partial-reversal, 1 dead-cat, 1 V-shaped, 1 no-recovery") instead of a bare revert/no-revert flag — richer texture on the same per-ticker case list already planned in the base spec.

### 7.4 Not yet decided
- **Intraday poll cadence/window**: candidates discussed — first 30–60 minutes after open (lighter, catches the bulk of real price discovery) vs. all day (heavier, catches late-day continuation/fade too). Needs a decision before build.
- **Historical `events` re-derivation with track-aware checkpoints**: not urgent, existing data already passed QC — revisit only if the BMO-timing looseness noted in §4 turns out to matter.
- **AR/CAR estimation-window parameters** (§5): exact trailing window length, and whether to phase in alongside or instead of the raw-% threshold.
- **PARTIAL-REVERSAL retracement band** (§7.3): exact % range (e.g. 30-70%) that qualifies as "partial" rather than "full" or "none" — TBD at build time.
- **Deliverable #4's live-prediction framing** (added 2026-08-16, reviewed and deferred — not blocking): should the confirmed event card's live prediction move from a binary bounce/fade probability to a 3-way full-reversal/partial-reversal/continuation framing, matching §7.3's taxonomy correction? Fine to leave as binary for now; revisit later rather than deciding now.

### 7.5 Backfill scope — locked
**Forward-only + lazy on-demand.** New events get 7.1–7.3 computed automatically as part of the live pipeline. Existing historical events (37,773) get it computed the first time a user actually looks that ticker up in the app, then cached — not a blind batch backfill. Avoids ~37K extra one-day minute-bar pulls for data that may never actually get viewed.

## 8. Deferred — phase 2, not this build

- Analyst consensus estimates / whisper numbers / options-implied move ahead of the report ("what the big houses are predicting"). Real idea, but needs its own data source (an estimates feed or options IV/straddle pricing) that HuntHarvest doesn't currently have. Does not block Stages 1–3 above. Named real sources identified in `RESEARCH_institutional_methodology.md` Part 7 (EarningsWhispers/Estimize, options ATM straddle).

## 9. Non-goals

- This spec does not change the historical backfill, the monthly retrain cadence, or the existing 28-column full-history table — those stay as-is. It adds a new, smaller, live-facing layer on top.
- No new Polygon plan tier required (see §3).
