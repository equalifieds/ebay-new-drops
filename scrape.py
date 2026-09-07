#!/usr/bin/env python3
"""
eBay store watcher
------------------
Scrapes every page of one or more eBay storefronts, records each product's
name + image, compares against the previous run, and pushes new products to
Telegram.

Two-tier fetching:
  1. Plain HTTP (fast, ~2s/page). The storefront is server-side rendered, so
     no JavaScript is needed for the happy path.
  2. If eBay returns a bot-check or a non-200, the run automatically escalates
     to a real headless Chromium via Playwright for the rest of the session.

Run:  python scrape.py             normal run
      python scrape.py --dry-run   scrape + diff, send nothing
      python scrape.py --reset     rebuild baseline, no new-product alerts
      python scrape.py --force-browser   skip tier 1, go straight to Playwright

Env:  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID   required (unless --dry-run)
      MAX_PHOTOS       default 20
      CHROMIUM_PATH    optional path to an existing Chromium binary
"""

from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from parser import parse_products, detect_state

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
STORES_FILE = ROOT / "stores.json"

ITEMS_PER_PAGE = 200          # what the grid actually renders; asking for more
                              # strides pagination past listings we never fetch
FULL_PAGE_THRESHOLD = 200     # fewer than this means we reached the last page
MAX_PAGES = 80                # hard stop so a pagination bug can't loop forever
RETRIES_PER_PAGE = 3
SURGE_LIMIT = 200             # 'new' products before we warn instead of alerting
HTTP_TIMEOUT = 45

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

BROWSER_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
              "image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Cache-Control": "no-cache",
}


class ScrapeError(RuntimeError):
    """A store could not be scraped safely (blocked, timeouts, ...)."""


class BlockedError(ScrapeError):
    """eBay served a bot-check instead of the storefront."""


# --------------------------------------------------------------------------- #
# fetching
# --------------------------------------------------------------------------- #

def store_page_url(store_url: str, page_no: int) -> str:
    return f"{store_url}?_ipg={ITEMS_PER_PAGE}&_pgn={page_no}"


class HttpFetcher:
    """Tier 1: plain HTTP. The storefront is server-rendered, so this is enough."""

    name = "http"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(BROWSER_HEADERS)

    def get(self, url: str) -> str:
        r = self.session.get(url, timeout=HTTP_TIMEOUT)
        if r.status_code in (403, 429, 503):
            raise BlockedError(f"HTTP {r.status_code}")
        r.raise_for_status()
        return r.text

    def close(self):
        self.session.close()


class BrowserFetcher:
    """Tier 2: a real headless Chromium. Started lazily, only if tier 1 fails."""

    name = "browser"

    def __init__(self):
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        launch = {
            "headless": True,
            "args": ["--disable-blink-features=AutomationControlled",
                     "--no-sandbox", "--disable-dev-shm-usage"],
        }
        if os.environ.get("CHROMIUM_PATH"):
            launch["executable_path"] = os.environ["CHROMIUM_PATH"]
        try:
            self._browser = self._pw.chromium.launch(**launch)
        except Exception:                                   # noqa: BLE001
            # Chromium is installed lazily. The plain-HTTP path never needs it,
            # and installing it on every scheduled run wastes ~2 minutes.
            print("  installing chromium (first time it is needed)...")
            subprocess.run(
                [sys.executable, "-m", "playwright", "install", "--with-deps",
                 "chromium"],
                check=True,
            )
            self._browser = self._pw.chromium.launch(**launch)
        self._ctx = self._browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1440, "height": 900},
            locale="en-US",
            timezone_id="America/New_York",
        )
        self._ctx.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
        )
        self._page = self._ctx.new_page()

    def get(self, url: str) -> str:
        self._page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        try:
            self._page.wait_for_selector("article.str-item-card", timeout=15_000)
        except Exception:                                   # noqa: BLE001
            pass                                            # sold-out stores have no grid
        return self._page.content()

    def close(self):
        for closer in (self._ctx.close, self._browser.close, self._pw.stop):
            try:
                closer()
            except Exception:                               # noqa: BLE001
                pass


