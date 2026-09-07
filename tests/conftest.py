"""
Test harness for scrape.py.

Everything is offline: TieredFetcher is replaced by an in-memory FakeFetcher and
Telegram._call is replaced by a recorder, so no socket is ever opened.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import scrape  # noqa: E402
import parser as ebay_parser  # noqa: E402,F401

from ebayfakes import FakeFetcher  # noqa: E402


# --------------------------------------------------------------------------- #

class RecordingTelegram(scrape.Telegram):
    """Real Telegram class; only the HTTP call is stubbed out."""

    def __init__(self, token, chat_id, enabled=True):
        super().__init__(token, chat_id, enabled)
        self.calls = []                 # list of (method, payload)
        self.fail_methods = set()       # methods that should return False
        self.fail_once = {}             # method -> remaining failures

    def _call(self, method, payload):
        self.calls.append((method, payload))
        if self.fail_once.get(method, 0) > 0:
            self.fail_once[method] -= 1
            return False
        return method not in self.fail_methods

    # -- assertions helpers ------------------------------------------------- #
    def methods(self):
        return [m for m, _ in self.calls]

    def texts(self):
        return [p.get("text", "") for m, p in self.calls if m == "sendMessage"]

    def albums(self):
        return [json.loads(p["media"]) for m, p in self.calls if m == "sendMediaGroup"]

    def photos(self):
        return [p for m, p in self.calls if m == "sendPhoto"]

    def blob(self):
        """Everything the user would actually receive, concatenated."""
        out = []
        for m, p in self.calls:
            if m == "sendMediaGroup":
                for media in json.loads(p["media"]):
                    out.append(media.get("caption", ""))
                    out.append(media.get("media", ""))
            else:
                out.append(p.get("text") or "")
                out.append(p.get("caption") or "")
                out.append(p.get("photo") or "")
        return "\n".join(out)


class Harness:
    def __init__(self, tmp_path, monkeypatch):
        self.tmp = tmp_path
        self.mp = monkeypatch
        self.data_dir = tmp_path / "data"
        self.stores_file = tmp_path / "stores.json"
        self.tg_instances = []
        self.tg_fail_methods = set()   # methods every Telegram instance refuses
        self.announced = []      # list of (store_slug, [ids]) per announce_new call
        self.fetchers = []

        monkeypatch.setattr(scrape, "DATA_DIR", self.data_dir)
        monkeypatch.setattr(scrape, "STORES_FILE", self.stores_file)
        monkeypatch.setattr(scrape.time, "sleep", lambda *_a, **_k: None)
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "-1001")

        harness = self

        def tg_factory(token, chat_id, enabled=True):
            tg = RecordingTelegram(token, chat_id, enabled)
            tg.fail_methods |= harness.tg_fail_methods
            harness.tg_instances.append(tg)
            return tg

        monkeypatch.setattr(scrape, "Telegram", tg_factory)

        real_announce = scrape.announce_new

        def spy_announce(tg, store, new_items, max_photos):
            harness.announced.append((store["slug"], [p["id"] for p in new_items]))
            return real_announce(tg, store, new_items, max_photos)

        monkeypatch.setattr(scrape, "announce_new", spy_announce)

    # ---------------------------------------------------------------- setup #
    def set_stores(self, stores):
        self.stores_file.write_text(json.dumps(stores), encoding="utf-8")

    def set_stores_raw(self, text):
        self.stores_file.write_text(text, encoding="utf-8")

    def use_products(self, mapping):
        """Bypass HTML entirely: {slug: [product dicts]} returned by scrape_store."""
        def fake_scrape_store(fetcher, store):
            res = mapping[store["slug"]]
            if isinstance(res, Exception):
                raise res
            return sorted(res, key=lambda p: p["id"])
        self.mp.setattr(scrape, "scrape_store", fake_scrape_store)
        self.use_fetcher(FakeFetcher([[]]))

    def use_no_fetcher(self):
        """Fail loudly if anything tries to open a connection at all."""
        def nope(*_a, **_k):
            raise AssertionError("TieredFetcher was constructed -- a config error "
                                 "must be rejected before any HTTP work")
        self.mp.setattr(scrape, "TieredFetcher", nope)

    def use_fetcher(self, fetcher):
        self.fetchers.append(fetcher)
        self.mp.setattr(scrape, "TieredFetcher", lambda force_browser=False: fetcher)
        return fetcher

    # ------------------------------------------------------------------ run #
    def run(self, *argv):
        self.mp.setattr(sys, "argv", ["scrape.py", *argv])
        self.announced.clear()
        before = len(self.tg_instances)
        rc = scrape.main()
        self.tg = self.tg_instances[before] if len(self.tg_instances) > before else None
        return rc

    # ------------------------------------------------------------ inspect  #
    def snapshot(self, slug):
        return json.loads((self.data_dir / f"{slug}.json").read_text(encoding="utf-8"))

    def write_snapshot(self, slug, obj):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        text = obj if isinstance(obj, str) else json.dumps(obj, indent=2)
        (self.data_dir / f"{slug}.json").write_text(text, encoding="utf-8")

    def announced_ids(self, slug=None):
        ids = []
        for s, batch in self.announced:
            if slug is None or s == slug:
                ids.extend(batch)
        return ids


@pytest.fixture
def hz(tmp_path, monkeypatch):
    return Harness(tmp_path, monkeypatch)


def prod(iid, title=None, price="$9.99", image=None):
    return {
        "id": iid,
        "title": title or f"Product {iid}",
        "image": f"https://i.ebayimg.com/images/g/g{iid}/s-l1600.jpg" if image is None else image,
        "url": f"https://www.ebay.com/itm/{iid}",
        "price": price,
    }


STORE_A = {"slug": "storea", "name": "HerbalDirect", "url": "https://www.ebay.com/str/herbaldirect"}
STORE_B = {"slug": "storeb", "name": "VitaminRush Health Shop",
           "url": "https://www.ebay.com/str/supplementhealthshoppe"}
