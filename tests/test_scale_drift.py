"""
Requirement 2: production-scale simulation.

~9,000 listings over ~45 pages, ~10% shuffled between pages each run and ~300
missed entirely, run several times in a row through the REAL scrape_store,
save_snapshot/load_snapshot and main() diff.
"""
from __future__ import annotations

import pytest

from conftest import STORE_B
from ebayfakes import FakeFetcher, drifted_layout, make_catalogue, paginate

N = 9000
PER_PAGE = 200


@pytest.fixture(scope="module")
def catalogue():
    return make_catalogue(N)


def _run(hz, layout):
    hz.use_fetcher(FakeFetcher({STORE_B["url"]: layout}, store_name=STORE_B["name"]))
    return hz.run()


def test_shuffle_and_missing_items_produce_zero_false_alerts(hz, catalogue):
    hz.set_stores([STORE_B])

    # run 1 -- clean baseline
    assert _run(hz, paginate(catalogue, PER_PAGE)) == 0
    assert hz.announced_ids() == []
    snap = hz.snapshot("storeb")
    assert snap["count"] == N
    assert len(snap["seen"]) == N

    old_code_false_alerts = 0
    prev_products = {p["id"] for p in snap["products"]}

    # runs 2..6 -- 10% shuffled onto other pages, 300 missed entirely
    for seed in range(1, 6):
        layout, dropped = drifted_layout(catalogue, PER_PAGE,
                                         missing=300, dupes=900, seed=seed)
        assert len(dropped) == 300
        assert _run(hz, layout) == 0

        snap = hz.snapshot("storeb")
        cur = {p["id"] for p in snap["products"]}
        old_code_false_alerts += len(cur - prev_products)
        prev_products = cur

        assert hz.announced_ids() == [], (
            f"run seed={seed}: {len(hz.announced_ids())} false alerts")
        assert len(snap["seen"]) == N, "seen must still cover the whole catalogue"
        assert snap["count"] == N - 300

    # The pre-fix diff (products vs products) would have fired hundreds of times
    # on exactly this data. Recorded here so the regression is visible.
    print(f"\n  old products-only diff would have announced "
          f"{old_code_false_alerts} products over 5 runs")
    assert old_code_false_alerts > 200


def test_genuinely_new_listings_at_scale_announced_once(hz, catalogue):
    hz.set_stores([STORE_B])
    _run(hz, paginate(catalogue, PER_PAGE))

    fresh = make_catalogue(5, base=177700000000)
    grown = catalogue + fresh
    layout, dropped = drifted_layout(grown, PER_PAGE, missing=300, dupes=900, seed=42)
    # make sure the new ones are actually present this run
    present = {i for pg in layout for i, _ in pg}
    fresh_present = [i for i, _ in fresh if i in present]

    _run(hz, layout)
    assert sorted(hz.announced_ids()) == sorted(fresh_present)

    # next run: nothing new, even with a different shuffle
    layout2, _ = drifted_layout(grown, PER_PAGE, missing=300, dupes=900, seed=43)
    _run(hz, layout2)
    assert hz.announced_ids() == []


def test_suspicious_drop_guard_blocks_half_scrape_and_keeps_seen(hz, catalogue):
    hz.set_stores([STORE_B])
    _run(hz, paginate(catalogue, PER_PAGE))
    before = hz.snapshot("storeb")

    # a blocked/half run returns 40% of the catalogue
    half = catalogue[:3600]
    rc = _run(hz, paginate(half, PER_PAGE))
    assert rc == 1, "a suspicious drop must be reported as a run failure"
    after = hz.snapshot("storeb")
    assert after["scraped_at"] == before["scraped_at"], "snapshot must not be rewritten"
    assert len(after["seen"]) == N
    assert hz.announced_ids() == []
    assert any("only 3600 products" in t for t in hz.tg.texts())

    # the next healthy run recovers with no alerts
    rc = _run(hz, paginate(catalogue, PER_PAGE))
    assert rc == 0
    assert hz.announced_ids() == []
