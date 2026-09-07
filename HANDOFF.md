# eBay store watcher — handoff

Paste this file into a new Claude session to bring it up to speed.

## What it is

A GitHub Actions job that watches two eBay storefronts and pushes **newly-listed
products only** to Telegram, as photo albums with clickable links.

- Repo: `equalifieds/ebay-new-drops` (**public**)
- Telegram bot: `@EbayNewDropsBot`
- Chat id: owner's DM — lives only in the `TELEGRAM_CHAT_ID` repo secret
- Schedule: every 2 hours — cron `45 */2 * * *` (UTC), i.e. 06:15, 08:15, 10:15, … IST.
  Two things are deliberate about that cron:
  - **`*/2` is affordable because the repo is public.** Public repos get
    unlimited Actions minutes. On a private repo 2-hourly is ~1,900 of the 2,000
    free monthly minutes (95%) and one slow month stops the watcher silently —
    if this repo is ever made private, move the cron back to `45 */3 * * *`.
  - **`:45` keeps it clear of the older `ebay-store-watcher` repo**, which runs
    on `:30`. The 15-minute offset means the two watchers never hit eBay in the
    same minute.

This is a separate deployment from `ebay-store-watcher`: its own bot, its own
schedule, its own `data/` memory. The two share no state.

## Files

| File | Role |
|---|---|
| `scrape.py` | Scrapes each storefront, diffs against `data/<slug>.json`, sends alerts |
| `parser.py` | Pure HTML → product parsing, no network (unit-testable) |
| `stores.json` | The storefronts to watch |
| `.github/workflows/watch.yml` | Schedule, deps, run, commit snapshots back |
| `data/<slug>.json` | Per-store snapshot: current products **and** `seen` (every id ever observed) |
| `tests/` | 260-test offline suite (`python3 -m pytest tests/ -q`) |

## Secrets (set these in Settings → Secrets and variables → Actions)

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

## How the diff works — read this before changing anything

The snapshot carries a **`seen`** list of every item id ever observed at that
store. An alert fires only for an id that is not in `seen`, and `seen` only ever
grows.

This is not decoration. eBay shuffles listings between pages while you
paginate, so a product routinely drops out of one scrape and reappears in the
next. Diffing this run against only the previous run produced **449 false
alerts in one night** in production. With `seen`, a 9,000-item store with a
full page re-shuffle and 300 items missing per run produces **0** false alerts
over 5 consecutive runs — that is a test in `tests/test_scale_drift.py`.

If you ever need to stop a flood: run the workflow with the **`reset`** input
ticked. That seeds `seen` with the union of everything known plus everything
just read, and sends no product alerts.

## State as of this handoff

**Deployed and running:** an earlier version with the `seen` fix, album
sending, `MAX_PHOTOS: "100"`, the schedule, and a `git pull --rebase`
retry on the commit step. It is working — the last runs were clean.

**In this folder but NOT yet deployed:** a further round of fixes from an
independent QA pass (13 defects found, all fixed, 256 tests passing). To deploy,
replace `scrape.py`, `parser.py` and `.github/workflows/watch.yml` in the repo
with the copies here. Notable changes:

- `ITEMS_PER_PAGE` 240 → 200. Asking for 240 while the grid renders 200 may
  stride pagination past 40 listings a page — those would never be announced.
- Item ids must match `^\d{9,13}$`, otherwise fall back to the `/itm/` href.
  A sponsored tile used to become a dead product URL and a false alert.
- A page is only treated as a bot-check if it carries **no** product cards. A
  real listing titled "Anti-Captcha Solver CD" used to shut the watcher down.
- Long alert lists are split across messages instead of truncated — 351 of 500
  product links used to be silently dropped on a bulk restock.
- Captions are budgeted **after** HTML escaping, so they never cut mid-tag or
  mid-`&entity;` (which Telegram rejects with a 400).
- Alerts are sent **before** the snapshot is saved, and only ids Telegram
  accepted are remembered — a failed send is retried next run instead of lost.
- Surge guard: more than `min(max(200, len(seen)//4), 500)` "new" ids sends one
  warning instead of hundreds of alerts. That is a markup change, not a restock.
- One bad store can no longer abort the whole run; `stores.json` is validated
  (missing/blank/duplicate slug, non-https url) with a clean exit 2.
- Workflow: `fetch-depth: 0`, `mkdir -p data`, and a rebase conflict is
  resolved by keeping our snapshot instead of failing the push.

## Known, still open

- A **corrupt** snapshot JSON discards the whole `seen` history for that store,
  so every currently-off-grid id is announced once. No flood, but avoidable —
  recover from the last committed version instead. (`tests/` marks this xfail.)
- A legitimate restock of 500+ items makes the Actions run go **red**, because
  the surge guard records a failure. The snapshot still lands and the next run
  is quiet.
- `data/supplementhealthshoppe.json` is ~2.3 MB and is rewritten up to 12×/day.
  Git history will grow. Consider splitting `seen` into its own file.

## Load on eBay

51 GETs per run (3 for HerbalDirect, 48 for VitaminRush) → **612/day** at 12
runs/day. (It was 408/day on the old 3-hourly schedule; 2-hourly raised eBay
load ~50%. Do not raise it further.)
Requests are strictly sequential with a 2–4 s gap. The owner has been
CAPTCHA'd before with a cruder scraper, so **do not** add parallelism, do not
lower the delay, and do not add bot-detection evasion. If eBay starts refusing,
back off — do not work around it.

## Adding a store

Append to `stores.json`:

```json
{ "slug": "somestore", "name": "Some Store", "url": "https://www.ebay.com/str/somestore" }
```

`slug` is the snapshot filename: keep it unique, path-safe and stable.

## Running locally

```bash
pip install -r requirements.txt
python scrape.py --dry-run   # scrape + diff, send nothing
python scrape.py --reset     # rebuild the baseline, no alerts
python3 -m pytest tests/ -q  # 260 tests, fully offline
```
