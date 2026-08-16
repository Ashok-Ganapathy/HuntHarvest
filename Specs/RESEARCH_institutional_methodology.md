# Research — Institutional/Academic Methodology vs. HuntHarvest

**Purpose**: reference material, not a build spec. Compiled 2026-08-16 while designing the daily pipeline (see `SPEC_daily_pipeline.md`) to answer: what do real quant researchers and PhDs actually do with this exact question (does a stock's price reaction to an event hold or revert), separate from what any retail app productizes — and where does HuntHarvest's current build stand against that.

**Core finding, stated plainly**: HuntHarvest is not chasing an unproven idea. The phenomenon it's built around — Post-Earnings Announcement Drift — is one of the most replicated results in empirical finance, 50+ years of literature deep. The model architecture (gradient-boosted trees) matches what earnings-forecasting research still considers a strong baseline. The gaps between HuntHarvest and institutional-grade practice are specific, nameable, and mostly about *rigor of measurement* and *validation discipline*, not about the underlying idea being wrong.

---

## Part 1 — The phenomenon: Post-Earnings Announcement Drift (PEAD)

Bernard & Thomas (1989) showed markets don't fully price in an earnings surprise immediately — prices keep drifting in the surprise's direction for weeks afterward, because institutional position-adjustment is gradual, not instant. This is the direct academic ancestor of what HuntHarvest's `reverted`/`days_to_revert`/shape-taxonomy fields are testing (bounce back = reversion wins; keep going = drift wins).

- **Institutional strategy shape**: rank the full cross-section of reporting stocks by earnings surprise (SUE, see Part 2), long the top decile, short the bottom decile, hold ~60 trading days. Baseline annual return ~12.5%; a value-stock-filtered variant runs 16.6-18.8%/yr.
- **Real calibration point**: academic PEAD drift runs roughly **60 trading days (~3 months)**, with 25-30% of the total drift concentrated in tight 3-day windows around the *next* quarterly report (only ~5% of trading days). Worth checking once the pipeline's live: does HuntHarvest's own `days_to_revert` distribution land anywhere near that ~60-day window, as an external sanity check.
- **Honest scope difference**: institutional PEAD strategies trade the *full cross-section* of surprises (including modest ones) as a market-neutral book across hundreds of names. HuntHarvest deliberately targets only the **extreme tail** (≥10% single-day reactions) for a single person watching a handful of names a day. Different use case, not a lesser version of the same one.

Sources: [Quantpedia — Post-Earnings Announcement Effect](https://quantpedia.com/strategies/post-earnings-announcement-effect), [DayTrading.com — PEAD Strategy](https://www.daytrading.com/post-earnings-announcement-drift-pead-strategy)

## Part 2 — Measuring the event properly: abnormal returns, not raw returns

The academic event-study standard (MacKinlay 1997) never measures a stock's raw price move. It computes an **abnormal return**: `AR = actual return − expected return`, where "expected" comes from a market-model regression (`R = α + β×R_market`) estimated over a prior clean window, then sums those into a **Cumulative Abnormal Return (CAR)** over the event window — isolating what's attributable to the event itself, net of the stock just moving with the market that day.

- **Where HuntHarvest stands**: the ≥10% event-detection threshold is applied to *raw* price change, not abnormal return. `market_relative_return`/`sector_relative_return` exist as model *features* fed in after the fact, but don't define what counts as an event in the first place. Consequence: a +12% move on a day the whole market ripped +5%, and a +12% move on a flat day, are currently tagged as equally "extreme" — a properly beta-adjusted event study would treat those very differently.

Sources: [Event Study Methodology — Step-by-Step Guide](https://www.eventstudytools.com/introduction-event-study-methodology), [Cumulative Abnormal Return explainer](https://eventstudy.de/blog/cumulative-abnormal-return)

## Part 3 — Measuring the surprise properly: SUE, not a flat threshold

Standardized Unexpected Earnings scales the surprise by that specific firm's own historical forecast-error volatility: `SUE = (surprise) / σ(historical surprises)`. Principle: the same raw surprise means something very different for a stock that reliably surprises a little every quarter versus one that almost never does.

- **Where HuntHarvest stands**: one flat ≥10% price-move rule, identical across the entire universe — and it's driven by *price* reaction, not the underlying EPS-vs-consensus surprise at all (no analyst-estimate data feeds this). A deeper distinction worth being honest about: HuntHarvest currently detects "big price reactions that happen to cluster near an earnings date" (via SEC-filing-date proximity tagging), not "big earnings surprises" per se — a stock can move 12% on guidance commentary with an in-line EPS print, or on unrelated sector rotation that day, and get the causal 'earnings' tag either way. Real SUE methodology needs the actual consensus-estimate input this tool doesn't have yet (same gap already tracked as "big houses' predictions" / whisper numbers, phase 2 in the pipeline spec — now with the academic name attached).

Sources: [Standardized Unexpected Earnings — QuantConnect](https://www.quantconnect.com/research/15369/standardized-unexpected-earnings/), [SUE — Breaking Down Finance](https://breakingdownfinance.com/trading-strategies/standardized-unexpected-earnings-sue/)

## Part 4 — Validating the model properly: purged cross-validation, not random splits

Marcos López de Prado's core finding (*Advances in Financial Machine Learning*, 2018): standard random train/test splitting is a real trap in finance specifically because labels depend on future price data. If a training row's outcome window overlaps in time with a test row's — or training data simply falls chronologically after the test set — the model leaks information and validation accuracy comes back **falsely optimistic**. His fix, purged (and embargoed) cross-validation, explicitly removes any training observation whose label period overlaps the test period.

- **Where HuntHarvest stands — the single most concrete, fixable finding in this whole review**: `train_models.py` uses `train_test_split(X, y, test_size=0.2, random_state=42, stratify=...)` — a plain **random** split, no purging, no time-ordering. The reported 63-64% accuracy hasn't been validated the way a serious quant shop would insist on, and could plausibly be somewhat inflated versus genuine walk-forward, out-of-sample performance. Worth fixing before trusting that number further, independent of anything else in this document.

Sources: [Purged cross-validation — Wikipedia](https://en.wikipedia.org/wiki/Purged_cross-validation), [Advances in Financial Machine Learning — López de Prado](https://toc.library.ethz.ch/objects/pdf03/e01_978-1-119-48208-6_01.pdf)

## Part 5 — Trusting the result properly: factor-zoo discipline

Harvey, Liu & Zhu (2016) reviewed 316 published "anomaly" factors and found most don't survive a properly corrected significance bar — the old t>2 rule of thumb is far too loose once you account for how many things get tried; their recommendation is t>3 minimum, with Bonferroni/false-discovery-rate correction on top. The broader discipline: a signal needs real economic rationale, not just a fit that happened to work this time.

- **Where HuntHarvest stands, and this part's genuinely fine**: the current `FEATURES` list (RSI-14, ATR-14, price-vs-SMA50/200, 3-month momentum, volume ratio, market/sector-relative return, log market cap, recency) is economically motivated, not mined — good practice already, not a factor-zoo risk. What's missing is the reporting discipline: no confidence interval or significance test is reported alongside the 63-64% accuracy figure, just a point estimate. Combined with Part 4's finding, that number deserves more scrutiny than it's gotten so far.

Sources: [Taming the Factor Zoo](https://dachxiu.chicagobooth.edu/download/ZOO.pdf), [Finance's Replication Crisis: Harvey-Liu-Zhu 2016](https://atticusli.com/replication-crisis/finance-replication-crisis-harvey-2016/)

## Part 6 — Modeling technique: where the current choice stands

Classic ML (SVMs, Random Forests, Gradient Boosting) has dominated earnings-surprise prediction research since roughly 2000, valued for handling hundreds of variables and capturing non-linear relationships. More specifically, XGBoost-family models have been shown to produce the lowest forecast error across multi-year horizons for earnings prediction — a serious, current baseline, not an outdated technique.

- **Where HuntHarvest stands — good news**: `train_models.py` already uses gradient-boosted trees (scikit-learn's `GradientBoostingClassifier`/`Regressor`). Not behind the curve on the core algorithm choice for a tool at this scale.
- **Real gap, appropriately out of scope for now**: the more advanced published approaches fuse a text/sentiment layer (BERT on the actual earnings release or call transcript) with a transformer for return forecasting and a graph network for cross-stock relationships, then gradient boosting on top to combine it all. HuntHarvest is price/technical features only — no NLP layer reading what management actually said. Legitimate phase-3+ idea (would need earnings-call transcript ingestion, a project on its own), not something to chase now.

Sources: [Machine Learning, Earnings Forecasting, and Implied Cost of Capital](https://www.bauer.uh.edu/departments/accy/research/documents/machine-learning-earnings-forecasting-and-implied-cost-of-capital.pdf), [FinCall-Surprise benchmark](https://arxiv.org/pdf/2510.03965)

## Part 7 — Named, concrete sources for the "big houses' predictions" phase-2 idea

Two real, specific data sources exist for the analyst-consensus/whisper-number/options-implied-move layer already parked as phase 2 in the pipeline spec — not just an abstract idea:

- **EarningsWhispers.com / Estimize** — crowd-sourced buy-side/trader whisper numbers, distinct from sell-side consensus, have out-predicted Wall Street consensus in **67.4% of quarters since 2019**.
- **Options-implied move** (ATM straddle price ahead of the report) — the options market's implied move has historically **exceeded the actual move ~58% of the time**, i.e. usually somewhat overprices the reaction. This is the same "was the early read right or wrong" question as the pipeline spec's §6.2 reaction-accuracy scoring, just measured from the options side instead of price. The Polygon/Massive account already has an Options Basic ($0) tier with the reference endpoint confirmed working — whether it's rich enough for real straddle pricing hasn't been checked yet.

Sources: [Earnings Whispers](https://www.earningswhispers.com/), [Whisper Numbers vs Consensus](https://www.heygotrade.com/en/blog/whisper-numbers-vs-consensus-why-stocks-drop-on-beats/), [How Earnings Move Stocks — CME Group](https://www.cmelitegroup.com/knowledge-hub/how-earnings-move-stocks-expected-moves-iv-crush-and-overnight-gaps/)

---

## Part 8 — Deep-dive round 2026-08-16: does the edge still exist, and does it even apply to HuntHarvest's actual population?

This round was prompted by a direct question: dig into every available piece of material, not just the introductory framing. Several findings here revise assumptions already baked into the spec, not just add color — flagged explicitly below.

### 8.1 PEAD has weakened over time — and the reason matters

PEAD has declined significantly since it was first documented. Two competing explanations exist: the older consensus is increased arbitrage activity (more funds trading on it, competing away the edge); newer research argues the bigger driver is **declining signal informativeness** — current earnings surprises predict *future* earnings surprises less reliably than they used to, especially for smaller firms — and when that's accounted for, the "more arbitrage" story loses statistical significance. Despite the decline, PEAD "in one form or another" continues to exist after 50+ years of study and publicity, which is itself unusual for a published anomaly. **Implication**: don't assume HuntHarvest's model is tapping into the same-strength edge the original 1989 paper found — the honest baseline expectation should be weaker, and worth periodically re-checking as data accumulates rather than assumed constant. [Explaining the Decline of PEAD](https://experts.colorado.edu/display/pubid_511043), [PEAD: An Anomalous Anomaly](https://jkatz.caltech.edu/documents/28622/peads.pdf)

### 8.2 The population HuntHarvest actually studies behaves differently than classic PEAD — the most important finding of this round

Classic PEAD research studies the **full cross-section** of earnings surprises, including small/moderate ones, and finds *underreaction* — prices drift slowly in the surprise's direction for weeks. HuntHarvest studies a completely different population: **extreme (≥10%) reactions only**. The literature on that specific population says something different: **"Attention-driven reaction to extreme earnings surprises"** research finds that high attention to very extreme earnings news causes *faster* price incorporation — an **overreaction**, followed by a **partial reversal** — the opposite mechanism from classic PEAD's slow underreaction-drift. This resolves an apparent contradiction in the behavioral literature more broadly: overreaction dominates at short horizons (<1 month) and >1 year, underreaction/momentum dominates the 3-12 month middle horizon (Barberis-Shleifer-Vishny 1998 reconciles this: underreaction to an isolated news item, overreaction to a *pattern* of similar news — extreme single-day moves are the isolated-but-attention-grabbing case). **Implication for the spec**: HuntHarvest's own reversion-tracking window (days, not months) sits in the *overreaction/partial-reversal* zone, not the *classic-PEAD-drift* zone the original framing (Part 1 of this doc) leaned on. This doesn't invalidate the model, but it changes what result should actually be *expected* — betting on reversion for a ≥10% move has more direct literature support than the multi-month drift-continuation story does. [Attention-driven reaction to extreme earnings surprises](https://www.sciencedirect.com/science/article/abs/pii/S106297692300114X), [Evidence of Simultaneous Overreaction and Underreaction](https://shs.hal.science/halshs-03037432v1/document)

### 8.3 Real gap-size statistics — directly on-point, and pulling the same direction as 8.2's caveat

Concrete, sourced numbers on exactly HuntHarvest's population: **gaps larger than 10% fill (fully revert) only 60% of the time within 5 days**, versus 90%+ for small 3-5% gaps. Separately: **earnings-driven gaps hold their initial direction 71% of the time within 5 days**, versus only 43% for gaps driven by other pre-market news (earnings gaps are ~3x more likely to hold). Volume matters too: 3x+ relative volume with a clear catalyst tends to continue; low volume with no clear news tends to fade within the first hour. **Implication**: bigger moves (HuntHarvest's exact threshold population) are *less* likely to fully round-trip than small ones — some tension with 8.2's "overreaction then partial reversal" (partial ≠ full reversion), worth holding both findings together rather than picking one. Relative volume at the reaction itself looks like a real, cheap, directly-addable filter/feature — HuntHarvest already computes `volume_ratio`, just needs to be checked as a component of the shape-taxonomy read, not only the baseline feature set. [Stock Gaps Explained](https://pro.stockalarm.io/blog/stock-gap-up-gap-down-explained), [Gap and Go Strategy Guide](https://www.tradezella.com/blog/gap-and-go-strategy)

### 8.4 A real, named statistical test exists for the significance gap already flagged (Part 5)

The Boehmer-Musumeci-Poulsen (1991) standardized cross-sectional test — the actual answer to "how do you properly test whether an average CAR is significant," better than a naive t-test specifically because event days show *increased* return variance (known since Beaver 1968), which a plain t-test doesn't correct for and which inflates false positives. This is the concrete mechanism to fill the "no significance test reported" gap flagged in Part 5. [BMP test overview](https://www.eventstudytools.com/significance-tests)

### 8.5 Multi-factor models are a real, bounded upgrade path beyond the single-factor market model (§5 of the pipeline spec)

Adding Fama-French/Carhart factors to the market-model regression lifts explanatory fit (R²) into the 0.30-0.50 range for well-explained names — a tighter benchmark, more statistical power. Separately: naive buy-and-hold abnormal return (BHAR) calculations have known statistical problems (skewed distributions, misspecified t-stats, don't handle cross-sectional correlation well) — worth knowing as a trap to avoid if CAR/AR calculations ever get built out, in favor of the standard cumulative (not buy-and-hold) approach already scoped in §5. [Fama-French five-factor event study](https://link.springer.com/article/10.1186/s40854-023-00477-3)

### 8.6 Real, validated numbers for the phase-2 "big houses' predictions" idea

Peer-reviewed, not just vendor marketing: Estimize's crowdsourced consensus beats Wall Street's IBES consensus 58-65% of the time (63% in one 2012-2017 study), and *combining* IBES + Estimize beats IBES alone 60% of the time (Jame et al., *Journal of Accounting Research*). Real academic validation behind the source named in Part 7, not just the vendor's own claim.

### 8.7 Text/sentiment layer — real quantified performance, strengthens the Part 6 "phase 3+" idea

RavenPack's earnings-call-transcript sentiment signal alone posts an Information Ratio above 1.1 for mid/large caps and up to 2.0 for small caps (holding periods up to 1 week); combined with earnings-news sentiment, IR rises to 1.4 and 3.0 respectively over a 10-day hold. Real institutional clients, not a toy backtest. Doesn't change the phase-3 scoping call (still needs transcript ingestion infrastructure HuntHarvest doesn't have) but confirms the juice is real if ever pursued.

### 8.8 Market microstructure literature supports the BMO/AMC checkpoint design already locked (§4 of the pipeline spec)

Preopening call auctions play a specifically important role in price discovery after an overnight information gap (like an after-hours earnings release) — research on overnight earnings announcements finds after-hours release genuinely benefits from better information transmission across the long overnight window *and* the preopening auction, rather than being purely noisy. This is real, independent support for anchoring Track B's (AMC) qualifying checkpoint at the next day's regular-session open rather than the raw after-hours print — not just intuition, as it was framed when the two-track split was first designed. [Overnight earnings announcements and preopening price discovery](https://www.sciencedirect.com/science/article/abs/pii/S0922142524000124)

### 8.9 The sobering caveat — and why it matters less for HuntHarvest specifically than for an institutional strategy

Transaction costs eat **70-100% of the paper profits** of PEAD-style long-short strategies at institutional scale, and the effect is concentrated in exactly the small/illiquid/high-cost names that are hardest to trade in size — a real catch-22 for a scaled fund. **This matters less directly for HuntHarvest**: it's not running a scaled long-short book across hundreds of names, it's decision support for one person's discretionary trades on individual tickers they already chose to watch — a completely different cost structure than an institutional stat-arb desk trying to capture the same edge at size. Still worth carrying as an honest caveat: don't let a clean backtest number imply more tradeable edge than it actually represents once real slippage/spread on a specific ticker is considered. [Implications of Transaction Costs for PEAD](https://onlinelibrary.wiley.com/doi/abs/10.1111/j.1475-679X.2008.00290.x)

## Summary table

| Institutional/academic practice | HuntHarvest today | Gap severity |
|---|---|---|
| Abnormal return (market-model-adjusted) defines the event | Raw price % defines the event | Structural, not urgent |
| SUE — surprise normalized by ticker's own forecast-error volatility | Flat ≥10% threshold, same for every ticker; price-driven not EPS-driven | Structural, needs a consensus-estimate data source (phase 2) |
| Purged/embargoed cross-validation (no look-ahead) | Random `train_test_split`, no time-ordering, no purging | **Concrete, fixable now** — highest-value item in this doc |
| Factor-zoo-aware significance testing (t>3, FDR correction) | Economically-motivated feature set (good), but no CI/significance reported on accuracy | Reporting gap, not a design flaw |
| Gradient-boosted trees as a strong baseline | Already using `GradientBoostingClassifier`/`Regressor` | **Already aligned** |
| Text/sentiment + graph + transformer fusion (state of the art) | Price/technical features only | Real gap, appropriately phase 3+ |
| Analyst consensus / whisper numbers / options-implied move | Not yet built (parked as phase 2) | Named real sources now available: EarningsWhispers, options ATM straddle |
| Recognize PEAD has weakened, re-check periodically | Assumed a stable, textbook-strength edge | Reporting/expectations gap — track over time, don't assume constant |
| Extreme-surprise population overreacts-then-partially-reverses (short horizon) | Spec framed around classic multi-month PEAD drift | **Assumption correction** — HuntHarvest's actual population matches the overreaction literature better than the drift literature |
| Relative volume as a continuation/fade filter | `volume_ratio` exists as a baseline feature, not used as a shape-taxonomy signal | Cheap, concrete addition — data already collected |
| BMP standardized cross-sectional test for significance | No named statistical test, just "attach a t-stat" | Now has a specific, real method to implement |
| Multi-factor (Fama-French) abnormal-return model | Deferred as "phase 3" in §5 of the pipeline spec | Confirmed as a real, bounded upgrade path, not speculative |
| Transaction-cost reality check | Not addressed | Caveat carried, but structurally less damaging for single-ticker discretionary use than institutional scale |