class TieredFetcher:
    """Tries HTTP, permanently escalates to the browser once HTTP gets blocked."""

    def __init__(self, force_browser: bool = False):
        self._http = None if force_browser else HttpFetcher()
        self._browser = None
        self.escalated = force_browser

    def _get_browser(self) -> BrowserFetcher:
        if self._browser is None:
            print("  ** escalating to headless Chromium **")
            self._browser = BrowserFetcher()
            self.escalated = True
        return self._browser

    def fetch(self, url: str) -> tuple[str, str]:
        """Return (html, tier_used), retrying and escalating as needed."""
        last: Exception | None = None

        for attempt in range(1, RETRIES_PER_PAGE + 1):
            fetcher = self._get_browser() if (self.escalated or self._http is None) \
                else self._http
            try:
                html = fetcher.get(url)
                if detect_state(html) == "blocked":
                    raise BlockedError("bot-check page served")
                return html, fetcher.name
            except BlockedError as exc:
                last = exc
                print(f"      blocked via {fetcher.name} ({exc})")
                if fetcher.name == "http":
                    self.escalated = True          # switch tiers and retry at once
                    continue
            except Exception as exc:                        # noqa: BLE001
                last = exc
                print(f"      {type(exc).__name__}: {exc}")

            if attempt < RETRIES_PER_PAGE:
                backoff = 5 * attempt + random.uniform(0, 3)
                print(f"      retry {attempt}/{RETRIES_PER_PAGE} in {backoff:.1f}s")
                time.sleep(backoff)

        raise ScrapeError(f"failed after {RETRIES_PER_PAGE} attempts: {last}")

    def close(self):
        if self._http:
            self._http.close()
        if self._browser:
            self._browser.close()


# --------------------------------------------------------------------------- #
# scraping
# --------------------------------------------------------------------------- #

def scrape_store(fetcher: TieredFetcher, store: dict) -> list[dict]:
    """Walk every page of a storefront and return a de-duplicated product list."""
    print(f"  scraping {store['name']} ({store['url']})")
    products: dict[str, dict] = {}
    page_no = 1
    repeats = 0

    while page_no <= MAX_PAGES:
        html, tier = fetcher.fetch(store_page_url(store["url"], page_no))
        state = detect_state(html)
        items = parse_products(html)
        print(f"    page {page_no}: {len(items)} items ({state}, via {tier})")

        if state == "sold_out" and not items:
            print("    store reports it is sold out — 0 active listings")
            break

        if not items:
            if page_no == 1:
                # A normal-looking page with no cards means eBay changed the
                # markup. Fail loudly rather than silently recording zero.
                raise ScrapeError(
                    "page 1 returned no product cards and the store is not marked "
                    "sold out — eBay's storefront markup has probably changed, so "
                    "the selectors in parser.py need updating"
                )
            break

        before = len(products)
        for item in items:
            products.setdefault(item["id"], item)
        if len(products) == before:
            # eBay re-serves a page now and then while it shuffles listings
            # between pages. One repeat is noise; two in a row means we really
            # have run out of catalogue.
            repeats += 1
            print(f"    no new ids on this page ({repeats} in a row)")
            if repeats >= 2:
                break
        else:
            repeats = 0

        if len(items) < FULL_PAGE_THRESHOLD:
            break

        page_no += 1
        time.sleep(random.uniform(2.0, 4.0))   # be a polite guest

    print(f"  -> {len(products)} unique products")
    return sorted(products.values(), key=lambda p: p["id"])


# --------------------------------------------------------------------------- #
# snapshots
# --------------------------------------------------------------------------- #

def snapshot_path(slug: str) -> Path:
    return DATA_DIR / f"{slug}.json"


def load_snapshot(slug: str) -> dict | None:
    path = snapshot_path(slug)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        print(f"  !! {path.name} is unreadable ({exc}), treating as first run")
        return None
    if not isinstance(data, dict) or not isinstance(data.get("products", []), list):
        print(f"  !! {path.name} is not a snapshot, treating as first run")
        return None
    data["products"] = [p for p in (data.get("products") or [])
                        if isinstance(p, dict) and p.get("id")]
    if not isinstance(data.get("seen"), list):
        data.pop("seen", None)
    return data


