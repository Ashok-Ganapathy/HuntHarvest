# HUNTHARVEST — Auto-loaded session context

This file is auto-read at the start of every session in this folder. **All critical state is embedded here** — greet immediately without waiting for folder access or reading other files.

---

## 🟢 CURRENT STATE — Greet from this immediately

**Session 1 (Aug 15–16, 2026) — v1 discovered/archived/torn down, v2 built end-to-end, verified, and LIVE at huntorharvest.com.** `~/HuntHarvest` turned out to already have a working (but buggy) droplet-deployed v1 site. Full audit found real structural bugs. User chose to archive v1 completely and rebuild — extended design conversation locked a v2 spec (see PROJECT_STATE.md), then explicit "build" go-ahead was given.
- ✅ v1 fully archived (`~/HuntHarvest_v1_archive`) and torn down. New droplet (142.93.196.178, 2GB RAM, replaced the original which died mid-MySQL-install) with MySQL (8 tables) + Python stack.
- ✅ **Backfill complete**: 37,773 events, 2,696 tickers, 2022-01-01→2026-08-14. Two real data-integrity bugs found and fixed via smoke-testing/QC: point-in-time market cap qualification, and Polygon's ticker-symbol reuse (FB→META-style reassignments splicing unrelated companies' price histories together — caught via a fake +1394% "META" event, now guarded by a `ticker_valid_from()` check).
- ✅ **Models trained** (class-balanced, honest ~63-64% accuracy with balanced recall — an earlier unweighted pass showed misleadingly high 81% accuracy that was just always guessing the majority class) and **QC passed** (all 8 structural checks green, including the SEZL proof case that was missing in v1).
- ✅ **Frontend built**: sidebar nav (Earnings Watch/System), full-width sortable 28-column table, expandable per-ticker history, admin config panel. Iterated live with the user through several rounds of column additions/reordering.
- ✅ **Live quotes** table + script built (current price, day/week/month change, relative volume) — populated once, not yet scheduled to refresh periodically.
- ✅ **LIVE**: real `huntharvest` systemd service running, DNS cut over in Cloudflare, Let's Encrypt SSL issued (expires 2026-11-14, auto-renews), `huntharvest-quotes.timer` (every 30 min) and `huntharvest-train.timer` (monthly) both enabled. End-to-end verified over `https://huntorharvest.com`.

**What's next:** The full daily pipeline (Phases 1, 2, and 3) is built, deployed, and verified 2026-08-16 — including running the Phase 3 historical backfill for real across all 37,786 existing events. A real bug was caught and fixed during that verification (post-settlement durability was defaulting to a false "held" for reverted events due to a `price_path` data-availability gap in the original historical backfill, not a genuine result) — fixed and rerun. One known real limitation, not hidden: `dead_cat` shape classification can't apply to historical reverted events for the same reason, only to new events this pipeline creates going forward; fixing the old data needs new Polygon calls, flagged as a follow-up, not done. The full ~2,696-ticker α/β backfill also wasn't run (real API cost) — only proven on 3 real tickers. **Monday 2026-08-17 remains the first real end-to-end trading-day test** — HTHT is genuinely on the watchlist reporting BMO that morning. Lower-priority cleanup still pending: destroy the dead old droplet, `git init` v2 code, rename the droplet in DO's dashboard. See TASKS.md.

**Active state:**
- Live site: **https://huntorharvest.com** (and www) — real data, real predictions, real UI
- Droplet: `142.93.196.178` (2GB RAM) — SSH via `~/.ssh/huntharvest_id_ed25519`. DO dashboard still shows default name `ubuntu-s-1vcpu-2gb-nyc1`, not renamed (cosmetic).
- Old droplet `165.227.88.24` is dead, not yet destroyed in DO dashboard — nothing on it is unique (fully archived locally).
- Login: `ashok` (admin) / `train` (user) — see SECRETS.md for passwords.
- Local folder: `~/HuntHarvest` mirrors what's deployed to `/var/www/huntorharvest` on the new droplet.

---

## 🔁 UPDATE THIS BLOCK after every significant task
_Keep it ≤10 lines. Update `TASKS.md` for anything actionable. Update `PROJECT_STATE.md` for the narrative/full-detail log._

---

**On start:**
1. Greet with a 2–3 line recap from the block above — do NOT ask user to re-explain.
2. **Immediately read `SECRETS.md`** — hold all credentials in context for the session.
3. Read `TASKS.md` for the current actionable/pending list.
4. Read `PROJECT_STATE.md` for the full locked v2 spec and v1 archive details.

## 🔐 CREDENTIALS RULE — NEVER VIOLATE
**NEVER ask the user for a credential that's already in `SECRETS.md`.** Read it at session start and use it silently. Credentials still genuinely missing (not yet obtained) are tracked in TASKS.md, not re-asked for from scratch each session.

## ⚠️ USER PREFERENCE — NEVER VIOLATE
**Never write/edit code or run anything build-flavored without an explicit go-ahead in that turn.** User stated this directly (2026-08-15), citing the same rule learned the hard way on AGSTOX — a clarifying-question answer or an approved design is NOT itself authorization to build; wait for an explicit build-flavored word (build/implement/go/do it) first.

---

## 🔧 Access & Tool Rules

### Git
- GitHub repo (`github.com/Ashok-Ganapathy/HuntHarvest`) currently holds only the **archived v1 history**, tagged `v1-legacy`. v2 code exists locally and on the droplet but has **not been committed to git yet** — needs a fresh `git init` when that's wanted. Same sandbox caution as AGSTOX applies: avoid running `git` commands from this sandbox against the Mac's local filesystem (lock-file risk) — prefer the user run it, or a `.command` file, once that's set up for this project.

### SSH / Droplet (142.93.196.178 — the CURRENT one, not 165.227.88.24)
- `ssh -i ~/.ssh/huntharvest_id_ed25519 root@142.93.196.178` — confirmed working.
- Password auth over network SSH is disabled on this image (rejects before prompting) — only works via the DigitalOcean web console's direct login if the key is ever lost.
- Live app directory: `/var/www/huntorharvest` (same path convention as v1, fresh code).

### Deploy (v2 — different from v1's pattern)
- `huntharvest.service` (systemd) + `.env` (secrets, gitignored, not committed) are deployed to the droplet but the service is **not started yet** — waiting on trained models.
- `huntorharvest.com.conf` (nginx) is written but **not enabled yet** — waiting on DNS cutover.
- Deploy method going forward: edit locally in `~/HuntHarvest`, `scp` to `/var/www/huntorharvest` on the droplet, restart the systemd service — not yet formalized into a `.command`/script like AGSTOX's.

---

**Repo**: `~/HuntHarvest`. **Backend**: `app.py` (FastAPI/uvicorn, MySQL via pymysql). **DB**: MySQL `huntharvest` on the droplet (not SQLite, not local).

**Pending/actionable tasks**: see `TASKS.md`. **Full locked v2 spec, v1 archive/bug writeup, schema**: see `PROJECT_STATE.md`.
