# HUNTHARVEST — Auto-loaded session context

This file is auto-read at the start of every session in this folder. **All critical state is embedded here** — greet immediately without waiting for folder access or reading other files.

---

## 🟢 CURRENT STATE — Greet from this immediately

**Session 1 (Aug 15–16, 2026) — v1 discovered/archived/rebuilt as v2, full daily pipeline (Phases 1-3) built and live-verified, git + droplets cleaned up. LIVE and self-sustaining at huntorharvest.com.**
- ✅ v2 core: backfill (37,773 events/2,696 tickers), models (63-64% accuracy), QC, frontend, go-live infra — all from Session 1's first half (full detail: PROJECT_STATE.md).
- ✅ **Daily pipeline (Phases 1-3) live-verified against real data**: evening watchlist scan → live reaction polling/checkpoint-lock → settle into permanent history → AR/CAR + shape-taxonomy analysis. Real bugs caught+fixed during verification, not shipped silently (see PROJECT_STATE.md for specifics). Both deferred backfills (α/β, `price_path` extension) done. Edge-strength tracking verified (73.6% recent vs 73.1% prior win rate → holding).
- ✅ Git properly scoped to `~/HuntHarvest` (a stray home-level `.git` was found+fixed), v1 preserved via a real `v1-legacy` tag, v2 live on `main`. Droplet renamed + old one destroyed (Ashok, confirmed via DO API).
- ✅ **Checkpoint-lock push notifications working** — SMS turned out carrier-blocked (real A2P 10DLC compliance issue, not fixable via code); reused AGSTOX's already-working push infra instead via a new small `/api/internal/push` relay added to `agstox_exchange.py`. Two real end-to-end tests confirmed delivered to Ashok's phone.
- **Monday 2026-08-17 = first real trading-day test** (HTHT reports BMO) — no other open items from this session.

**Active state:**
- Live site: **https://huntorharvest.com** (and www)
- Droplet: `142.93.196.178` ("HuntHarvest" in DO), SSH via `~/.ssh/huntharvest_id_ed25519`
- Login: `ashok` (admin) / `train` (user) — see SECRETS.md
- Local folder: `~/HuntHarvest`, git-tracked, mirrors `/var/www/huntorharvest` on the droplet

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
