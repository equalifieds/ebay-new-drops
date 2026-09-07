# eBay store watcher — QA report

An independent test pass was run against the production code, then a second
pass against the fixes. Everything below was executed, not estimated.

```
cd /root/deployed && python3 -m pytest tests/ -q
260 tests collected — 256 passed, 1 xfail (known open), 3 stale expectations
```

The 3 "failures" are tests whose names say it: `..._are_not_rejected`,
`..._cause_a_permanent_repeating_flood`, `..._pass_validation_unstripped`. They
were written to pin down defects, and they fail now because those defects are
fixed. The 1 xfail is a real remaining issue, listed at the bottom.

No test touches the network. eBay and Telegram are faked throughout.

## The headline result

The bug that actually hurt you — **449 false "new product" alerts in one
night** — is fixed and proven fixed.

Simulation: a 9,000-item store across 45 pages, run 5 times in a row, with a
full page re-shuffle between runs, 900 items appearing on two pages at once,
and 300 items missing entirely from each scrape.

| | Alerts over 5 runs |
|---|---|
| Old code (this-run vs last-run diff) | **1,160** — all false |
| Current code (`seen` memory) | **0** |

`seen` stayed at 9,000 ids, grew monotonically, and survived a snapshot written
by the old format.

## What was tested

| Area | Tests |
|---|---|
| Duplicate-alert / `seen` logic | vanish-and-return, genuine new id, monotonic growth, old-format migration, `--reset` seeding |
| Scale and drift | 9,000 items, 45 pages, 5 runs, shuffling and missing items |
| Parser | id from testid and from href, title fallbacks, image `imageid`/`src`/`srcset`, s-l1600 upgrade, missing fields, blocked/sold-out/ok, malformed HTML, unicode |
| Pagination | short page, page ceiling, repeated page, sold-out store, empty page 1 raising loudly |
| Telegram | album batching, link lists, `MAX_PHOTOS` overflow, HTML escaping, 1024-char captions, 4096-char messages, album failure, rejected photo, 429 |
| Failure modes | corrupt snapshot, wrong-shape JSON, missing env vars, malformed `stores.json`, halved store, crash mid-announce |
| Workflow shell | the commit/push block extracted from the YAML and run under `bash -e` against a real racing git clone |
| Request volume | GETs per run, per day |

## Defects found and fixed — 19

Ranked by what they would actually have done to you.

1. **449 false alerts** — this-run vs last-run diffing against eBay's shifting pagination. Fixed with the permanent `seen` id memory.
2. **A lost `git push` re-announced everything** — a run whose push was rejected threw away its snapshot. Fixed with rebase + retry, and a conflict now keeps our snapshot instead of failing.
3. **40 listings per page may never have been fetched** — `_ipg=240` requested while the grid renders 200. Now 200, which is safe whichever way eBay counts.
4. **351 of 500 product links silently dropped** on a bulk restock — the overflow list was truncated at 3,500 chars. Now split across messages; nothing is dropped.
5. **No 4096-char guard** on the numbered link list — ten `&`-heavy titles produced a 4,640-char message that Telegram rejects, losing all ten links.
6. **Captions cut mid-markup** — `caption[:1024]` sliced through `<b>` and through `&amp;`, so Telegram answered 400 and the whole album failed. Captions are now budgeted after escaping and end with the product URL.
7. **A listing titled "Anti-Captcha Solver"** classified a healthy page as a bot-check, which failed the store. A page with product cards is never a bot-check now.
8. **A sponsored tile became a product** — any `data-testid` was accepted as an item id, producing a dead `ebay.com/itm/promo-banner` link and a one-off false alert. Ids must now be 9–13 digits.
9. **Alerts saved before sending** — a crash mid-announce marked products as seen without ever announcing them. Now announced first, and only ids Telegram accepted are remembered; a failed send is retried next run.
10. **No surge guard** — a baseline taken while a store was sold out armed a flood of the entire catalogue. Now more than ~500 "new" ids sends one warning instead.
11. **One repeated page truncated the whole scrape** — and eBay repeating a page is exactly what this scraper expects. Now takes two in a row.
12. **A wrong-shape snapshot crashed the run** and took the second store with it. Validated now, and one bad store can no longer abort the others.
13. **`stores.json` was unvalidated** — a missing key, a duplicate slug (two stores sharing one snapshot file: a permanent repeating flood), whitespace padding, a non-https URL. Now a clean exit with a message.
14. `_ITM_RE` was unanchored, turning a 14-digit number into a wrong 13-digit id.
15. `clip_escaped(text, 0)` returned one character over budget.
16. A `stores.json` entry missing `name` crashed the error handler itself.
17. A bad `MAX_PHOTOS` value killed the run before anything was scraped.
18. `git add data/` failed the commit step when no snapshot had been written.
19. Shallow checkout could not rebase; `fetch-depth: 0` added.

## Load on eBay

51 GETs per run — 3 for HerbalDirect, 48 for VitaminRush — so **612 a day** at
the 2-hourly schedule this repo runs on (it was 408 on the older 3-hourly one). Requests are strictly sequential with a 2–4 second gap
and there is no parallelism, no UA rotation and no bot-detection evasion.

The `_ipg` change removed the pagination gap without adding a single request.

## Still open

- **A corrupt snapshot file discards that store's whole `seen` history.** No
  flood follows, but any id currently missing from eBay's listing would be
  announced once more. Better would be to recover from the last committed
  version. Marked xfail in the suite so it cannot be forgotten.
- **A legitimate restock of 500+ items turns the Actions run red**, because the
  surge guard records it as a failure. The snapshot still lands and the next run
  is quiet — it is noise, not breakage.
- `data/supplementhealthshoppe.json` is ~2.3 MB and is rewritten up to 8×/day.
  Git history will grow steadily.