def save_snapshot(slug: str, store: dict, products: list[dict],
                  seen: set[str] | None = None) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    snapshot_path(slug).write_text(json.dumps({
        "store": store["name"],
        "url": store["url"],
        "scraped_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "count": len(products),
        # Every id ever seen at this store. eBay shuffles listings between
        # pages, so an item can drop out of one scrape and come back on the
        # next; without this it would be announced as new all over again.
        "seen": sorted(seen if seen is not None
                       else {p["id"] for p in products}),
        "products": products,
    }, indent=2, ensure_ascii=False), encoding="utf-8")


# --------------------------------------------------------------------------- #
# telegram
# --------------------------------------------------------------------------- #

class Telegram:
    def __init__(self, token: str | None, chat_id: str | None, enabled: bool = True):
        self.token, self.chat_id = token, chat_id
        self.enabled = enabled and bool(token and chat_id)
        self.api = f"https://api.telegram.org/bot{token}" if token else None

    def _call(self, method: str, payload: dict) -> bool:
        if not self.enabled:
            print(f"  [dry-run] {method}: {payload.get('caption') or payload.get('text')}")
            return True
        for attempt in range(3):
            try:
                r = requests.post(f"{self.api}/{method}", data=payload, timeout=45)
                if r.status_code == 429:
                    wait = r.json().get("parameters", {}).get("retry_after", 5)
                    print(f"  telegram rate-limited, sleeping {wait}s")
                    time.sleep(wait + 1)
                    continue
                if r.ok and r.json().get("ok"):
                    return True
                print(f"  telegram {method} failed: {r.status_code} {r.text[:200]}")
                return False
            except requests.RequestException as exc:
                print(f"  telegram network error ({exc}); retry {attempt + 1}/3")
                time.sleep(3 * (attempt + 1))
        return False

    def text(self, message: str) -> bool:
        return self._call("sendMessage", {
            "chat_id": self.chat_id, "text": message[:TEXT_LIMIT],
            "parse_mode": "HTML", "disable_web_page_preview": True,
        })

    def photo(self, image_url: str, caption: str) -> bool:
        ok = image_url and self._call("sendPhoto", {
            "chat_id": self.chat_id, "photo": image_url,
            "caption": caption[:CAPTION_LIMIT], "parse_mode": "HTML",
        })
        return ok or self.text(caption)   # fall back to text if the image won't load

    def album(self, items: list[dict], captions: list[str]) -> bool:
        """Up to 10 photos in a single API call (Telegram's sendMediaGroup)."""
        media = [
            {"type": "photo", "media": item["image"],
             "caption": caption[:CAPTION_LIMIT], "parse_mode": "HTML"}
            for item, caption in zip(items, captions)
        ]
        return self._call("sendMediaGroup", {
            "chat_id": self.chat_id, "media": json.dumps(media),
        })


TEXT_LIMIT = 4096
CAPTION_LIMIT = 1024


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def clip(s: str, limit: int) -> str:
    """Trim the plain text, never the markup we wrap around it."""
    return s if len(s) <= limit else s[:max(0, limit - 1)].rstrip() + "\u2026"


def send_lines(tg: "Telegram", header: str, lines: list[str]) -> bool:
    """Send as many messages as it takes; never drop a line to fit a limit."""
    ok = True
    chunk = [header] if header else []
    size = len(header) + 1 if header else 0
    for line in lines:
        if chunk and size + len(line) + 1 > TEXT_LIMIT - 64:
            ok = tg.text("\n".join(chunk)) and ok
            time.sleep(1.0)          # Telegram allows ~20 messages a minute
            chunk, size = [], 0
        chunk.append(line)
        size += len(line) + 1
    if chunk:
        ok = tg.text("\n".join(chunk)) and ok
    return ok


ALBUM_SIZE = 10                # Telegram's maximum photos per sendMediaGroup


def clip_escaped(text: str, limit: int) -> str:
    """Trim already-escaped text without ever cutting inside an &entity;."""
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    cut = text[:limit - 1]
    amp = cut.rfind("&")
    if amp != -1 and ";" not in cut[amp:]:
        cut = cut[:amp]
    return cut.rstrip() + "\u2026"


