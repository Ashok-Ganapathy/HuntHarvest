# HuntHarvest — Pending Tasks

## 🟢 Calendar preview upgraded to full data columns + tomorrow BMO/AMC added — 2026-08-17
Ashok's request: replace the plain ticker-name list (tonight's AMC reporters) with the
same full column table as the real tracked rows, and add previews for tomorrow's BMO and
AMC reporters too (not just tonight's AMC). New `/api/calendar-preview` endpoint - fresh
Finviz fetch each call (~8s, a fresh bulk export, correctly kept OFF the 30s poll and on
its own 5-min interval instead), buckets into today_amc/tomorrow_bmo/tomorrow_amc,
returns rows in the exact same shape as real `daily_watch` rows so the frontend reuses
`renderDailyRow` unchanged. Baseline-specific fields (RSI/ATR/SMA/etc.) are honestly left
null/"n/a" for preview rows - that data genuinely doesn't exist until the real scan
captures it later, not faked. Cheap fields (sector, market cap, live price/day-change)
come from already-cached `tickers`/`live_quotes` tables, no extra per-ticker API calls.
Preview rows aren't clickable (no `watch_id`, nothing to expand). Live-verified in the
browser: FN/XP/YALA (tonight) and AS/BIDU/+more (tomorrow) all rendering correctly with
real sector/market-cap data and a "Preview" status badge.

## 🟢 Real bug: rows stuck at 'watching' forever once poll window closed — found+fixed 2026-08-17
Ashok asked for a routine status re-check ~2 hours after HTHT/DRUG/EMAT started tracking.
Found live data hadn't updated since 14:13 UTC despite the livepoll timer firing cleanly
every 5 min with zero errors - `in_poll_window()` was correctly returning False (60-min
post-open window had legitimately closed), which is right, but exposed a real logic gap:
`window_elapsed()` (the code that marks a row 'dropped' when its window closes without a
qualifying move) only ever ran *inside* `poll_row()`, which only runs for rows still
inside `in_poll_window()`. Once a row's window closes, it drops out of `active` and
`poll_row()` never runs for it again - so a ticker that never crosses threshold stayed
stuck at 'watching' forever instead of resolving to 'dropped'. Fixed: `main()` now also
checks every "touches today" candidate NOT already handled this run for an elapsed
window, independent of the active-poll filter. Verified live: HTHT/DRUG/EMAT all
correctly transitioned to 'dropped' on the next run (none crossed ±10% today - HTHT
topped out under threshold, DRUG and EMAT never moved meaningfully) - confirmed via
both the manual run's log output and the live API/frontend.

## 🟢 Morning catch-up scan + baseline-contamination bug — found, fixed, verified 2026-08-17
Ashok noticed EMAT/DRUG missing from today's watchlist despite genuinely reporting BMO
today - cross-checked against AGSTOX's own independent earnings-calendar parse (same
Finviz source, different code) via a live screenshot, which **confirmed both are real**
(today · before open, alongside HTHT). Root cause: the evening scan is a single
snapshot; Finviz added/confirmed these two AFTER last night's run, with no catch-up
mechanism. My first read (dismissing DRUG/EMAT as not real opportunities, based on
EMAT's price already having moved Friday) was premature and got corrected by checking
AGSTOX's calendar rather than trusting my own read.

Built `daily_watch_morning_catchup.py` (~7am ET daily, well before open) to catch
same-day BMO reporters the evening scan missed. **Second real bug caught while
building it**: my first manual run picked up a contaminated baseline (EMAT $3.08
instead of Friday's real $3.03 close) because `compute_baseline_features()` always
fetched through "today" and took the last row - correct for the evening scan (runs
after close) but wrong run mid-session, where Polygon's "today" bar reflects the
current in-progress price, not a clean baseline. Fixed with a new `exclude_today`
parameter (also correctly filters the shared SPY/sector benchmark frames, not just the
ticker's own bars - same contamination risk applied there too). Cleared the
contaminated rows, reran, verified clean baselines exactly matching Friday's real
closes (EMAT $3.03, DRUG $76.44).

**Third, smaller bug in the same live-testing pass**: the "Reporting Tomorrow Evening"
preview heading (FN/XP/YALA) was still hardcoded/static - same class of staleness as
the two headings fixed earlier, just missed on that pass. Now dynamic like the other
two, flips to "Reporting Today Evening" once watch_date arrives.

All three fixes live-verified in the browser during actual Monday market hours:
3 tickers now correctly tracked before-open (DRUG/EMAT/HTHT), preview heading correct,
real live price still flowing for HTHT. New `huntharvest-morningcatchup.timer` (weekday
11:00 UTC) deployed and enabled.

## 🟢 Real bugs found live during Monday's actual market open — fixed 2026-08-17
First genuine trading-day test (HTHT reporting BMO) surfaced two real gaps, both found by
Ashok actually watching the dashboard live, not by design review:
1. **No auto-refresh at all.** `loadDailyWatch()` was only ever called once, on page load
   (`initDashboard()`) - zero `setInterval` anywhere. A tab left open showed whatever it
   looked like at load time forever, not live state. Fixed: polls every 30s. Also added a
   real live-price column to the main table (`/api/daily-watch` now joins the latest
   `live_reaction_ticks` row per watch_id) - previously the only way to see the forming
   reaction at all was clicking into the raw-ticks detail view.
2. **Stale section headings once watch_date actually arrives.** "Reporting Tomorrow" /
   "Reported Today" are correct language the evening the scan runs, but read as wrong once
   you're actually viewing the dashboard ON watch_date itself (e.g. Monday morning showing
   "Reporting Tomorrow" for a stock reporting that same morning). Fixed: headings/empty-
   state copy now flip based on whether `watch_date` is today.
Both live-verified against the real, live HTHT situation during actual market hours
(premarket +8.9%, real-time in the browser) before being called done.

## 🟢 Checkpoint-lock notifications — working via AGSTOX push relay, closed 2026-08-16
SMS attempt (below) turned out to be carrier-blocked (real A2P 10DLC compliance issue,
not fixable via code - needs Ashok's actual business registration through Twilio's
Console directly). Email was ruled out (Ashok doesn't check it regularly). Real working
answer: **relay through AGSTOX's already-proven push infrastructure.** Added
`/api/internal/push` to `agstox_exchange.py` (small, additive, token-gated with the
existing `AGSTOX_INTERNAL_TOKEN`, hardcoded to `user_id=1`/ashok - not a general
multi-tenant relay) - reuses AGSTOX's working `send_push()` (APNs to Ashok's iOS app +
Web Push) instead of building new infrastructure from scratch. HuntHarvest's `notify.py`
gained `send_push_via_agstox()`, wired into `live_reaction_poll.py`'s checkpoint-lock
point (replacing the blocked SMS call - `send_sms()` kept in the file, correct and
ready, just not called by the live pipeline). **Two real end-to-end tests confirmed
delivered to Ashok's phone**: one direct to the new AGSTOX endpoint, one through
HuntHarvest's own droplet exercising the full real path. This is now the working
notification channel - no email, no SMS.

## 🟡 SMS checkpoint-lock notifications — built, but BLOCKED (see above for what's actually used)
Ashok asked how he'd know about a confirmed drop/gain if not in front of the app - real
gap, everything built so far was pull-only. New `notify.py` - direct Twilio REST API
integration (NOT copied from AGSTOX's `send_sms()`, which was found to no longer send
real SMS at all - fully replaced by APNs push there, kept only as a compatibility
wrapper; HuntHarvest has no app to push to, so this needed a fresh implementation using
the still-valid Twilio credentials). Wired into `live_reaction_poll.py` right at the
checkpoint-lock point - fires the moment a drop/gain is confirmed, with ticker/direction/
move%/model probability/expected days. Best-effort by design (a notification failure
never blocks the actual settle). **Real test SMS sent and accepted by Twilio's API** -
credentials confirmed live, not just assumed from stale docs.

## 🟢 Real gap found + fixed via live use: AMC-tomorrow-evening preview — 2026-08-16
Ashok noticed FN/XP/YALA missing from tomorrow's watchlist despite knowing they report
soon. Traced it: not a bug in the existing logic - all three report **8/17 after close**,
so their real reaction is Tuesday 8/18 (next-session-open rule), correctly excluded from
Monday's active tracking. But this exposed a genuine UX gap: no advance visibility into
tomorrow-evening's AMC reporters until the evening they're already reacting. Added a third
"Reporting Tomorrow Evening — After Close" preview section: `daily_watch_scan.py` now also
writes tomorrow-evening AMC reporters to `upcoming_earnings` (identity/timing only,
deliberately no baseline features - today's close isn't their correct baseline, Monday's
close is, captured correctly by Monday evening's own scan run). New
`preview_amc_tomorrow_evening` field on `/api/daily-watch`. Live-verified: real scan run
found HTHT (tracked) + FN/XP/YALA (preview), rendered correctly in the browser.

## 🟢 Git properly set up for v2 — closed 2026-08-16
**Real, unrelated-to-the-task finding caught while investigating, not blindly executed
around**: `~/HuntHarvest` had no `.git` of its own - a stray `.git` was instead sitting at
`/Users/ashokganapathy/.git` (the whole home folder), remote already pointed at the
HuntHarvest GitHub repo, no commits yet (so no damage done, but `git add -A` from inside
HuntHarvest would have staged the entire home directory - .ssh, .bash_history,
.claude.json, AGSTOX's own files, everything). Flagged directly instead of fixing it
silently. Also found the remote's `main` wasn't empty either - it held v1's actual single
commit ("whole site from droplet"), and the `v1-legacy` tag the docs described had never
actually been pushed. Fixed, with explicit go-ahead at each consequential step: removed the
stray home-level `.git`; `git init` properly scoped to `~/HuntHarvest`; tagged the existing
remote main commit as `v1-legacy` (now real, pushed); committed all 39 v2 files (respecting
the existing `.gitignore` - confirmed no `SECRETS.md`/`.env`/`venv`/`__pycache__` got
staged); force-pushed (`--force-with-lease`, not a bare `--force`) as the new `main`.
Verified on the remote afterward: `main` → v2 commit `9449d6c`, `v1-legacy` tag → old
commit `d3abd71`, clean working tree.

**Droplet rename + destroy — closed 2026-08-16.** Ashok handled both himself in the DO
dashboard that same morning. Confirmed via a fresh write-scoped DigitalOcean token (added
to SECRETS.md) before touching anything: droplet 592705831 already shows as "HuntHarvest"
(not the old default hostname), and 165.227.88.24 no longer appears in the account's
droplet list at all. Nothing left to do — SECRETS.md updated to match reality.

## 🟢 Backfill items #1/#2 completed + edge-strength verified 2026-08-16
Ran the deep backfill (`backfill_deep_analysis.py`, explicit go-ahead) across all 3,277
tickers: α/β computed for 2,587 tickers, `price_path` extended for 30,009 reverted events.
Reran `backfill_event_analysis.py` afterward - **verified the real effect, not just a clean
exit code**: the summary log line looked suspiciously identical to the pre-extension run, so
checked the actual data underneath instead of trusting it. Real change confirmed: `dead_cat`
went from 0 (structurally unreachable) to 1,911 genuine classifications; shape distribution
shifted substantially since `retracement_fraction` now reflects ~40 real days after
reversion instead of the crossing-moment snapshot. Remaining 1,498 unfinalized reverted
events checked directly - all genuinely <90 days old, not a bug, will finalize via the
weekly timer as they age.

**Real bug caught testing the edge-strength feature for the first time** (`train_models.py`
had never actually been run since that code was added): `check_edge_strength()` assumed
dict-style cursor access (`row["n"]`) but this file's `conn.cursor()` returns a plain tuple
cursor, unlike the rest of the codebase's `DictCursor` convention - crashed on first real
run. Fixed to tuple-unpack, redeployed, reran clean. **First real edge-strength result**:
recent win rate 73.59% (n=5,423, Nov 2025-May 2026) vs. prior 73.07% (n=4,326, May-Nov
2025) → trend=**holding**, not decaying - logged to `edge_strength_log`. Models refreshed
(fresh `.pkl` timestamps 2026-08-16 20:40) with today's full dataset including everything
from today's build.

## 🟢 Daily Pipeline Phase 3 — built, deployed, verified against real data 2026-08-16
Per `Specs/SPEC_daily_pipeline.md` §5, §6 Stage 3, §7. Two open parameters locked: AR/CAR
estimation window = 252 trading days ending 30 days before the event (min 120 required,
else skipped), runs ALONGSIDE Phase 2's already-live raw-% threshold rather than replacing
it; PARTIAL-REVERSAL band = retracement_fraction in [0.30, 1.00), V-SHAPED/GRIND-BACK split
at 10 trading days.

**Built**: `event_analysis.py` (shared classification math - AR/CAR market-model regression,
retracement fraction, shape taxonomy, post-settlement durability, reaction-accuracy scoring),
`settle_watch.py` (folds settled `daily_watch` rows into permanent `events`/`price_path`,
runs every 15 min), `backfill_event_analysis.py` (one-time local-only pass, zero new API
calls, over existing historical events), `track_live_events.py` (daily forward-tracker
scoped only to events this pipeline itself creates - old-backfill daily incremental ingest
stays out of scope per the base spec's non-goals), edge-strength tracking added to
`train_models.py` (new `edge_strength_log`, runs as part of the existing monthly retrain).
New `events`/`tickers` columns, new Status/Confirmed Read-adjacent Shape column on Earnings
Watch, shape distribution + per-event shape/outcome tags in the case-history panel.

**Verified against real data**: ran the historical backfill for real across all 37,786
existing events (zero API cost - pure local computation over already-stored `price_path`).
**Real bug caught and fixed during verification, not after**: the backfill initially showed
28,542 reverted events as `post_settlement_outcome='held'` - checking the actual numbers
(reverted events average only 66.6 observed days vs. 332 for non-reverted) revealed this was
a false default, not a genuine result - `ingest_historical.py`'s original backfill stops
recording `price_path` the DAY a reversion is first detected, so there was no data past that
point to actually check durability against. Fixed `classify_post_settlement()` to return
`None` (honestly unknown) instead of defaulting to "held" when the peak is the last visible
data point, reset and reran the full backfill with the fix. Also verified the AR/CAR
regression against 3 real tickers (HTHT beta=0.36, WTI beta=-0.67, TXG beta=2.32 - all
plausible, correct 252-day window).

**Known real limitation, not silently hidden**: `dead_cat` shape classification is currently
unreachable for the historical backfill's reverted events, for the same root reason as the
bug above - their `price_path` has no data past the reversion point. Only NEW events created
via this pipeline (`track_live_events.py`, which doesn't truncate at reversion) will be able
to reach `dead_cat`. Extending `price_path` for the ~30,000 historical reverted events would
fix this but needs real new Polygon API calls - a real, scoped follow-up, not done here.

**Explicitly not run**: the full ~2,696-ticker α/β backfill (real API cost/time) - only
demonstrated on 3 tickers. Flagged as an opt-in follow-up, not run blindly.

**Gap caught and closed same session**: `backfill_event_analysis.py` was only run as a
manual one-time pass - nothing was scheduled to re-run it, so the ~3,249 events still
`monitoring` (event too recent for its 90-day settled window) would never actually
finalize as time passed. Added `huntharvest-analysisbackfill.timer` (weekly, zero API
cost - pure local computation) so newly-eligible events get finalized automatically going
forward. All 7 pipeline timers now confirmed active on the droplet: dailywatch, livepoll,
settle, tracklive, analysisbackfill, quotes, train.

**Three real backfill items total, all tracked here**: (1) extend `price_path` for
~30,000 historical reverted events to unlock `dead_cat` classification - needs new
Polygon calls, not done; (2) the ~2,696-ticker α/β backfill - needs new Polygon calls,
not done; (3) the analysis-finalization re-run - now scheduled weekly, closed.

All of Phase 1, 2, and 3 are now built and deployed. **Monday 2026-08-17 remains the first
real end-to-end trading-day test** (HTHT reports BMO) - worth checking that day that a real
event flows all the way through settle → fold into `events` → Phase 3 analysis once enough
time has passed.

## 🟢 Daily Pipeline deliverable #3 (historical comparison) — built, deployed, live-verified 2026-08-16
Per `Specs/SPEC_daily_pipeline.md` §2 deliverable #3. New `/api/daily-watch/{id}/history-compare`
endpoint - today's live ticks alongside this same ticker's own past qualifying events'
minute-by-minute data, normalized against each event's own baseline, computed on-demand
(a handful of Polygon calls per click, not pre-cached or bulk-backfilled). Frontend: past
events listed with real move%/reverted status, each collapsible to its own raw tick list
(no chart, all data - locked design). No shape-category fallback yet (§7.3's taxonomy is
Phase 3, not built) - a ticker with no history just says so plainly. **Live-verified with
real data**: HTHT's row correctly showed 5 real past qualifying events (2022-2024, all
gains, move% 10.8-16.6%, real revert status/days), expanding one showed genuine Polygon
minute bars from 2024-12-09 normalized correctly against that event's own prior_close.
All three of Phase 2's spec deliverables (#2, #3, #4) are now built. Only Phase 3 (Stage 3
settle job + post-event analysis layer) remains, still gated on the AR/CAR window and
partial-reversal band decisions.

## 🟢 Daily Pipeline Phase 2 (core) — built, deployed, verified against real data 2026-08-16
Per `Specs/SPEC_daily_pipeline.md` §2 deliverables #2 and #4 (live reaction data + confirmed
event card). Poll cadence locked: premarket through open+60min for Track A; report-evening
after-hours through open+60min for Track B, checkpoint anchored specifically to the next-day
open print for Track B (not the noisier earlier prints), per spec §4's locked design.
`live_reaction_poll.py` runs every 5 min via `huntharvest-livepoll.timer` (cheap no-op when
nothing's in an active window), captures real 1-min bars (reusing AGSTOX's proven
`fetch_intraday_bars()` pattern) into new `live_reaction_ticks`, and once a checkpoint locks,
runs the EXISTING trained model on the baseline features already captured in Phase 1 — no
retraining needed. New `daily_watch` columns (checkpoint_price/pct/locked_at,
predicted_probability/expected_days/direction), new `/api/daily-watch/{id}/ticks` endpoint,
new Status + Confirmed Read columns on the Daily Watch tab with a click-to-expand raw-tick
view (no chart, all data — locked design decision).
**Verified against real data, not just deployed**: since today is a weekend, live real-time
behavior can't be observed until markets are open — instead ran the actual `poll_row()` logic
against a real historical event (WTI, 2026-08-10, +11.37% gain) via a temporary test row,
confirmed real Polygon minute bars were fetched (444 ticks, premarket through after-hours),
the checkpoint correctly locked at the actual threshold-crossing tick (+10.06% at 15:48 UTC),
and the existing trained model produced a real prediction (47.65% fade probability, ~52
expected days) — then deleted the test row. Real bug caught+fixed during this build: the AMC
table's column headers weren't updated when Status/Confirmed Read columns were added (only the
BMO table's `old_string` match was unique enough for the edit) — would have caused a silent
column-misalignment once a real AMC row appeared. Fixed and re-verified via curl before
declaring done.
**Not yet built**: deliverable #3 (historical comparison — today's minute data vs. this
ticker's own past events' minute data, on-demand) — a real next increment, deliberately not
rushed into this same pass. Also not yet built: Stage 3/Phase 3 (settle job — folding a
confirmed event into `events`/`price_path`, post-event analysis layer, edge-strength
tracking) — still needs the AR/CAR estimation-window length and PARTIAL-REVERSAL retracement
band decided first. **First real end-to-end trading-day test**: HTHT reports 2026-08-17 BMO —
worth checking Monday that the live poll actually catches its real reaction.

## 🟢 Daily Pipeline Phase 1 — built, deployed, live-verified 2026-08-16
Per `Specs/SPEC_daily_pipeline.md` §2 deliverable #1 (the starting point). New "Daily Watch"
tab (now the default view) shows tomorrow's small watchlist: Track A (BMO, reports before
tomorrow's open) and Track B (AMC, reported today after close) — both react at the same
next-session open. `daily_watch_scan.py` runs weekdays 21:30 UTC (`huntharvest-dailywatch.timer`,
after `huntharvest.service` is restarted with the new code), reuses AGSTOX's Finviz
earnings-calendar-parsing technique (`_earnings_timing()`), gates on HuntHarvest's own
already-cap-filtered `tickers` table, and captures the same baseline `FEATURES` set as the
historical `events` table (RSI-14/ATR-14/SMA-relative/momentum/vol-ratio/market+sector-relative
return) as of today's close. New `daily_watch` table + `/api/daily-watch` endpoint. Live-verified
end-to-end: real HTHT row (reports 2026-08-17 BMO) flowing DB → API → frontend, correct empty-state
on the AMC side, no regression on the existing Earnings Watch/System tabs (now lazy-loaded).
**Not yet built** (needs their own locked open items first, per the phased build plan):
Phase 2 (Stage 2, live reaction tracking — needs intraday poll cadence decided) and Phase 3
(Stage 3 + post-event analysis layer — needs AR/CAR estimation-window length and the
PARTIAL-REVERSAL retracement band decided).

## 🟢 LIVE (2026-08-16) — huntorharvest.com is up in production
Full v2 rebuild complete and live: backfill, training, QC, frontend, and go-live
infrastructure all done and verified.

### Go-live checklist — all done
1. ✅ Real `huntharvest` systemd service started and enabled (replaced the ad-hoc test instance used during development)
2. ✅ DNS cutover in Cloudflare — huntorharvest.com and www both point to 142.93.196.178
3. ✅ Let's Encrypt SSL issued via certbot (ECDSA, expires 2026-11-14, auto-renewal configured) — HTTPS confirmed working on both domains
4. ✅ `huntharvest-quotes.timer` — refreshes `live_quotes` every 30 minutes
5. ✅ `huntharvest-train.timer` — retrains models monthly, matching the locked spec's cadence
6. ✅ End-to-end verified live: real predictions, real per-ticker case history, real Recom suggestions all confirmed serving over `https://huntorharvest.com/api/events`

### Build summary
- New droplet (142.93.196.178, 2GB RAM), MySQL (8 tables) + Python stack, replacing the original droplet which died mid-install
- **Historical backfill**: 37,773 events, 2,696 distinct tickers, 2022-01-01 to 2026-08-14. Two real data-integrity bugs found and fixed via smoke-testing/QC before trusting this data:
  - Point-in-time market cap qualification (same bug class that broke v1)
  - **Ticker symbol reassignment** (Polygon reuses tickers across unrelated companies — e.g. Facebook/Meta's FB→META change produced a fake +1394% "event" from an unrelated company's history before the switch date). Fixed via Polygon's ticker-events endpoint.
  - SEZL's `2025-08-08 -34.32%, $3.08B` event confirms v1's exact missing flagship proof case is now present.
- **Models**: separate classifier+regressor per direction (drop/gain), class-balanced (an unweighted first pass showed misleadingly high 81%/75% accuracy that was just always guessing the majority class). Honest numbers: 64%/63% accuracy, 53-67% recall both classes. Days-to-revert regression is weak (R² 0.05-0.10) — paired with per-ticker case history by design, not relied on alone.
- **QC**: all 8 structural checks green. 10 outlier moves >75% flagged for review, not failures — spot-checked two (ALOY, BMNR) against raw price data, both genuine.
- **Frontend**: sidebar nav (Earnings Watch/System), full-width sortable 28-column table, expandable per-ticker history, admin config panel. Iteratively refined through live testing — see PROJECT_STATE.md for the full bug list found along the way.
- **Live quotes**: current price, day/week/month % change, relative volume — refreshed every 30 min via timer.

## Still open / lower priority

### Daily same-day pipeline — NOT built yet (real gap surfaced 2026-08-16, design in progress)
Discussed 2026-08-16: today's app is backward-looking only — it shows a ticker's past history on request, it does not detect or flag today's/tomorrow's earnings reactions on its own. Three sub-gaps, previously tracked separately, now understood as one connected pipeline:
1. **Forward-looking earnings calendar (day-before-prep)** — blocked on Polygon's Benzinga/TMX endpoints (gated on current $79/mo plan). `upcoming_earnings` table exists in schema, no ingestion feeds it. **Real unblock candidate found 2026-08-16, not yet tried**: AGSTOX's `/api/earnings-calendar` already parses Finviz's raw earnings-date+time string (before/after-open split) reliably, live, for free — same technique could feed HuntHarvest's evening-before scan instead of waiting on a Polygon upgrade.
2. **No daily incremental event-ingest** — `ingest_historical.py` is a full backfill (idempotent, but slow, ~1.5hr), not a "just check yesterday" job.
3. **No live/intraday reaction tracking** — the original spec called for intraday 5-/10-min bars pulled live for tickers crossing the ≥10% threshold *today*, but this was never actually written, only spec'd.
**Design locked 2026-08-16 (not yet built — needs explicit build go-ahead)**: two separate tracks, not one — BMO and AMC reporters have genuinely different reaction mechanics, not just a label difference.
- **Data feasibility confirmed 2026-08-16**: Polygon's aggregates endpoint includes pre-market and after-hours trades by default, on every paid tier including the current $79/mo plan — no upgrade, no parameter needed. Nothing blocking intraday/extended-hours polling.
- **Stage 1 (evening scan, ~4:30pm ET after close)**: pull tomorrow's BMO reporters + today's AMC reporters (Finviz-style calendar technique, reused from AGSTOX's `/api/earnings-calendar`), filter to tracked universe, capture point-in-time baseline features (same FEATURES set already used in training). Small daily list by design — becomes the dashboard's primary/default view instead of the full 28-column history table. Two sub-lists, not merged:
  - **Track A — BMO (reports before tomorrow's open)**: baseline = today's close (last clean pre-reaction price). One continuous reaction window: report drops ~6-8:30am, premarket absorbs it, 9:30 open is premarket's verdict made official. Qualifying move can be checked against premarket price as soon as it clears threshold — don't need to wait for the open bell.
  - **Track B — AMC (reported today after close)**: baseline = today's close. Three checkpoints exist (immediate after-hours print, next morning's premarket, next day's regular-session open) — after-hours/premarket are thin-volume and often over/understate the real move. **Locked: the qualifying/model-feeding event is anchored to the next day's regular-session open** (real liquidity, matches the "genuine recovery or fall" framing), not the noisier earlier prints.
  - **Locked: after-hours/premarket prints ARE shown live on the dashboard** before the genuine checkpoint locks in, clearly labeled "forming/not yet confirmed" — useful context, but they don't drive the model or get stored as the qualifying event.
- **Stage 2 (reaction tracking)**: for just that day's short watchlist (not the whole market — makes intraday polling tractable), poll Polygon intraday bars, confirm the move crosses the qualifying threshold at the track-appropriate checkpoint, then run it through the existing trained model + that ticker's own case history for a live bounce/fade read.
- **Stage 3 (settle)**: once the qualifying checkpoint price is in, the event folds into the normal `events`/`price_path` tables and feeds the next monthly retrain — no new storage model needed, reuses what's already built.
- **Not yet decided**: exact intraday poll cadence/window (a prior open question — first 30-60min after open vs. all day — still unanswered), and whether historical `events` (built on same-day close-to-close for everything, no BMO/AMC distinction) should eventually be re-derived with track-aware checkpoints — not urgent, that data already passed QC, flagged for later if it matters.
- **Deferred, phase 2**: analyst consensus estimates / whisper numbers / options-implied move ahead of the report ("what the big houses are predicting") — needs a different data source (estimates feed or options IV), not blocking this build.
- Training-run metrics aren't persisted anywhere queryable today — `train_models.py` only logs classification-report/R² to stdout. Future scheduled runs (via `huntharvest-train.timer`) will be captured in `journalctl -u huntharvest-train.service` automatically since it's a systemd service; today's initial manual run's numbers are already lost and not re-checkable without rerunning.
- Rename droplet in DO dashboard from default `ubuntu-s-1vcpu-2gb-nyc1` to `HuntHarvest` (cosmetic)
- Old droplet (165.227.88.24) still not destroyed in DO dashboard — safe to do, everything is archived locally in `~/HuntHarvest_v1_archive`
- GitHub repo needs fresh `git init` for v2 code (currently only has archived v1 history, tagged `v1-legacy`) — not done yet, per the sandbox git caution (same as AGSTOX)
