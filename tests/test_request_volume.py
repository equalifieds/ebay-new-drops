"""
Requirement 7: how many HTTP GETs a single run costs, and the daily arithmetic.
"""
from __future__ import annotations

import math

import pytest

import scrape
from scrape import ITEMS_PER_PAGE, FULL_PAGE_THRESHOLD, MAX_PAGES, RETRIES_PER_PAGE
from conftest import STORE_A, STORE_B
from ebayfakes import FakeFetcher, make_catalogue, page, paginate

HERBAL = 576
VITAMIN = 9400
RUNS_PER_DAY = 12          # cron "45 */2 * * *"


@pytest.fixture(autouse=True)
def _nosleep(monkeypatch):
    monkeypatch.setattr(scrape.time, "sleep", lambda *_a, **_k: None)


def test_requests_per_run_for_both_real_stores(hz, capsys):
    hz.set_stores([STORE_A, STORE_B])
    f = hz.use_fetcher(FakeFetcher({
        STORE_A["url"]: paginate(make_catalogue(HERBAL, base=161000000000), 200),
        STORE_B["url"]: paginate(make_catalogue(VITAMIN, base=162000000000), 200),
    }))
    assert hz.run() == 0

    a = f.count_for(STORE_A["url"])
    b = f.count_for(STORE_B["url"])
    total = a + b
    assert a == 3, a                    # 200 + 200 + 176(short) -> stop
    assert b == 48, b                   # 47 full pages + 1 empty probe page
    assert total == 51

    with capsys.disabled():
        print(f"\n  HerbalDirect  {HERBAL:>5} items -> {a:>2} GETs/run")
        print(f"  VitaminRush   {VITAMIN:>5} items -> {b:>2} GETs/run")
        print(f"  total {total} GETs/run x {RUNS_PER_DAY} runs/day = "
              f"{total * RUNS_PER_DAY} GETs/day  ({total * RUNS_PER_DAY * 30} /month)")
        print(f"  worst case with RETRIES_PER_PAGE={RETRIES_PER_PAGE}: "
              f"{total * RETRIES_PER_PAGE * RUNS_PER_DAY} GETs/day")


def test_full_page_always_costs_one_extra_probe_request():
    """A catalogue that is an exact multiple of 200 always pays for one more
    page than it needs."""
    f = FakeFetcher(paginate(make_catalogue(400), 200))
    scrape.scrape_store(f, STORE_A)
    assert len(f.requests) == 3
    assert "_pgn=3" in f.requests[-1]


def test_run_wall_clock_from_the_inter_page_sleep(monkeypatch):
    slept = []
    monkeypatch.setattr(scrape.time, "sleep", lambda s: slept.append(s))
    f = FakeFetcher(paginate(make_catalogue(VITAMIN), 200))
    scrape.scrape_store(f, STORE_B)
    # one 2-4s sleep after every page that is followed by another fetch
    assert len(slept) == 47
    assert 2.0 <= min(slept) and max(slept) <= 4.0
    lo, hi = 47 * 2.0, 47 * 4.0
    assert lo < sum(slept) < hi
    print(f"\n  VitaminRush politeness sleep alone: {lo:.0f}-{hi:.0f}s per run")


def test_max_pages_caps_the_catalogue_at_16000_items():
    assert MAX_PAGES * FULL_PAGE_THRESHOLD == 16_000
    # VitaminRush at ~9,400 has ~40% headroom before the ceiling silently
    # truncates the store.
    assert VITAMIN < MAX_PAGES * FULL_PAGE_THRESHOLD



def test_offset_stride_matches_the_rendered_page_size():
    """
    Regression for the old _ipg=240 / 200-rendered mismatch: with the offset
    striding by ITEMS_PER_PAGE and the grid rendering the same number, the walk
    must collect the whole catalogue with no gaps.
    """
    items = make_catalogue(VITAMIN)

    def layout(pgn):
        off = (pgn - 1) * ITEMS_PER_PAGE
        return page(items[off:off + FULL_PAGE_THRESHOLD])

    f = FakeFetcher(layout)
    got = scrape.scrape_store(f, STORE_A)
    assert len(got) == VITAMIN, f"{VITAMIN - len(got)} listings never fetched"
    assert {p["id"] for p in got} == {i for i, _ in items}