def _caption(item: dict, store: dict) -> str:
    """
    Build a caption that already fits Telegram's limit, so nothing downstream
    has to slice it. Escaping expands "&" into five characters, so the budget
    has to be counted after escaping, not before.
    """
    url = item["url"]
    price = esc(clip(item.get("price", ""), 40))
    shop = esc(clip(store["name"], 80))
    tail = (f"\n{price}" if price else "") + f"\n{shop}\n{url}"
    if len(tail) > CAPTION_LIMIT - 64:          # a pathological price or shop name
        tail = f"\n{url}"
    budget = max(0, CAPTION_LIMIT - len(tail) - len("<b></b>"))
    return f"<b>{clip_escaped(esc(item['title']), budget)}</b>{tail}"


def _link_line(index: int, item: dict) -> str:
    price = f" - {esc(clip(item['price'], 40))}" if item.get("price") else ""
    return (f'{index}. <a href="{item["url"]}">'
            f'{esc(clip(item["title"], 120))}</a>{price}')


def announce_new(tg: Telegram, store: dict, new_items: list[dict],
                 max_photos: int) -> list[str]:
    """
    Send new products as albums: ten photos per API call instead of ten separate
    calls, so even a bulk upload stays well inside Telegram's limits.

    Every album is followed by a plain-text list of the same items, because
    Telegram hides per-photo captions behind a tap and the links should be one
    tap away, not two. Nothing is ever dropped to fit a message limit - long
    lists are split across messages instead.

    Returns the ids Telegram would not accept, so the caller can leave them
    unremembered and try again on the next run.
    """
    failed: list[str] = []
    plural = "s" if len(new_items) != 1 else ""
    tg.text(f"\U0001F195 <b>{len(new_items)} new product{plural}</b> "
            f"at {esc(clip(store['name'], 80))}")

    with_photo = [p for p in new_items if p.get("image")]
    shown, rest = with_photo[:max_photos], with_photo[max_photos:]
    rest = rest + [p for p in new_items if not p.get("image")]

    for start in range(0, len(shown), ALBUM_SIZE):
        batch = shown[start:start + ALBUM_SIZE]
        captions = [_caption(p, store) for p in batch]

        if len(batch) == 1 or not tg.album(batch, captions):
            for item, caption in zip(batch, captions):
                if not tg.photo(item["image"], caption):
                    failed.append(item["id"])
                time.sleep(1.5)

        # The links go out either way - an album hides its captions behind a tap.
        if not send_lines(tg, "", [_link_line(start + n + 1, item)
                                   for n, item in enumerate(batch)]):
            failed.extend(item["id"] for item in batch)
        time.sleep(2.0)

    if rest:
        if not send_lines(tg, f"...and {len(rest)} more:",
                          [_link_line(n + 1, item)
                           for n, item in enumerate(rest)]):
            failed.extend(item["id"] for item in rest)

    # An id can be refused by both the photo and the link list; count it once.
    return list(dict.fromkeys(failed))


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--force-browser", action="store_true")
    args = ap.parse_args()

    try:
        max_photos = int(os.environ.get("MAX_PHOTOS", "100"))
    except ValueError:
        print("  MAX_PHOTOS is not a number; falling back to 100")
        max_photos = 100
    tg = Telegram(os.environ.get("TELEGRAM_BOT_TOKEN"),
                  os.environ.get("TELEGRAM_CHAT_ID"),
                  enabled=not args.dry_run)
    if not args.dry_run and not tg.enabled:
        print("ERROR: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID are not set.", file=sys.stderr)
        return 2

    try:
        stores = json.loads(STORES_FILE.read_text(encoding="utf-8"))
        if not isinstance(stores, list):
            raise ValueError("stores.json must be a list of stores")
        slugs = set()
        for entry in stores:
            if not isinstance(entry, dict):
                raise ValueError(f"not a store object: {entry!r}")
            for key in ("slug", "name", "url"):
                if not isinstance(entry.get(key), str) or not entry[key].strip():
                    raise ValueError(f"store is missing a usable {key!r}: {entry!r}")
                entry[key] = entry[key].strip()
            if "/" in entry["slug"]:
                raise ValueError(f"slug must be path-safe: {entry['slug']!r}")
            # Two stores sharing a slug share a snapshot file, so each run
            # overwrites the other's memory and both flood, every run, forever.
            if entry["slug"] in slugs:
                raise ValueError(f"duplicate slug: {entry['slug']!r}")
            slugs.add(entry["slug"])
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: stores.json is not usable: {exc}", file=sys.stderr)
        return 2
    fetcher = TieredFetcher(force_browser=args.force_browser)
    failures: list[str] = []
    total_new = 0

    try:
        for store in stores:
            # A label worked out before anything can throw, so the handlers
            # below can name the store even when the store entry is the problem.
            label = ((store.get("name") or store.get("slug") or repr(store))
                     if isinstance(store, dict) else repr(store))
            # Anything at all going wrong with one store must leave the
            # others alone - a single bad snapshot used to abort the run.
            try:
                slug = store["slug"]
                print(f"\n=== {label} ===")
                products = scrape_store(fetcher, store)

                previous = load_snapshot(slug)

                if previous is None or args.reset:
                    # Seed the memory with everything we already knew as well as
                    # everything we just read, so a reset is the way to stop a
                    # flood of false alerts rather than a cause of one.
                    seeded = {p["id"] for p in products}
                    if previous:
                        seeded |= {p["id"] for p in previous.get("products", [])}
                        seeded |= set(previous.get("seen") or ())
                    save_snapshot(slug, store, products, seeded)
                    print(f"  {'reset' if args.reset else 'first run'} — baseline of "
                          f"{len(products)} products saved, no alerts sent")
                    tg.text(f"📋 Baseline saved for <b>{esc(store['name'])}</b>: "
                            f"{len(products)} products now being tracked.")
                    continue

                old = {p["id"]: p for p in previous.get("products", [])}
                seen = set(previous.get("seen") or old.keys())

                # Guard: a half-scraped run must never wipe the baseline, or the next
                # run would report the entire catalogue as "new".
                if len(old) >= 20 and len(products) < len(old) * 0.5:
                    print(f"  !! suspicious drop ({len(old)} -> {len(products)}), "
                          f"snapshot not updated")
                    tg.text(f"⚠️ <b>{esc(store['name'])}</b>: only {len(products)} products "
                            f"found this run vs {len(old)} last time. Skipping the update "
                            f"in case the scrape was blocked — will retry next run.")
                    failures.append(f"{store['name']}: suspicious item-count drop")
                    continue

                new_items = [p for p in products if p["id"] not in seen]
                current_ids = {p["id"] for p in products}
                gone = [p for p in old.values() if p["id"] not in current_ids]
                print(f"  {len(new_items)} new, {len(gone)} removed "
                      f"({len(old)} -> {len(products)})")

                failed: list[str] = []
                surge_at = min(max(SURGE_LIMIT, len(seen) // 4), 500)
                if len(new_items) > surge_at:
                    # Hundreds of listings do not appear at once. This is a markup
                    # or id-format change, so say so once instead of firing an alert
                    # for every product in the catalogue.
                    print(f"  !! {len(new_items)} 'new' listings looks like a markup "
                          f"change, not a restock; sending one warning instead")
                    tg.text(f"⚠️ <b>{esc(store['name'])}</b>: {len(new_items)} listings "
                            f"look new this run, far more than a normal restock. Not "
                            f"sending individual alerts — this usually means eBay "
                            f"changed the page markup. The ids have been recorded, so "
                            f"the next run will be quiet again.")
                    failures.append(f"{store['name']}: suspicious surge of "
                                    f"{len(new_items)} new ids")
                elif new_items:
                    failed = announce_new(tg, store, new_items, max_photos)
                    if failed:
                        print(f"  !! Telegram rejected {len(failed)} alert(s); "
                              f"they will be retried next run")
                        failures.append(f"{store['name']}: {len(failed)} alert(s) "
                                        f"not delivered")
                    total_new += len(new_items) - len(failed)

                # Only remember what actually reached Telegram, so a failed send is
                # retried next run rather than silently swallowed.
                seen |= {p["id"] for p in products} - set(failed)
                save_snapshot(slug, store, products, seen)
            except ScrapeError as exc:
                print(f"  !! FAILED: {exc}")
                failures.append(f"{label}: {exc}")
            except Exception as exc:                        # noqa: BLE001
                print(f"  !! unexpected error: {type(exc).__name__}: {exc}")
                failures.append(f"{label}: {type(exc).__name__}: {exc}")
    finally:
        fetcher.close()

    if failures:
        print("\nCompleted with problems:")
        for f in failures:
            print(f"  - {f}")
        return 1

    print(f"\nDone. {total_new} new product(s) across all stores.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
